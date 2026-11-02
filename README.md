# Amazon.eg price tracker

Checks the prices of products you choose every 10 minutes, keeps their price history, and
sends you a Telegram message only when a deal is **real**.

## Setup (once)

1. **Install Python 3.10 or newer** from https://www.python.org/downloads/
   (on Windows, tick "Add Python to PATH" during install). Nothing else is needed.
2. **Get the code**: download this folder (or `git clone` the GitHub repo) and open a
   terminal inside it.
3. **Create your config**:
   - Windows: `copy config.example.json config.json`
   - Mac/Linux: `cp config.example.json config.json`
4. **Connect Telegram**: create a bot with @BotFather (`/newbot`), open your bot and press
   **Start**, then run:

       python3 -m tracker.cli telegram

   Paste the token when asked. You should get a test message. (On Windows type `python`
   instead of `python3`.)
5. **Add products** by pasting their Amazon.eg links:

       python3 -m tracker.cli add "https://www.amazon.eg/-/en/.../dp/B0XXXXXXXX" --title "Kettle"

## Run it

    python3 -m tracker.main --once     # one check now, to see that it works
    python3 -m tracker.main            # keep checking every 10 minutes (leave the window open)
    python3 -m tracker.cli status      # current vs usual price for each product

It needs **3 days of history** before it starts judging deals, so leave it running.
The computer must stay on and awake while it runs.

## How it decides
- **Usual price (reference):** the median price over the last 90 days (or 30 days if the
  price has settled lower). Amazon's crossed-out "was" price is ignored.
- **Real deal:** 15%+ below the usual price. **Pricing error:** 50%+ below.
- **Fake discount:** a big "was" price while the price is near normal, or the price was
  pushed up in the last 14 days and the "sale" only brings it back down.
- The same product won't alert again for 24 hours unless the price drops another 3%.
- Change the thresholds in `config.json` under `settings`.

## About reading Amazon pages
This reads Amazon.eg product pages directly, which Amazon's terms of use don't allow; it's
meant for a small personal watch list. To stay gentle it reads one page at a time with
pauses, never checks more often than every 5 minutes, and if Amazon shows a robot check it
stops and waits (10 min, then 20, 40 ... up to 2 hours) instead of trying to get around it.
Keep the watch list small (roughly 20-30 products). If Amazon changes its page layout, prices
may stop being found; the tracker will print "no price on the page" and the parser in
`tracker/amazon_page.py` needs an update.

## Files
- `tracker/amazon_page.py`: reads price, "was" price, stock and title from a product page
- `tracker/analyzer.py`: usual price, real vs fake deal logic
- `tracker/db.py`: price history (SQLite file `prices.db`)
- `tracker/notify.py`: Telegram messages
- `tracker/main.py`: the every-10-minutes loop
- `tracker/cli.py`: add/list/status/telegram commands

## Tests
    python3 -m unittest
