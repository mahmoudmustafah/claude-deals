"""Amazon's official Creators API (the replacement for the old Product Advertising API).

Allowed by Amazon and not blocked like page reading. Needs an Amazon Associates account
that has made qualifying sales; then Associates Central > Tools > Creators API >
Create Application gives you a Credential ID, Secret and Version.

Put them in config.json:

    "source": "creators_api",
    "creators_api": {
        "credential_id": "...", "credential_secret": "...", "version": "3.2",
        "partner_tag": "yourtag-21"
    }

(or set CREATORS_API_ID / CREATORS_API_SECRET / CREATORS_API_TAG environment variables).

What it gives the tracker:
- watch list prices (GetItems, 10 products per request)
- deal discovery: SearchItems for each search term with "minSavingPercent", so Amazon
  itself returns only discounted products (up to 10 per page, 3 pages per term), with the deal's end time and % claimed for Lightning Deals
- Amazon's reference price type: LIST_PRICE, WAS_PRICE or LOWEST_PRICE (lowest price
  in the last 30 days), which helps before our own history is long enough
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

from .sources import PriceQuote, PriceSource

API_BASE = "https://creatorsapi.amazon/catalog/v1/"
TOKEN_URLS = {  # by region of the marketplace; Egypt uses the EU endpoint
    "NA": "https://api.amazon.com/auth/o2/token",
    "EU": "https://api.amazon.co.uk/auth/o2/token",
    "FE": "https://api.amazon.co.jp/auth/o2/token",
}
REGION = {"www.amazon.com": "NA", "www.amazon.ca": "NA", "www.amazon.com.mx": "NA",
          "www.amazon.com.br": "NA", "www.amazon.co.jp": "FE", "www.amazon.sg": "FE",
          "www.amazon.com.au": "FE"}  # everything else (incl. www.amazon.eg) is EU
RESOURCES = [
    "itemInfo.title", "images.primary.medium",
    "offersV2.listings.price", "offersV2.listings.availability",
    "offersV2.listings.dealDetails", "offersV2.listings.isBuyBoxWinner",
    "offersV2.listings.type",
]


class ApiError(Exception):
    pass


def _http_post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise ApiError(f"HTTP {e.code}: {detail}") from None


class CreatorsAPI:
    def __init__(self, cfg, post=_http_post_json, sleep=time.sleep):
        api = cfg.get("creators_api", {})
        self.marketplace = cfg.get("domain", "www.amazon.eg")
        self.cid = os.environ.get("CREATORS_API_ID") or api.get("credential_id")
        self.secret = os.environ.get("CREATORS_API_SECRET") or api.get("credential_secret")
        self.tag = os.environ.get("CREATORS_API_TAG") or api.get("partner_tag")
        self.version = str(api.get("version", "3.2"))
        if not (self.cid and self.secret and self.tag):
            raise SystemExit("Creators API: add credential_id, credential_secret and partner_tag "
                             "to the creators_api section of config.json.")
        self.token_url = TOKEN_URLS[REGION.get(self.marketplace, "EU")]
        self.post = post
        self.sleep = sleep
        self.token, self.token_expires = None, 0.0
        self.min_gap = float(api.get("seconds_between_requests", 1.5))
        self.last_call = 0.0

    def _auth(self):
        if not self.token or time.time() > self.token_expires - 120:
            r = self.post(self.token_url, {"grant_type": "client_credentials", "client_id": self.cid,
                                           "client_secret": self.secret,
                                           "scope": "creatorsapi::default"}, {})
            self.token = r["access_token"]
            self.token_expires = time.time() + int(r.get("expires_in", 3600))
        auth = f"Bearer {self.token}"
        if self.version.startswith("2."):  # older credentials need the version in the header
            auth += f", Version {self.version}"
        return auth

    def call(self, operation, body):
        wait = self.min_gap - (time.time() - self.last_call)
        if wait > 0:
            self.sleep(wait)
        body = {"partnerTag": self.tag, "marketplace": self.marketplace, "resources": RESOURCES, **body}
        try:
            return self.post(API_BASE + operation, body,
                             {"Authorization": self._auth(), "x-marketplace": self.marketplace})
        finally:
            self.last_call = time.time()

    def get_items(self, asins):
        out = []
        for i in range(0, len(asins), 10):
            r = self.call("getItems", {"itemIds": asins[i:i + 10], "itemIdType": "ASIN"})
            out += ((r.get("itemsResult") or {}).get("items") or [])
        return out

    def search_deals(self, keywords, page=1, min_saving=10, search_index="All"):
        """Only products Amazon itself marks at least `min_saving`% off."""
        body = {"keywords": keywords, "searchIndex": search_index, "minSavingPercent": int(min_saving),
                "itemCount": 10, "itemPage": page}
        r = self.call("searchItems", body)
        return ((r.get("searchResult") or {}).get("items") or [])


# ---------- reading API items ----------

def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p, d.get(p[:1].upper() + p[1:]))  # docs mix camelCase and PascalCase
    return d


def _iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def best_listing(item):
    listings = _get(item, "offersV2", "listings") or []
    return next((l for l in listings if _get(l, "isBuyBoxWinner")), listings[0] if listings else None)


def item_to_card(item, domain="www.amazon.eg"):
    from .scanner import Card
    l = best_listing(item)
    price = _get(l, "price", "money", "amount") if l else None
    if not price:
        return None
    basis = _get(l, "price", "savingBasis", "money", "amount")
    basis_type = (_get(l, "price", "savingBasis", "savingBasisType") or "").upper()
    deal = _get(l, "dealDetails") or {}
    claimed = _get(deal, "percentClaimed")
    asin = item.get("asin") or item.get("ASIN")
    card = Card(
        asin=asin, title=_get(item, "itemInfo", "title", "displayValue"), price=float(price),
        badge=_get(deal, "badge") or ("Lightning Deal" if (_get(l, "type") or "") == "LIGHTNING_DEAL" else None),
        ends_at=_iso(_get(deal, "endTime")),
        claimed=float(str(claimed).rstrip("%")) / 100 if claimed not in (None, "") else None,
        image=_get(item, "images", "primary", "medium", "url"),
        url=f"https://{domain}/dp/{asin}",
    )
    if basis and float(basis) > card.price:
        if basis_type == "LIST_PRICE":
            card.list_price = float(basis)
        else:  # WAS_PRICE / LOWEST_PRICE(_STRIKETHROUGH): Amazon's recent-price reference
            card.typical_price = float(basis)
    return card


def item_to_quote(item, domain="www.amazon.eg"):
    c = item_to_card(item, domain)
    l = best_listing(item)
    avail = (_get(l, "availability", "type") or "IN_STOCK").upper() if l else "OUT_OF_STOCK"
    if not c:
        asin = item.get("asin") or item.get("ASIN")
        return PriceQuote(asin, 0.0, in_stock=False, title=_get(item, "itemInfo", "title", "displayValue"))
    return PriceQuote(c.asin, c.price, list_price=c.list_price or c.typical_price,
                      in_stock=avail not in ("OUT_OF_STOCK", "UNAVAILABLE"), title=c.title, url=c.url)


# ---------- plugging into the tracker ----------

class CreatorsApiSource(PriceSource):
    """Watch list prices from the official API."""

    def __init__(self, cfg, api=None):
        self.api = api or CreatorsAPI(cfg)
        self.domain = cfg.get("domain", "www.amazon.eg")

    def get_prices(self, asins):
        out = {}
        try:
            for item in self.api.get_items(list(asins)):
                q = item_to_quote(item, self.domain)
                out[q.asin] = q
        except ApiError as e:
            print("Creators API error:", e)
        return out


def api_scan_once(db, cfg, notifier=None, settings=None, now=None, api=None):
    """Deal discovery through SearchItems with a minimum saving, per category."""
    from .analyzer import Settings
    from .scanner import MIN_SCAN_MINUTES, _alert_new_deals

    settings = settings or Settings(**cfg.get("settings", {}))
    api = api or CreatorsAPI(cfg)
    opts = cfg.get("creators_api", {})
    domain = cfg.get("domain", "www.amazon.eg")
    interval = max(cfg.get("scan", {}).get("interval_minutes", 30), MIN_SCAN_MINUTES) * 60
    stats = {"pages": 0, "products": 0, "deals": 0, "empty_pages": [], "stopped": None}
    from .scanner import DEFAULT_QUERIES
    queries = opts.get("queries") or cfg.get("scan", {}).get("queries", DEFAULT_QUERIES)
    pages = int(opts.get("pages_per_query", 3))
    for index in queries:  # each search term, e.g. "laptops"
        for page in range(1, pages + 1):
            t = now or time.time()
            try:
                items = api.search_deals(index, page, opts.get("min_saving_percent", 10))
            except ApiError as e:
                print(f"{index} page {page}: {e}")
                if "429" in str(e) or "TooManyRequests" in str(e):
                    stats["stopped"] = "API rate limit"
                    break
                continue
            stats["pages"] += 1
            cards = [c for c in (item_to_card(i, domain) for i in items) if c]
            for c in cards:
                db.seen_product(c.asin, c.title, c.url, c.image, index)
                db.record_price(c.asin, c.price, c.list_price or c.typical_price, ts=t, commit=False)
                stats["products"] += 1
                if c.is_deal:
                    stats["deals"] += 1
                    db.upsert_deal(c.asin, t, c.price, c.list_price, c.typical_price, c.badge,
                                   c.ends_at, c.claimed, index)
            db.commit()
            if notifier:
                _alert_new_deals(db, [c for c in cards if c.is_deal], notifier, settings, cfg, t)
            if len(items) < 10:
                break  # no more pages in this category
        if stats["stopped"]:
            break
    db.end_stale_deals(now or time.time(), stale_after=max(3 * interval, 3 * 3600))
    db.set_meta("last_scan", now or time.time())
    db.set_meta("last_scan_stats", f"{stats['pages']} API pages" +
                (f", stopped: {stats['stopped']}" if stats["stopped"] else ""))
    return stats
