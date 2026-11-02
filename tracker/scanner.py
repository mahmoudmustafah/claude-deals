"""Finds discounts across Amazon.eg by itself, without a watch list.

Every scan it reads the deals page and the search results for a list of popular search
terms, saves the price of every product it sees (so price history builds up for thousands
of products), and keeps track of which products are on a deal right now.

    python3 -m tracker.scanner --once      # one scan, then exit
    python3 -m tracker.scanner             # scan every 60 minutes

Usually you don't run this directly: `python3 -m tracker.dashboard` runs it for you.
"""
import argparse
import html
import json
import os
import random
import re
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote_plus

from .amazon_page import SHARED, Blocked, _num
from .analyzer import DAY, Settings, analyze

# Kept short on purpose: Amazon refuses scans that read many pages in a row.
DEFAULT_QUERIES = ["mobile phones", "laptops", "headphones", "air fryer"]
DEFAULT_SCAN_MINUTES = 60
MIN_SCAN_MINUTES = 20


@dataclass
class Card:
    asin: str
    title: str | None
    price: float
    list_price: float | None = None      # Amazon's "List price" strike-through
    typical_price: float | None = None   # Amazon's "Typical price" / "Was" label
    badge: str | None = None
    ends_at: float | None = None
    claimed: float | None = None         # Lightning Deal: share of stock already claimed
    image: str | None = None
    url: str | None = None

    @property
    def claimed_discount(self):
        ref = self.list_price or self.typical_price
        return 1 - self.price / ref if ref and ref > self.price else 0.0

    @property
    def is_deal(self):
        return self.claimed_discount >= 0.03 or bool(self.badge) or bool(self.ends_at)


# ---------- reading listing pages ----------

# Search results mark cards with data-asin; the carousels on the deals page use
# data-csa-c-item-type="asin" data-csa-c-item-id="amzn1.asin.XXXXXXXXXX" instead.
ASIN_ATTR = re.compile(r'data-asin="([A-Z0-9]{10})"'
                       r'|data-csa-c-item-type="asin" data-csa-c-item-id="amzn1\.asin\.([A-Z0-9]{10})"')
DEAL_WORDS = ("lightning deal", "limited time deal", "deal of the day", "prime day deal",
              "prime exclusive deal", "top deal", "with prime", "عرض لفترة محدودة", "عرض البرق")


def _clean(text):
    return html.unescape(re.sub(r"<[^>]+>|\s+", " ", text)).strip()


def parse_ends_in(text, now):
    """'Ends in 02:15:33' / 'Ends in 2h 15m' / 'Ends in 3 days' -> unix time, else None."""
    t = _clean(text).lower()
    m = re.search(r"ends in\s*(\d{1,2}):(\d{2}):(\d{2})", t)
    if m:
        h, mi, s = map(int, m.groups())
        return now + h * 3600 + mi * 60 + s
    m = re.search(r"ends in\s*((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m)\b\s*)+)", t)
    if m:
        total = 0
        for n, unit in re.findall(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m)\b", m.group(1)):
            total += int(n) * (86400 if unit.startswith("d") else 3600 if unit.startswith("h") else 60)
        return now + total if total else None
    return None


def _segments(page):
    """Split a listing page into one chunk of HTML per product card."""
    hits = [(m.start(), m.group(1) or m.group(2)) for m in ASIN_ATTR.finditer(page)]
    for i, (pos, asin) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(page)
        end = min(end, pos + 20000)
        script = page.find("<script", pos, end)  # don't read a neighbour's text out of page scripts
        yield asin, page[pos:script if script >= 0 else end]


