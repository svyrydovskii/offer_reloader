"""Briefly re-activate ENDED Allegro offers and end them again, so Allegro
doesn't auto-delete them from the archive.

Only offers that ended at least --older-than days ago are reloaded (default:
ARCHIVE_DAYS minus a safety margin). A reload resets an offer's end date, so
each offer comes up again only when it's getting close to deletion again.

Usage:
    run.command / run.bat              # for everyday use: real account, asks before changing anything
    python main.py                     # sandbox, dry run: just lists offers due for reload
    python main.py --run               # sandbox, actually reload
    python main.py --env prod --run    # real account
    python main.py --older-than 0      # every ENDED offer, whatever its age
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import creds
from auth import ENVIRONMENTS, get_user_token, logout

MEDIA_TYPE = "application/vnd.allegro.public.v1+json"
BATCH_SIZE = 1000  # max offers per publication command
POLL_SECONDS = 3

# Allegro deletes ENDED offers from the archive this many days after they end.
ARCHIVE_DAYS = 30
# Reload offers this many days before they'd be deleted. Must be longer than the
# gap between runs, or offers can slip past the deadline in between.
SAFETY_MARGIN_DAYS = 10

Problem = Tuple[str, bool, str, str]  # (offer_id, urgent, step, reason)


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, text: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {text}")
        self.status = status


def status_of(offer: Optional[Dict[str, Any]]) -> Optional[str]:
    return ((offer or {}).get("publication") or {}).get("status")


def ended_at(offer: Dict[str, Any]) -> Optional[datetime]:
    raw = (offer.get("publication") or {}).get("endedAt")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class Allegro:
    def __init__(self, env: str):
        self.env = env
        self.base = ENVIRONMENTS[env]["api"]
        self.session = requests.Session()
        # Retry dropped connections, rate limits and server errors with backoff.
        # Safe for PUTs too: each command has its own UUID, so a repeat gets 409,
        # which _submit treats as success.
        retry = Retry(total=6, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=None, raise_on_status=False)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({
            "User-Agent": creds.user_agent,
            "Accept": MEDIA_TYPE,
            "Content-Type": MEDIA_TYPE,
            "Accept-Language": "en-US",
        })

    def request(self, method: str, path: str, ok_statuses: Tuple[int, ...] = (), **kwargs) -> Dict[str, Any]:
        self.session.headers["Authorization"] = f"Bearer {get_user_token(self.env)}"
        resp = self.session.request(method, self.base + path, timeout=30, **kwargs)
        if resp.status_code >= 400:
            if resp.status_code in ok_statuses:
                return {}
            raise ApiError(method, path, resp.status_code, resp.text)
        return resp.json() if resp.content else {}

    def ended_offers(self) -> Iterator[Dict[str, Any]]:
        offset, limit = 0, 1000
        while True:
            page = self.request("GET", "/sale/offers", params={
                "publication.status": "ENDED", "limit": limit, "offset": offset,
            })
            offers = page.get("offers", [])
            yield from offers
            offset += len(offers)
            if not offers or offset >= page.get("totalCount", 0):
                return

    def set_stock(self, offer_ids: List[str], quantity: int, timeout: int) -> Dict[str, List]:
        """Batch set available stock to a fixed value (Allegro requires > 0)."""
        path = self._submit("offer-quantity-change-commands", offer_ids,
                            {"modification": {"changeType": "FIXED", "value": quantity}})
        return self._wait(path, offer_ids, timeout, f"stock {quantity}",
                          lambda o: (o.get("stock") or {}).get("available") == quantity)

    def reload(self, offer_ids: List[str], timeout: int, problems: List[Problem]) -> List[str]:
        """Activate the offers, and end each one as soon as it shows up ACTIVE, so no
        offer stays live while the script waits for slower ones.
        Returns the reloaded ids. Everything else is appended to `problems`, also
        when this crashes or is interrupted, so the caller can always report it."""
        act_path = self._publication("ACTIVATE", offer_ids)
        activating = set(offer_ids)        # ACTIVATE sent, not seen ACTIVE yet
        live: Dict[str, float] = {}        # END sent at this time, not seen ENDED yet
        end_cmds: Dict[str, set] = {}      # END command path -> its offers still in `live`
        slow = set()                       # activation not confirmed in time, END sent anyway
        reloaded: List[str] = []
        deadline = time.monotonic() + timeout
        try:
            while activating or live:
                time.sleep(POLL_SECONDS)
                offers = self._offers_by_id(sorted(activating | set(live)))
                now = time.monotonic()

                # 1. End whatever went live, right away.
                to_end = [i for i in activating if status_of(offers.get(i)) == "ACTIVE"]
                if activating:
                    for oid, reason in self._task_failures(act_path).items():
                        if oid in activating and oid not in to_end:
                            activating.discard(oid)
                            problems.append((oid, False, "ACTIVATE", reason))
                    if now > deadline:
                        late = sorted(activating - set(to_end))
                        slow.update(late)
                        to_end += late
                if to_end:
                    end_cmds[self._publication("END", to_end)] = set(to_end)
                    for oid in to_end:
                        activating.discard(oid)
                        live[oid] = now
                    print(f"  {len(to_end)} went live, END sent")

                # 2. Confirm they really ended.
                for oid in [i for i in live if status_of(offers.get(i)) == "ENDED"]:
                    del live[oid]
                    if oid in slow:
                        # It may still be mid-activation and go live after our END, so a human checks.
                        problems.append((oid, True, "ACTIVATE",
                                         f"activation not confirmed within {timeout}s, END sent anyway. "
                                         "Check in a few minutes that the offer is ENDED!"))
                    else:
                        reloaded.append(oid)
                for path, ids in list(end_cmds.items()):
                    ids.intersection_update(live)
                    if not ids:
                        del end_cmds[path]
                        continue
                    for oid, reason in self._task_failures(path).items():
                        if oid in ids:
                            ids.discard(oid)
                            del live[oid]
                            problems.append((oid, True, "END", f"{reason}. MAY STILL BE ACTIVE, end it manually!"))
                for oid in [i for i, sent in live.items() if now - sent > timeout]:
                    del live[oid]
                    problems.append((oid, True, "END", f"not confirmed ENDED within {timeout}s. "
                                                       "MAY STILL BE ACTIVE, end it manually!"))
        except BaseException:
            at_risk = sorted(activating | set(live))
            if at_risk:
                print(f"\nStopped mid-way: sending END to {len(at_risk)} offer(s) that may be live...")
                try:
                    self._publication("END", at_risk)
                    note = "script stopped mid-way, END sent but not confirmed"
                except Exception as e:
                    note = f"script stopped mid-way and END could not be sent ({e})"
                problems.extend((i, True, "END", f"{note}. Check the offer is ENDED!") for i in at_risk)
            raise
        return reloaded

    def _publication(self, action: str, offer_ids: List[str]) -> str:
        return self._submit("offer-publication-commands", offer_ids, {"publication": {"action": action}})

    def _submit(self, kind: str, offer_ids: List[str], body: Dict[str, Any]) -> str:
        path = f"/sale/{kind}/{uuid.uuid4()}"
        # 409 means a command with this fresh UUID already exists: an earlier attempt
        # got through, its response was lost, and the automatic retry hit the duplicate.
        self.request("PUT", path, ok_statuses=(409,), json={
            **body,
            "offerCriteria": [{"type": "CONTAINS_OFFERS", "offers": [{"id": i} for i in offer_ids]}],
        })
        return path

    def _offers_by_id(self, offer_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        found = {}
        for batch in chunks(offer_ids, 100):
            page = self.request("GET", "/sale/offers", params={"offer.id": batch, "limit": 1000})
            found.update({o["id"]: o for o in page.get("offers", [])})
        return found

    def _task_failures(self, path: str) -> Dict[str, str]:
        failed, offset = {}, 0
        while True:
            tasks = self.request("GET", f"{path}/tasks",
                                 params={"limit": 1000, "offset": offset}).get("tasks", [])
            for t in tasks:
                if t.get("status") == "FAIL":
                    errs = "; ".join(e.get("userMessage") or e.get("message") or "" for e in t.get("errors") or [])
                    failed[t["offer"]["id"]] = errs or t.get("message") or "unknown error"
            if len(tasks) < 1000:
                return failed
            offset += 1000

    def _wait(self, path: str, offer_ids: List[str], timeout: int, goal: str,
              is_done: Callable[[Dict[str, Any]], bool]) -> Dict[str, List]:
        """Poll the offers themselves until each one is done or its task reports FAIL.
        (The command report's completedAt/taskCount can't be trusted, at least in the sandbox.)
        Returns {"ok": [ids], "failed": [(id, reason)]}."""
        pending, ok, failed = set(offer_ids), [], []
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(POLL_SECONDS)
            offers = self._offers_by_id(sorted(pending))
            for oid in [i for i in pending if i in offers and is_done(offers[i])]:
                ok.append(oid)
                pending.discard(oid)
            for oid, reason in self._task_failures(path).items():
                if oid in pending:
                    failed.append((oid, reason))
                    pending.discard(oid)
            if not pending:
                break
            if time.monotonic() > deadline:
                failed += [(oid, f"did not reach {goal} within {timeout}s") for oid in sorted(pending)]
                break
        return {"ok": ok, "failed": failed}


def chunks(items: List[str], size: int) -> Iterator[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


OFFER_URLS = {
    "sandbox": "https://allegro.pl.allegrosandbox.pl/oferta/{}",
    "prod": "https://allegro.pl/oferta/{}",
}


def write_manual_report(env: str, problems: List[Problem], names: Dict[str, str]) -> Path:
    """CSV of offers that need a human. Urgent ones (possibly still live) first.
    utf-8-sig so Excel shows Polish characters correctly."""
    path = Path(__file__).with_name(f"manual_offers_{env}_{date.today().isoformat()}.csv")
    problems = sorted(problems, key=lambda p: not p[1])
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")  # ';' is what Excel expects in Polish locale
        w.writerow(["urgent", "offer_id", "title", "step", "reason", "link"])
        for oid, urgent, step, reason in problems:
            w.writerow(["YES" if urgent else "", oid, names.get(oid, ""), step, reason,
                        OFFER_URLS[env].format(oid)])
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", choices=ENVIRONMENTS, default="sandbox")
    p.add_argument("--run", action="store_true", help="Actually reload offers (default is a dry run).")
    p.add_argument("--older-than", type=int, default=ARCHIVE_DAYS - SAFETY_MARGIN_DAYS, metavar="DAYS",
                   help="Only reload offers that ended at least DAYS ago (default: %(default)s). "
                        "Offers with no end date are always included. 0 = all ENDED offers.")
    p.add_argument("--limit", type=int, default=None, help="Process at most N offers, oldest first (handy for testing).")
    p.add_argument("--timeout", type=int, default=900, help="Seconds to wait for each offer to change status.")
    p.add_argument("--ask", action="store_true", help="After listing, ask whether to reload (used by the launchers).")
    p.add_argument("--logout", action="store_true", help="Forget saved tokens and log in again.")
    args = p.parse_args()

    if args.logout:
        logout(args.env)

    api = Allegro(args.env)
    all_ended = list(api.ended_offers())

    now = datetime.now(timezone.utc)
    age: Dict[str, Optional[float]] = {}
    for o in all_ended:
        e = ended_at(o)
        age[o["id"]] = None if e is None else (now - e).total_seconds() / 86400
    due = [o for o in all_ended if age[o["id"]] is None or age[o["id"]] >= args.older_than]
    due.sort(key=lambda o: -(age[o["id"]] if age[o["id"]] is not None else float("inf")))  # oldest first
    due_ids = {o["id"] for o in due}
    not_due = [age[o["id"]] for o in all_ended if o["id"] not in due_ids]
    if args.limit:
        due = due[:args.limit]

    print(f"[{args.env}] {len(all_ended)} ENDED offer(s), {len(due)} due for reload "
          f"(ended {args.older_than}+ days ago; Allegro deletes them after {ARCHIVE_DAYS}).")
    for o in due:
        a = age[o["id"]]
        when = "end date unknown" if a is None else f"ended {int(a)}d ago"
        stock = (o.get("stock") or {}).get("available")
        print(f"  {o['id']}  {when:>16}  stock={stock}  {o.get('name', '')}")
    if not_due:
        print(f"Next offer becomes due in {max(0, args.older_than - max(not_due)):.0f} day(s).")

    if not due:
        print("Nothing to do.")
        return 0

    if not args.run and args.ask:
        answer = input(f"\nReload these {len(due)} offer(s) now? Type 'yes' to continue: ")
        args.run = answer.strip().lower() in ("yes", "y")

    if not args.run:
        print("\nNothing was changed." + ("" if args.ask else " Add --run to reload these offers."))
        return 0

    names = {o["id"]: o.get("name", "") for o in due}
    stock = {o["id"]: (o.get("stock") or {}).get("available") for o in due}
    problems: List[Problem] = []
    reloaded = 0
    try:
        for batch in chunks([o["id"] for o in due], BATCH_SIZE):
            # Allegro refuses to activate offers with no stock. Set those to 1 first.
            # The stock stays at 1 after the offer is ended again (the API can't set it to 0),
            # which is harmless: an ended offer can't be bought.
            no_stock = [i for i in batch if not stock[i]]
            if no_stock:
                print(f"\nSetting stock to 1 on {len(no_stock)} sold-out offer(s)...")
                res = api.set_stock(no_stock, 1, args.timeout)
                problems += [(i, False, "SET STOCK", r) for i, r in res["failed"]]
                bad = {i for i, _ in res["failed"]}
                batch = [i for i in batch if i not in bad]
            if not batch:
                continue
            print(f"\nReloading {len(batch)} offer(s)...")
            done = api.reload(batch, args.timeout, problems)
            reloaded += len(done)
            print(f"Reloaded {len(done)} offer(s).")
    finally:
        # Runs on crashes and Ctrl+C too, so offers that may be live are always reported.
        print(f"\nReloaded {reloaded} of {len(due)} offer(s) in total.")
        if problems:
            print(f"\n{len(problems)} offer(s) need manual attention:")
            for oid, urgent, step, reason in sorted(problems, key=lambda p: not p[1]):
                print(f"  {'!! ' if urgent else ''}{oid} [{step}] {reason}")
            report = write_manual_report(args.env, problems, names)
            print(f"\nList saved to: {report}")

    if problems:
        return 1
    print("\nAll done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
