"""Reads prices from Amazon.eg product pages, for a small personal watch list.

Rules it follows on purpose:
- One page at a time, with a random pause of several seconds between products.
- If Amazon shows a robot check (CAPTCHA) or an error, it stops and waits longer
  before trying again (30 min, 60, then 2 hours). It never tries to get around it.
"""
import gzip
import html
import os
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

from .sources import PriceQuote, PriceSource

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Blocked(Exception):
    pass


def _ssl_context():
    """Python from python.org on a Mac comes without trusted certificates until you run its
    'Install Certificates' step; macOS's own list works just as well."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    return ctx


SSL_CONTEXT = _ssl_context()


def _num(text):
    """'EGP 1,299.00' / '1,299.' / '١٬٢٩٩' -> 1299.0"""
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group(0).replace(",", "")) if m else None


def _block(page, element_id, size=6000):
    """Text of the page starting at the element with this id (rough but robust)."""
    i = page.find(f'id="{element_id}"')
    return page[i:i + size] if i >= 0 else ""


PRICE_BLOCKS = ("corePriceDisplay_desktop_feature_div", "corePrice_feature_div",
                "corePrice_desktop", "apex_desktop", "price")


def is_robot_check(page):
    return ("validateCaptcha" in page or "Type the characters you see" in page
            or "api-services-support@amazon.com" in page)


def parse_product_page(page, asin):
    if is_robot_check(page):
        raise Blocked("Amazon showed a robot check")

    title = None
    m = re.search(r'id="productTitle"[^>]*>(.*?)</span>', page, re.S)
    if m:
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()

    price = list_price = None
    for bid in PRICE_BLOCKS:
        block = _block(page, bid)
        if not block:
            continue
        # The strike-through "was" price, if there is one
        # (live pages add more classes, e.g. "a-price a-text-price apex-basisprice-value")
        m = re.search(r'class="a-price a-text-price[^"]*"[^>]*data-a-strike="true"[^>]*>\s*'
                      r'<span class="a-offscreen">([^<]+)</span>', block)
        if m:
            list_price = _num(m.group(1))
        # The real price: first a-price that is not the strike-through one.
        # Live pages often leave its a-offscreen empty and only fill a-price-whole/-fraction.
        for m in re.finditer(r'<span class="a-price(?P<cls>[^"]*)"(?P<attrs>[^>]*)>\s*'
                             r'<span class="a-offscreen">(?P<val>[^<]*)</span>', block):
            if "a-text-price" in m.group("cls") or 'data-a-strike="true"' in m.group("attrs"):
                continue
            price = _num(m.group("val"))
            if price is None:
                block = block[m.start():]  # read whole/fraction from this same price
            break
        if price is None:
            w = re.search(r'class="a-price-whole">([^<]+)', block)
            f = re.search(r'class="a-price-fraction">(\d+)', block)
            if w:
                price = _num(w.group(1).rstrip(".") + "." + (f.group(1) if f else "0"))
        if price:
            break

    avail = re.sub(r"<[^>]+>|\s+", " ", _block(page, "availability", 1500)).lower()
    unavailable = "currently unavailable" in avail or "غير متوفر" in avail
    in_stock = bool(price) and not unavailable
    if list_price and price and list_price <= price:
        list_price = None
    return PriceQuote(asin=asin, price=price or 0.0, list_price=list_price, in_stock=in_stock,
                      title=title, url=f"https://www.amazon.eg/dp/{asin}")


class PoliteFetcher:
    """Shared by the watch list checker and the deals scanner: one request at a time,
    and if Amazon pushes back, *everything* pauses (30 min, 60, then 2 hours)."""

    def __init__(self, fetch=None, sleep=time.sleep):
        self.lock = threading.Lock()
        self.fetch_raw = fetch or self._fetch
        self.sleep = sleep
        self.blocked_until = 0.0
        self.backoff_min = 30

    @staticmethod
    def _fetch(url):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        })
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data.decode("utf-8", "replace")

    def paused_for(self):
        return max(0.0, self.blocked_until - time.time())

    def get(self, url):
        """Fetch a page. Raises Blocked while paused or when Amazon shows a robot check."""
        with self.lock:
            if self.paused_for():
                raise Blocked(f"paused for {int(self.paused_for() / 60)} more min")
            try:
                page = self.fetch_raw(url)
                if is_robot_check(page):
                    raise Blocked("Amazon showed a robot check")
                return page
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise
                self._pause(f"HTTP {e.code}")
                raise Blocked(f"HTTP {e.code}")
            except Blocked as e:
                self._pause(str(e))
                raise

    def _pause(self, why):
        self.blocked_until = time.time() + self.backoff_min * 60
        print(f"Amazon pushed back ({why}). Pausing all requests {self.backoff_min} min.")
        self.backoff_min = min(self.backoff_min * 2, 120)

    def ok(self):
        self.backoff_min = 30


SHARED = PoliteFetcher()


class AmazonPageSource(PriceSource):
    def __init__(self, domain="www.amazon.eg", pause=(4, 10), fetcher=None):
        self.domain = domain
        self.pause = pause
        self.fetcher = fetcher or SHARED

    def get_prices(self, asins):
        if self.fetcher.paused_for():
            print(f"Paused after a robot check; retrying in {int(self.fetcher.paused_for() / 60)} min.")
            return {}
        out = {}
        for i, asin in enumerate(asins):
            if i:
                self.fetcher.sleep(random.uniform(*self.pause))
            try:
                page = self.fetcher.get(f"https://{self.domain}/-/en/dp/{asin}")
                out[asin] = parse_product_page(page, asin)
            except Blocked:
                return out
            except urllib.error.HTTPError as e:
                print(f"{asin}: product page not found ({e.code}), skipping.")
                continue
            except Exception as e:
                print(f"{asin}: couldn't read page ({e})")
            if asin in out and not out[asin].price:
                print(f"{asin}: no price on the page (out of stock or page layout changed)")
        else:
            if asins:
                self.fetcher.ok()  # a full clean round resets the wait
        return out
