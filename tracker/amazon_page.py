"""Reads prices from Amazon.eg product pages, for a small personal watch list.

Rules it follows on purpose:
- One page at a time, with a random pause of several seconds between products.
- If Amazon shows a robot check (CAPTCHA) or an error, it stops and waits longer
  before trying again (10 min, 20, 40 ... up to 2 hours). It never tries to get around it.
"""
import gzip
import html
import random
import re
import time
import urllib.error
import urllib.request

from .sources import PriceQuote, PriceSource

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Blocked(Exception):
    pass


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


def parse_product_page(page, asin):
    if ("validateCaptcha" in page or "Type the characters you see" in page
            or "api-services-support@amazon.com" in page):
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
        m = re.search(r'class="a-price a-text-price"[^>]*data-a-strike="true"[^>]*>\s*'
                      r'<span class="a-offscreen">([^<]+)</span>', block)
        if m:
            list_price = _num(m.group(1))
        # The real price: first a-price that is not the strike-through one
        for m in re.finditer(r'<span class="a-price(?P<cls>[^"]*)"(?P<attrs>[^>]*)>\s*'
                             r'<span class="a-offscreen">(?P<val>[^<]+)</span>', block):
            if "a-text-price" in m.group("cls") or 'data-a-strike="true"' in m.group("attrs"):
                continue
            price = _num(m.group("val"))
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


class AmazonPageSource(PriceSource):
    def __init__(self, domain="www.amazon.eg", pause=(4, 10), fetch=None, sleep=time.sleep):
        self.domain = domain
        self.pause = pause
        self.fetch = fetch or self._fetch
        self.sleep = sleep
        self.blocked_until = 0.0
        self.backoff_min = 10

    def _fetch(self, url):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data.decode("utf-8", "replace")

    def get_prices(self, asins):
        now = time.time()
        if now < self.blocked_until:
            print(f"Paused after a robot check; retrying in {int((self.blocked_until - now) / 60)} min.")
            return {}
        out = {}
        for i, asin in enumerate(asins):
            if i:
                self.sleep(random.uniform(*self.pause))
            try:
                page = self.fetch(f"https://{self.domain}/-/en/dp/{asin}")
                out[asin] = parse_product_page(page, asin)
            except (Blocked, urllib.error.HTTPError) as e:
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    print(f"{asin}: product page not found (404), skipping.")
                    continue
                self.blocked_until = time.time() + self.backoff_min * 60
                print(f"Amazon pushed back ({e}). Pausing {self.backoff_min} min.")
                self.backoff_min = min(self.backoff_min * 2, 120)
                return out
            except Exception as e:
                print(f"{asin}: couldn't read page ({e})")
            if asin in out and not out[asin].price:
                print(f"{asin}: no price on the page (out of stock or page layout changed)")
        else:
            self.backoff_min = 10  # a full clean round resets the wait
        return out