def parse_card(asin, seg, now, domain="www.amazon.eg"):
    price = strike = None
    strike_label = ""
    for m in re.finditer(r'<span class="a-price(?P<cls>[^"]*)"(?P<attrs>[^>]*)>\s*'
                         r'<span class="a-offscreen">(?P<val>[^<]+)</span>', seg):
        is_strike = "a-text-price" in m.group("cls") or 'data-a-strike="true"' in m.group("attrs")
        if is_strike and strike is None:
            strike = _num(m.group("val"))
            strike_label = _clean(seg[max(0, m.start() - 300):m.start()]).lower()[-40:]
        elif not is_strike and price is None:
            price = _num(m.group("val"))
    if not price:
        return None
    title = None
    m = (re.search(r'<h2[^>]*aria-label="([^"]+)"', seg) or re.search(r"<h2[^>]*>(.*?)</h2>", seg, re.S)
         or re.search(r'class="[^"]*dcl-product-title[^"]*"[^>]*>(.*?)</span>\s*</span>', seg, re.S))
    if m:
        title = _clean(m.group(1))
    if not title:
        m = re.search(r'<img[^>]+alt="([^"]{8,})"', seg)
        title = _clean(m.group(1)) if m else None
    img = re.search(r'<img[^>]+src="(https://m\.media-amazon\.com/images/I/[^"]+)"', seg)
    text = _clean(seg).lower()
    badge = None
    m = re.search(r'class="a-badge-text"[^>]*>([^<]+)<', seg)
    if m and any(w in m.group(1).lower() for w in ("deal", "عرض")):
        badge = _clean(m.group(1))
    if not badge:
        badge = next((w.title() for w in DEAL_WORDS if w in text and w != "with prime"), None)
    claimed = re.search(r"(\d{1,3})%\s*claimed", text)
    card = Card(asin=asin, title=title, price=price, badge=badge,
                ends_at=parse_ends_in(text, now),
                claimed=int(claimed.group(1)) / 100 if claimed else None,
                image=img.group(1) if img else None, url=f"https://{domain}/dp/{asin}")
    if strike and strike > price:
        _set_reference(card, strike, strike_label)
    return card


def _set_reference(card, strike, label):
    """'Typical price:' / 'Was:' is Amazon's own recent price; 'List:' is the RRP."""
    words = re.findall(r"\b(typical|was|list)\b", label.lower())
    if words and words[-1] in ("typical", "was"):
        card.typical_price = strike
    else:
        card.list_price = strike


