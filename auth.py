"""Allegro OAuth for a seller account, using the device code flow.

First run: prints a link. Open it, log in to the seller account, and approve
the app. Tokens are then saved to tokens.json. Later runs reuse the access
token (valid for 12h) or refresh it silently. The refresh token is valid for
3 months and is renewed on every refresh, so running the script at least once
every few months means nobody has to log in again.
"""
from __future__ import annotations

import json
import os
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict

import requests

import creds

ENVIRONMENTS = {
    "sandbox": {
        "auth": "https://allegro.pl.allegrosandbox.pl/auth/oauth",
        "api": "https://api.allegro.pl.allegrosandbox.pl",
    },
    "prod": {
        "auth": "https://allegro.pl/auth/oauth",
        "api": "https://api.allegro.pl",
    },
}

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class AuthError(RuntimeError):
    pass


def _token_file(env: str) -> Path:
    return Path(__file__).with_name(f"tokens.{env}.json")


def _save(env: str, tokens: Dict[str, Any]) -> None:
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 0)) - 60
    path = _token_file(env)
    path.write_text(json.dumps(tokens, indent=2))
    os.chmod(path, 0o600)  # these tokens give full access to the account


def _load(env: str) -> Dict[str, Any] | None:
    path = _token_file(env)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _post_token(env: str, params: Dict[str, str]) -> requests.Response:
    return requests.post(
        f"{ENVIRONMENTS[env]['auth']}/token",
        data=params,
        auth=(creds.client_id, creds.client_secret),
        headers={"User-Agent": creds.user_agent},
        timeout=30,
    )


def _device_login(env: str) -> Dict[str, Any]:
    resp = requests.post(
        f"{ENVIRONMENTS[env]['auth']}/device",
        data={"client_id": creds.client_id},
        auth=(creds.client_id, creds.client_secret),
        headers={"User-Agent": creds.user_agent},
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(
            f"Device authorization failed ({resp.status_code}): {resp.text}\n"
            "Check that the app is registered with the 'device' option enabled."
        )
    device = resp.json()

    print("\n=== Allegro login required ===")
    print(f"Open this link and approve access:\n  {device['verification_uri_complete']}")
    print(f"(or go to {device['verification_uri']} and enter code {device['user_code']})\n")
    print("Waiting for approval...")
    webbrowser.open(device["verification_uri_complete"])

    interval = int(device.get("interval", 5))
    deadline = time.time() + int(device.get("expires_in", 3600))
    while time.time() < deadline:
        time.sleep(interval)
        r = _post_token(env, {"grant_type": DEVICE_GRANT, "device_code": device["device_code"]})
        if r.status_code == 200:
            print("Login successful.\n")
            return r.json()
        error = r.json().get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise AuthError(f"Login failed: {r.text}")
    raise AuthError("Login link expired before it was approved. Run the script again.")


def _refresh(env: str, refresh_token: str) -> Dict[str, Any] | None:
    r = _post_token(env, {"grant_type": "refresh_token", "refresh_token": refresh_token})
    if r.status_code == 200:
        return r.json()
    return None  # refresh token expired or revoked, so log in again


def get_user_token(env: str) -> str:
    tokens = _load(env)
    if tokens and tokens.get("expires_at", 0) > time.time():
        return tokens["access_token"]
    if tokens and tokens.get("refresh_token"):
        refreshed = _refresh(env, tokens["refresh_token"])
        if refreshed:
            _save(env, refreshed)
            return refreshed["access_token"]
    tokens = _device_login(env)
    _save(env, tokens)
    return tokens["access_token"]


def logout(env: str) -> None:
    _token_file(env).unlink(missing_ok=True)
