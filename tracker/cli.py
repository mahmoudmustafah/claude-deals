"""Helper commands.

    python3 -m tracker.cli add "https://www.amazon.eg/dp/B0XXXXXXXX" [--title "My kettle"]
    python3 -m tracker.cli list
    python3 -m tracker.cli status
    python3 -m tracker.cli telegram       # connect your Telegram bot and send a test
"""
import argparse
import json
import re
import time
from urllib.parse import urlparse

from .analyzer import DAY, Settings, analyze
from .db import PriceDB

ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)", re.I)


def extract_asin(text):
    """Accepts a full Amazon link or a bare 10-character ASIN."""
    text = text.strip()
    if re.fullmatch(r"[A-Z0-9]{10}", text, re.I):
        return text.upper()
    m = ASIN_RE.search(urlparse(text).path + "/")
    if not m:
        raise ValueError(f"Couldn't find a product code (ASIN) in: {text}")
    return m.group(1).upper()


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, cfg):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def cmd_add(cfg_path, link, title=None):
    cfg = load(cfg_path)
    asin = extract_asin(link)
    if any(p["asin"] == asin for p in cfg["products"]):
        print(f"{asin} is already on your watch list.")
        return asin
    host = urlparse(link).netloc or "www.amazon.eg"
    cfg["products"].append({"asin": asin, "title": title, "url": f"https://{host}/dp/{asin}"})
    save(cfg_path, cfg)
    print(f"Added {asin}{' (' + title + ')' if title else ''}. Watching {len(cfg['products'])} products.")
    return asin


def cmd_list(cfg_path):
    for p in load(cfg_path)["products"]:
        print(f"{p['asin']}  {p.get('title') or ''}")


def cmd_status(cfg_path):
    cfg = load(cfg_path)
    db = PriceDB(cfg.get("db_path", "prices.db"))
    cur = cfg.get("currency", "EGP")
    now = time.time()
    for p in cfg["products"]:
        hist = db.history(p["asin"], now - 90 * DAY)
        name = p.get("title") or p["asin"]
        if not hist:
            print(f"- {name}: no prices yet")
            continue
        last = hist[-1]
        v = analyze(hist[:-1], last["ts"], last["price"], last["list_price"],
                    Settings(**cfg.get("settings", {})))
        line = f"- {name}: now {last['price']:,.2f} {cur}"
        if v.reference:
            line += f" | usual {v.reference:,.2f} | 90d {v.low_90:,.2f}-{v.high_90:,.2f}"
        tags = [t for t in v.tags if t != "LEARNING"] or (["learning"] if "LEARNING" in v.tags else ["normal"])
        print(line + " | " + ", ".join(tags))


def cmd_telegram(cfg_path):
    """Finds your chat id from the bot's messages, saves it, and sends a test alert."""
    import urllib.request
    from .notify import TelegramNotifier
    cfg = load(cfg_path)
    tg = cfg.setdefault("telegram", {})
    token = tg.get("bot_token") or input("Paste your bot token from @BotFather: ").strip()
    from .amazon_page import SSL_CONTEXT
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15,
                                context=SSL_CONTEXT) as r:
        updates = json.load(r).get("result", [])
    chats = [u["message"]["chat"]["id"] for u in updates if "message" in u]
    if not chats:
        print("No messages found. Open your bot in Telegram, press Start (or send 'hi'), then run this again.")
        return
    tg["bot_token"], tg["chat_id"] = token, str(chats[-1])
    save(cfg_path, cfg)
    TelegramNotifier(token, tg["chat_id"]).send("✅ Price tracker connected. Deals will arrive here.")
    print("Connected! Check Telegram for the test message.")


def main():
    ap = argparse.ArgumentParser(description="Manage your watch list")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add a product by Amazon link or ASIN")
    a.add_argument("link")
    a.add_argument("--title")
    sub.add_parser("list", help="show your watch list")
    sub.add_parser("status", help="show current vs usual price for each product")
    sub.add_parser("telegram", help="connect your Telegram bot and send a test message")
    args = ap.parse_args()
    if args.cmd == "add":
        cmd_add(args.config, args.link, args.title)
    elif args.cmd == "list":
        cmd_list(args.config)
    elif args.cmd == "telegram":
        cmd_telegram(args.config)
    else:
        cmd_status(args.config)


if __name__ == "__main__":
    main()