def _iso_time(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def parse_deals_json(page, now, domain="www.amazon.eg"):
    """The main grid on /deals is drawn by JavaScript, but its first products come in the
    page as JSON ("productSearchResponse"), with prices, badges and countdown end times."""
    cards = []
    decoder = json.JSONDecoder()
    for m in re.finditer(r'"productSearchResponse"\s*:\s*', page):
        try:
            data, _ = decoder.raw_decode(page, m.end())
        except ValueError:
            continue
        for p in data.get("products") or []:
            asin = p.get("asin")
            prices = p.get("price") or {}
            price = _num(str((prices.get("priceToPay") or {}).get("price", "")))
            if not asin or not price:
                continue
            frags = (((p.get("dealBadge") or {}).get("messaging") or {}).get("content") or {}).get("fragments") or []
            text = " ".join(f.get("text", "") for f in frags).strip()
            ends_at = next((_iso_time(f["countdownTimer"].get("targetTime"))
                            for f in frags if isinstance(f.get("countdownTimer"), dict)), None)
            badge = None
            if p.get("dealBadge") or p.get("dealDetails"):
                badge = text if text and not text.lower().startswith("ends in") else "Limited time deal"
            if (p.get("addToCart") or {}).get("isLightningDeal"):
                badge = "Lightning deal"
            img = ((p.get("image") or {}).get("lowRes") or {})
            card = Card(asin=asin, title=html.unescape(p.get("title") or "") or None, price=price,
                        badge=badge, ends_at=ends_at if ends_at and ends_at > now else None,
                        image=f"{img['baseUrl']}._AC_UL320_.{img.get('extension', 'jpg')}" if img.get("baseUrl") else None,
                        url=f"https://{domain}/dp/{asin}")
            basis = prices.get("basisPrice") or {}
            strike = _num(str(basis.get("price", "")))
            if strike and strike > price:
                _set_reference(card, strike, basis.get("label", ""))
            cards.append(card)
    return cards


def parse_listing(page, now, domain="www.amazon.eg"):
    cards = {c.asin: c for c in parse_deals_json(page, now, domain)}
    for asin, seg in _segments(page):
        if asin in cards:
            continue
        c = parse_card(asin, seg, now, domain)
        if c:
            cards[asin] = c
    return list(cards.values())


# ---------- judging a deal ----------

def judge(db, deal, settings=Settings(), now=None):
    """Compare a deal's price with what the product cost *before* the deal started."""
    now = now or time.time()
    before = [p for p in db.history(deal["asin"], deal["first_seen"] - 90 * DAY)
              if p["ts"] < deal["first_seen"]]
    claimed_ref = deal.get("list_price") or deal.get("typical_price")
    v = analyze(before, deal["first_seen"], deal["price"], claimed_ref, settings)
    tags = set(v.tags)
    if "LEARNING" in tags:
        verdict = "unverified"
    elif "PRICE_ERROR" in tags:
        verdict = "error"
    elif "REAL_DEAL" in tags:
        verdict = "real"
    elif tags & {"FAKE_LIST_PRICE", "INFLATED_BEFORE_SALE"} or (v.real_discount or 0) < 0.05:
        verdict = "fake"
    else:
        verdict = "small"
    notes = [n for n in v.notes if not n.startswith("Only ")]
    if verdict == "unverified":
        days = (deal["first_seen"] - before[0]["ts"]) / DAY if before else 0
        notes.insert(0, f"Not enough price history before this deal yet ({days:.1f} of "
                        f"{settings.min_history_days:g} days needed).")
        if deal.get("typical_price"):
            notes.append(f"Amazon says it typically sells for {deal['typical_price']:,.0f} "
                         "(Amazon's own label, not checked by us yet).")
    return {
        "verdict": verdict, "reference": v.reference, "real_discount": v.real_discount,
        "claimed_discount": (1 - deal["price"] / claimed_ref) if claimed_ref and claimed_ref > deal["price"] else None,
        "low_90": v.low_90, "high_90": v.high_90, "notes": notes,
    }


# ---------- scanning ----------

def scan_sources(cfg):
    domain = cfg.get("domain", "www.amazon.eg")
    scan = cfg.get("scan", {})
    sources = []
    if scan.get("deals_page", True):
        sources.append(("Today's deals", f"https://{domain}/-/en/deals"))
    for q in scan.get("queries", DEFAULT_QUERIES):
        for page in range(1, int(scan.get("pages_per_query", 1)) + 1):
            sources.append((q, f"https://{domain}/-/en/s?k={quote_plus(q)}&page={page}"))
    return sources


def scan_once(db, cfg, fetcher=SHARED, notifier=None, settings=None, now=None, pause=(15, 30)):
    from .main import should_alert
    from .notify import format_alert

    settings = settings or Settings(**cfg.get("settings", {}))
    domain = cfg.get("domain", "www.amazon.eg")
    interval = max(cfg.get("scan", {}).get("interval_minutes", DEFAULT_SCAN_MINUTES), MIN_SCAN_MINUTES) * 60
    stats = {"pages": 0, "products": 0, "deals": 0, "empty_pages": [], "stopped": None}
    sources = scan_sources(cfg)
    for i, (label, url) in enumerate(sources):
        if i:
            fetcher.sleep(random.uniform(*pause))
        t = now or time.time()
        try:
            page = fetcher.get(url)
        except Blocked as e:
            stats["stopped"] = str(e)
            break
        except urllib.error.HTTPError as e:
            print(f"{label}: page not available ({e.code})")
            continue
        except Exception as e:
            print(f"{label}: couldn't read page ({e})")
            continue
        cards = _record_page(db, label, page, t, domain, stats)
        if cards and notifier:
            _alert_new_deals(db, [c for c in cards if c.is_deal], notifier, settings, cfg, t)
    db.end_stale_deals(now or time.time(), stale_after=max(3 * interval, 3 * 3600))
    db.set_meta("last_scan", now or time.time())
    db.set_meta("last_scan_stats", f"{stats['pages']}/{len(sources)} pages"
                + (f", stopped: {stats['stopped']}" if stats["stopped"] else ""))
    if stats["stopped"] is None:
        fetcher.ok()
    return stats


def _record_page(db, label, page, t, domain, stats):
    stats["pages"] += 1
    cards = parse_listing(page, t, domain)
    if not cards:
        stats["empty_pages"].append(label)
        _save_debug(label, page)
        return []
    for c in cards:
        db.seen_product(c.asin, c.title, c.url, c.image, label)
        db.record_price(c.asin, c.price, c.list_price or c.typical_price, ts=t, commit=False)
        stats["products"] += 1
        if c.is_deal:
            stats["deals"] += 1
            db.upsert_deal(c.asin, t, c.price, c.list_price, c.typical_price, c.badge,
                           c.ends_at, c.claimed, label)
        else:
            db.end_deal(c.asin, t)  # seen again without a discount: the deal is over
    db.commit()
    return cards


def import_files(db, cfg, paths):
    """Read Amazon pages you saved from your own browser (File > Save As, "Page Source"),
    for when Amazon refuses the tracker's own requests. Uses each file's save time."""
    domain = cfg.get("domain", "www.amazon.eg")
    stats = {"pages": 0, "products": 0, "deals": 0, "empty_pages": [], "stopped": None}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            page = f.read()
        label = "Today's deals" if "discount-asin-grid" in page else os.path.basename(path)
        m = re.search(r"<title>\s*Amazon\.eg\s*:\s*([^<]+)</title>", page)
        if m and label != "Today's deals":
            label = _clean(m.group(1))
        _record_page(db, label, page, os.path.getmtime(path), domain, stats)
    return stats


def _alert_new_deals(db, cards, notifier, settings, cfg, now):
    for c in cards:
        deal = db.deal(c.asin)
        if not deal or deal["ended_at"] is not None:
            continue
        j = judge(db, deal, settings, now)
        # only the best ones go to Telegram; everything is on the dashboard
        min_pct = cfg.get("scan", {}).get("alert_min_discount", 0.25)
        if j["verdict"] != "error" and not (j["verdict"] == "real" and j["real_discount"] >= min_pct):
            continue
        hist = db.history(c.asin, now - 90 * DAY)
        v = analyze([p for p in hist if p["ts"] < deal["first_seen"]], now, c.price,
                    c.list_price or c.typical_price, settings)
        if v.is_alert and should_alert(db, c.asin, v, now):
            product = db.product(c.asin) or {"asin": c.asin}
            text = format_alert(product, v, cfg.get("currency", "EGP"))
            if c.ends_at:
                text += f"\n⏳ Deal ends in {_fmt_left(c.ends_at - now)}"
            try:
                notifier.send(text)
            except Exception as e:
                print("couldn't send alert:", e)
            db.record_alert(c.asin, c.price, v.kind, now)


def _fmt_left(sec):
    h, m = divmod(int(sec // 60), 60)
    return f"{h // 24}d {h % 24}h" if h >= 24 else f"{h}h {m}m"


def _save_debug(label, page):
    """When a page has no products we can read, keep a copy so the parser can be fixed."""
    os.makedirs("debug", exist_ok=True)
    name = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "page"
    with open(os.path.join("debug", f"{name}.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"{label}: found no products on this page. Saved it to debug/{name}.html "
          "(send it to Claude so the reader can be fixed).")


def run_scan_loop(cfg, once=False):
    from .db import PriceDB
    from .main import make_notifier
    db = PriceDB(cfg.get("db_path", "prices.db"))
    notifier = make_notifier(cfg)
    if notifier.__class__.__name__ == "ConsoleNotifier":
        notifier = None  # without Telegram, deals just show on the dashboard
    interval = max(cfg.get("scan", {}).get("interval_minutes", DEFAULT_SCAN_MINUTES), MIN_SCAN_MINUTES) * 60
    while True:
        try:
            if cfg.get("source") == "creators_api":  # official API instead of reading pages
                from .creators_api import api_scan_once
                s = api_scan_once(db, cfg, notifier=notifier)
            else:
                s = scan_once(db, cfg, notifier=notifier)
            msg = f"scan: {s['pages']} pages, {s['products']} products seen, {s['deals']} on a deal"
            if s["stopped"]:
                msg += f" (stopped early: {s['stopped']})"
            print(time.strftime("%H:%M:%S"), msg)
        except Exception as e:
            print("scan failed:", e)
        if once:
            break
        time.sleep(interval)


def main():
    from .cli import load
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--file", nargs="+", metavar="PAGE.html",
                    help="read saved Amazon pages instead of contacting Amazon")
    args = ap.parse_args()
    cfg = load(args.config)
    if args.file:
        from .db import PriceDB
        s = import_files(PriceDB(cfg.get("db_path", "prices.db")), cfg, args.file)
        print(f"read {s['pages']} saved pages: {s['products']} products, {s['deals']} on a deal")
        return
    run_scan_loop(cfg, args.once)


if __name__ == "__main__":
    main()
