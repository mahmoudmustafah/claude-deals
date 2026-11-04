"""Run the tracker: check prices every N minutes, analyze, alert.

    python -m tracker.main            # runs forever
    python -m tracker.main --once     # one check, then exit
"""
import argparse
import json
import os
import time

from .analyzer import DAY, Settings, analyze
from .db import PriceDB
from .notify import ConsoleNotifier, TelegramNotifier, format_alert
from .sources import DemoSource

REALERT_HOURS = 24  # don't repeat an alert for the same product unless the price falls further


def load_config(path="config.json"):
    with open(path) as f:
        return json.load(f)


def make_source(cfg):
    kind = cfg.get("source", "demo")
    if kind == "demo":
        return DemoSource({p["asin"]: p.get("demo_base", 1000.0) for p in cfg["products"]})
    if kind == "amazon_page":
        from .amazon_page import AmazonPageSource
        return AmazonPageSource(cfg.get("domain", "www.amazon.eg"))
    if kind == "creators_api":
        from .creators_api import CreatorsApiSource
        return CreatorsApiSource(cfg)
    raise SystemExit(f"Unknown source '{kind}'. Use 'creators_api', 'amazon_page' or 'demo'.")


def make_notifier(cfg):
    tg = cfg.get("telegram") or {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id")
    return TelegramNotifier(token, chat) if token and chat else ConsoleNotifier()


def should_alert(db, asin, v, now):
    last = db.last_alert(asin)
    if not last:
        return True
    return v.price < last["price"] * 0.97 or now - last["ts"] > REALERT_HOURS * 3600


def check_once(db, source, notifier, settings, currency="EGP", now=None):
    now = now or time.time()
    products = {p["asin"]: p for p in db.products()}
    quotes = source.get_prices(list(products))
    for asin, q in quotes.items():
        if q.title or q.url:
            db.add_product(asin, q.title, q.url)
            products[asin].update({k: v for k, v in (("title", q.title), ("url", q.url)) if v})
        if q.in_stock:
            v = analyze(db.history(asin, now - 90 * DAY), now, q.price, q.list_price, settings)
            if v.is_alert and should_alert(db, asin, v, now):
                notifier.send(format_alert(products[asin], v, currency))
                db.record_alert(asin, q.price, v.kind, now)
        db.record_price(asin, q.price, q.list_price, q.in_stock, q.seller, now)


def run_loop(cfg, once=False):
    db = PriceDB(cfg.get("db_path", "prices.db"))
    for p in cfg["products"]:
        db.add_product(p["asin"], p.get("title"), p.get("url"))
    source, notifier = make_source(cfg), make_notifier(cfg)
    settings = Settings(**cfg.get("settings", {}))
    interval = max(cfg.get("interval_minutes", 10), 5) * 60  # never faster than every 5 min
    while True:
        try:
            check_once(db, source, notifier, settings, cfg.get("currency", "EGP"))
            print(time.strftime("%H:%M:%S"), f"checked {len(db.products())} products")
        except Exception as e:  # keep running if one check fails
            print("check failed:", e)
        if once:
            break
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    run_loop(load_config(args.config), args.once)


if __name__ == "__main__":
    main()
