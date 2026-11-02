"""Scanner tests on trimmed copies of the structure Amazon search result cards use.
(Live pages could not be fetched from the build machine; confirm with a real run.)"""
import unittest

from tracker.amazon_page import Blocked, PoliteFetcher
from tracker.analyzer import DAY
from tracker.db import PriceDB
from tracker.scanner import judge, parse_ends_in, parse_listing, scan_once

NOW = 1_800_000_000.0


def card(asin, title, price, strike=None, label="List:", badge=None, extra=""):
    strike_html = ""
    if strike:
        strike_html = (f'<span class="a-size-base a-color-secondary">{label}</span>'
                       f'<span class="a-price a-text-price" data-a-size="b" data-a-strike="true" data-a-color="secondary">'
                       f'<span class="a-offscreen">EGP{strike:,.2f}</span><span aria-hidden="true">EGP{strike:,.2f}</span></span>')
    badge_html = f'<span class="a-badge-text" data-a-badge-color="sx-cloud">{badge}</span>' if badge else ""
    return f"""
<div role="listitem" data-asin="{asin}" data-index="1" data-component-type="s-search-result" class="sg-col-4-of-24 s-result-item">
  <div class="s-product-image-container"><img class="s-image" src="https://m.media-amazon.com/images/I/{asin}._AC_UL320_.jpg" alt="{title}"></div>
  {badge_html}
  <a class="a-link-normal s-line-clamp-4" href="/-/en/x/dp/{asin}/ref=sr_1_1"><h2 aria-label="{title}" class="a-size-base-plus a-spacing-none a-color-base a-text-normal"><span>{title}</span></h2></a>
  <div class="a-row"><span class="a-price" data-a-size="xl" data-a-color="base"><span class="a-offscreen">EGP{price:,.2f}</span>
  <span aria-hidden="true"><span class="a-price-symbol">EGP</span><span class="a-price-whole">{int(price):,}<span class="a-price-decimal">.</span></span><span class="a-price-fraction">00</span></span></span>
  {strike_html}</div>
  {extra}
</div>"""


PAGE = ("<html><body><div class='s-main-slot'>"
        + card("B0DEAL0001", "Air Fryer 6L", 3850, 6990, badge="Limited time deal")
        + card("B0TYPE0001", "Kettle 1.7L", 900, 1100, label="Typical price:")
        + card("B0NODL0001", "USB-C Charger", 650)
        + card("B0LIGHT001", "Earbuds", 1200, 2000, badge="Lightning Deal",
               extra='<span>Ends in 02:15:00</span><span>62% claimed</span>')
        + card("B0DEAL0001", "Air Fryer 6L duplicate (sponsored)", 3850, 6990)
        + "</div></body></html>")


class ParseTests(unittest.TestCase):
    def test_cards(self):
        cards = {c.asin: c for c in parse_listing(PAGE, NOW)}
        self.assertEqual(set(cards), {"B0DEAL0001", "B0TYPE0001", "B0NODL0001", "B0LIGHT001"})
        a = cards["B0DEAL0001"]
        self.assertEqual((a.price, a.list_price, a.title), (3850, 6990, "Air Fryer 6L"))
        self.assertEqual(a.badge, "Limited time deal")
        self.assertTrue(a.is_deal)
        self.assertIn("m.media-amazon.com", a.image)
        k = cards["B0TYPE0001"]
        self.assertEqual((k.typical_price, k.list_price), (1100, None))
        self.assertFalse(cards["B0NODL0001"].is_deal)
        e = cards["B0LIGHT001"]
        self.assertEqual(e.ends_at, NOW + 2 * 3600 + 15 * 60)
        self.assertEqual(e.claimed, 0.62)

    def test_ends_in_formats(self):
        self.assertEqual(parse_ends_in("Ends in 3 days", NOW), NOW + 3 * DAY)
        self.assertEqual(parse_ends_in("Deal ends in 2h 15m", NOW), NOW + 2 * 3600 + 900)
        self.assertIsNone(parse_ends_in("Limited time deal", NOW))


class FakeFetcher(PoliteFetcher):
    def __init__(self, pages):
        super().__init__(fetch=lambda url: pages(url), sleep=lambda s: None)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.db = PriceDB(":memory:")
        self.cfg = {"scan": {"queries": ["kitchen"], "deals_page": False}}

    def test_scan_records_everything_and_judges(self):
        # 10 days of normal prices for the air fryer and the kettle, seen by earlier scans
        for i in range(100):
            t = NOW - 10 * DAY + i * 2 * 3600
            self.db.record_price("B0DEAL0001", 5200, ts=t)
            self.db.record_price("B0TYPE0001", 910, ts=t)
        stats = scan_once(self.db, self.cfg, FakeFetcher(lambda u: PAGE), now=NOW)
        self.assertEqual(stats["pages"], 1)
        self.assertEqual(stats["deals"], 3)
        deals = {d["asin"]: d for d in self.db.deals()}
        self.assertEqual(judge(self.db, deals["B0DEAL0001"], now=NOW)["verdict"], "real")
        self.assertEqual(judge(self.db, deals["B0TYPE0001"], now=NOW)["verdict"], "fake")
        self.assertEqual(judge(self.db, deals["B0LIGHT001"], now=NOW)["verdict"], "unverified")
        self.assertEqual(deals["B0LIGHT001"]["ends_at"], NOW + 8100)
        # scanned products are not added to the watch list
        self.assertEqual(self.db.products(), [])

    def test_deal_ends_when_discount_disappears_or_countdown_passes(self):
        scan_once(self.db, self.cfg, FakeFetcher(lambda u: PAGE), now=NOW)
        later = PAGE.replace("EGP3,850.00", "EGP5,200.00").replace("Limited time deal", "")
        later = later.replace('data-a-strike="true" data-a-color="secondary"><span class="a-offscreen">EGP6,990.00',
                              'data-a-strike="true" data-a-color="secondary"><span class="a-offscreen">EGP5,100.00')
        later = later.replace("Lightning Deal", "").replace("<span>Ends in 02:15:00</span><span>62% claimed</span>", "")
        later = later.replace("EGP1,200.00", "EGP2,000.00").replace("EGP2,000.00</span><span aria-hidden", "EGP1,990.00</span><span aria-hidden")
        scan_once(self.db, self.cfg, FakeFetcher(lambda u: later), now=NOW + 3 * 3600)
        deals = {d["asin"]: d for d in self.db.deals()}
        self.assertIsNotNone(deals["B0DEAL0001"]["ended_at"])
        self.assertIsNone(deals["B0TYPE0001"]["ended_at"])
        # Lightning deal countdown (2h15m) has passed
        self.assertEqual(deals["B0LIGHT001"]["ended_at"], NOW + 8100)

    def test_stops_when_blocked(self):
        def pages(url):
            raise Blocked("robot check")
        cfg = {"scan": {"queries": ["a", "b", "c"], "deals_page": False}}
        stats = scan_once(self.db, cfg, FakeFetcher(pages), now=NOW)
        self.assertEqual(stats["pages"], 0)
        self.assertIsNotNone(stats["stopped"])


if __name__ == "__main__":
    unittest.main()
