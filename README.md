# Offer Reloader

Allegro deletes ended offers from the archive after a while. This tool keeps them
alive by briefly switching each ENDED offer on and immediately off again, the same
thing you would do by hand, but for all offers at once.

## One-time setup

1. Install Python 3 from https://www.python.org/downloads/
   - **Windows:** on the first installer screen, tick **"Add python.exe to PATH"**.
2. Put this folder somewhere permanent, e.g. Documents. Keep it private:
   it contains the app key and, after login, access to your Allegro account.

## Running it

- **Mac:** double-click `run.command`
  (first time only: right-click → Open → Open, because macOS blocks unknown files).
- **Windows:** double-click `run.bat`.

What happens:

1. **First run only:** it sets itself up (about a minute), then opens an Allegro page in
   your browser. Log in and approve **Offer Reloader**. You won't need to do this
   again as long as you run the tool at least once every 3 months.
2. It lists the ENDED offers that are **due for reload**: those that ended 20 or more
   days ago, so they'd be deleted within the next 10 days. Offers that ended more
   recently are left alone. A reload resets an offer's end date, so each offer comes
   up again about every 20 days. **Nothing changes yet.**
3. It asks `Reload these N offer(s) now?`. Type `yes` and press Enter to go ahead.
   Anything else cancels.
4. It waits until Allegro confirms each step. This usually takes a minute or two,
   but can take longer for many offers. Leave the window open until it says it's done.

## Offers that need manual attention

Some offers can't be renewed automatically, for example because Allegro introduced
new rules since the offer was created. These are skipped, and a file named
`manual_offers_prod_<date>.csv` is saved in this folder. Open it in Excel.
Each row shows the offer title, why it failed, and a link to the offer.

- Rows marked **urgent = YES** come first. These offers **may still be live (buyable)**.
  Open them and end them by hand right away.
- The other rows are still ended and safe. Fix them in Allegro when you have time,
  e.g. fill in the missing field it mentions.

## Good to know

- Sold-out offers get their stock set to **1** so Allegro allows switching them on.
  They stay at 1 after being ended again. When new stock arrives, set the real number.
- Each offer is ended again as soon as Allegro shows it as live, usually within
  seconds. During that time someone could, in theory, buy it.
- If the window is closed or the computer sleeps mid-way, the tool tries to end any
  offers that might be live and lists them as urgent in the CSV.
- **Run it once a week.** Offers become due 10 days before Allegro deletes them, so
  weekly runs always catch them in time. If you skip runs for longer than that,
  offers can be deleted.
- To log in with a different account, run in a terminal: `python main.py --env prod --logout --ask`
- To reload every ENDED offer regardless of age: `python main.py --env prod --older-than 0 --ask`
