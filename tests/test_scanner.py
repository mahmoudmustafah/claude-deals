"""Scanner tests on trimmed copies of the structure Amazon search result cards use.
(Live pages could not be fetched from the build machine; confirm with a real run.)"""
import os
import tempfile
import unittest

from tracker.amazon_page import Blocked, PoliteFetcher
from tracker.analyzer import DAY
from tracker.db import PriceDB
from tracker.scanner import import_files, judge, parse_ends_in, parse_listing, scan_once

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


# Trimmed from live amazon.eg pages fetched on 2026-11-03.
LIVE_SEARCH_WAS = """
<div role="listitem" data-asin="B0WASPRICE" data-index="15" data-component-type="s-search-result" class="s-result-item s-asin">
<div cel_widget_id="MAIN-SEARCH_RESULTS-15" data-csa-c-item-id="amzn1.asin.1.B0WASPRICE"><span class="a-declarative" data-action="puis-card-container-declarative" data-csa-c-item-id="amzn1.asin.B0WASPRICE" data-csa-c-type="item" data-csa-c-owner="puis">
<div class="s-product-image-container"><img class="s-image" src="https://m.media-amazon.com/images/I/61abcdEFgh._AC_UL320_.jpg" alt="Wired Earphones..."/></div>
<div data-cy="title-recipe"><a class="a-link-normal" href="/-/en/x/dp/B0WASPRICE/ref=sr_1_13"><h2 aria-label="Wired Earphones with Mic" class="a-size-base-plus"><span>Wired Earphones with Mic</span></h2></a></div>
<div data-cy="price-recipe"><div class="a-row"><span id="DEAL_B0WASPRICE" class="a-badge" data-a-badge-type="deal"><span class="a-badge-label-inner a-text-ellipsis"><span class="a-badge-text" data-a-badge-color="sx-deal-dynamic-text">Limited time deal</span></span></span></div>
<a class="a-link-normal s-no-hover" href="/-/en/x/dp/B0WASPRICE"><span class="a-price" data-a-size="xl" data-a-color="base"><span class="a-offscreen">EGP 245.00</span><span aria-hidden="true"><span class="a-price-symbol">EGP</span><span class="a-price-whole">245<span class="a-price-decimal">.</span></span><span class="a-price-fraction">00</span></span></span> <span class="a-offscreen">Was: EGP 279.99</span><div aria-hidden="Was: EGP 279.99" class="a-section aok-inline-block"><span class="a-size-base a-color-secondary">Was: </span><span class="a-price a-text-price" data-a-size="b" data-a-strike="true" data-a-color="secondary"><span class="a-offscreen">EGP 279.99</span><span aria-hidden="true">EGP279.99</span></span></div></a>
<div class="a-row a-size-base a-color-secondary"><span>20% off NBE Visa Signature Credit Cards</span></div></div>
</span></div></div>"""

LIVE_DEALS = """
<ol class="a-carousel" role="list"><li aria-roledescription="slide" class="a-carousel-card dcl-carousel-element"><div class="dcl-product-wrapper" data-csa-c-pos="1,2" data-csa-c-type="item" data-csa-c-item-type="asin" data-csa-c-item-id="amzn1.asin.B0GTLCZWVQ" data-csa-c-owner="Dossier"><div class="a-cardui dcl-product"><a class="a-link-normal dcl-product-link" href="/-/en/Arzum/dp/B0GTLCZWVQ?ref_=ST-shovler-2"><div class="dcl-product-image-container"><img alt="" src="https://m.media-amazon.com/images/I/31681RiloDL._AC_SR240,220_.jpg" class="a-dynamic-image dcl-dynamic-image"/></div><div class="a-section dcl-product-attributes"><span class="dcl-truncate dcl-product-title" style="--max-rows:1"><span>Arzum OKKA Minio Turkish Coffee Machine OK0050-04, 5 Cups</span></span><div class="a-section dcl-product-price"><div class="dcl-price-single"><span class="a-price dcl-product-price-new" data-a-size="medium_plus" data-a-color="base"><span class="a-offscreen">EGP 2,815.96</span><span aria-hidden="true"><span class="a-price-symbol">EGP</span><span class="a-price-whole">2,815<span class="a-price-decimal"></span></span><span class="a-price-fraction">96</span></span></span><div class="a-section dcl-product-old-price-section"><span class="a-size-mini a-color-secondary dcl-product-old-price-label aok-offscreen">List:</span><span class="a-price a-text-price dcl-product-price-old" data-a-size="mini" data-a-strike="true" data-a-color="secondary"><span class="a-offscreen">EGP 3,200.00</span><span aria-hidden="true">EGP 3,200.00</span></span></div></div></div></div></a></div></div></li></ol>
<div id="slot-14" data-id="discount-asin-grid"><div id="dealsGridLinkAnchor" class="discounts-react-app"></div>
<script>
  (function () { window.P && P.when('discounts-react-app').execute(function (app) { app.render(
  {"marketplaceId":"ARBP9OOSHTCHU","site":"amazon.eg","currencyIsoCode":"EGP","productSearchResponse":{"nextIndex":30,"startIndex":0,"products":[
   {"asin":"B0H68JT4HK","title":"Haier 32 Inch Smart TV - H32K70G","link":"/Haier/dp/B0H68JT4HK","image":{"altText":"Haier","lowRes":{"baseUrl":"https://m.media-amazon.com/images/I/41Cubsy2J+L","extension":"jpg","width":500,"height":500}},"price":{"priceToPay":{"label":"Deal Price:","price":"5999.0","strikethrough":false},"basisPrice":{"label":"List:","price":"6499.0","strikethrough":true}},"dealBadge":{"label":{"content":{"fragments":[{"text":"8% off"}]}},"messaging":{"content":{"fragments":[{"text":"Limited time deal"}]}}},"dealDetails":{"state":"AVAILABLE","type":"BEST_DEAL","id":"2d048156"},"addToCart":{"isLightningDeal":false}},
   {"asin":"B0F2B94W29","title":"adidas MEN CLOUDFOAM FLEX - LACES SHOES","link":"/adidas/dp/B0F2B94W29","image":{"lowRes":{"baseUrl":"https://m.media-amazon.com/images/I/31GJF7i93DL","extension":"jpg"}},"price":{"priceToPay":{"label":"Deal Price:","price":"2410.0","strikethrough":false},"basisPrice":{"label":"Was:","price":"2889.0","strikethrough":true}},"dealBadge":{"label":{"content":{"fragments":[{"text":"17% off"}]}},"messaging":{"content":{"fragments":[{"text":"Ends in "},{"countdownTimer":{"targetTime":"2027-01-16T08:00:00.000Z"}}]}}},"dealDetails":{"state":"AVAILABLE","type":"BEST_DEAL","id":"d8ce3b4b"}},
   {"asin":"B0BXB2CNQ3","title":"Notebook A5","price":{"priceToPay":{"label":"Price:","price":"147.5","strikethrough":false},"basisPrice":{"label":"List:","price":"276.0","strikethrough":true}},"dealBadge":null},
   {"asin":"B0NOPRICE1","title":"No price yet","price":{}}
  ],"refinements":[]},"acsConfig":{}}); }); })();
</script></div>"""


class LiveMarkupTests(unittest.TestCase):
    def test_search_card_with_was_price(self):
        [c] = parse_listing(LIVE_SEARCH_WAS, NOW)
        self.assertEqual((c.asin, c.price, c.typical_price, c.list_price), ("B0WASPRICE", 245.0, 279.99, None))
        self.assertEqual(c.title, "Wired Earphones with Mic")
        self.assertEqual(c.badge, "Limited time deal")
        self.assertIsNone(c.ends_at)
        self.assertIsNone(c.claimed)

    def test_deals_page_carousel_and_embedded_json(self):
        cards = {c.asin: c for c in parse_listing(LIVE_DEALS, NOW)}
        self.assertEqual(set(cards), {"B0GTLCZWVQ", "B0H68JT4HK", "B0F2B94W29", "B0BXB2CNQ3"})
        k = cards["B0GTLCZWVQ"]   # carousel card
        self.assertEqual((k.price, k.list_price), (2815.96, 3200.0))
        self.assertTrue(k.title.startswith("Arzum OKKA Minio"))
        self.assertIn("31681RiloDL", k.image)
        self.assertIsNone(k.badge)   # the grid's "Limited time deal" text must not leak in
        tv = cards["B0H68JT4HK"]
        self.assertEqual((tv.price, tv.list_price, tv.badge), (5999.0, 6499.0, "Limited time deal"))
        self.assertEqual(tv.image, "https://m.media-amazon.com/images/I/41Cubsy2J+L._AC_UL320_.jpg")
        shoe = cards["B0F2B94W29"]
        self.assertEqual((shoe.price, shoe.typical_price, shoe.list_price), (2410.0, 2889.0, None))
        self.assertEqual(shoe.ends_at, 1800086400.0)   # 2027-01-16T08:00:00Z
        self.assertEqual(shoe.badge, "Limited time deal")
        self.assertTrue(cards["B0BXB2CNQ3"].is_deal)   # no badge, but 47% below list price
        self.assertIsNone(cards["B0BXB2CNQ3"].badge)


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

    def test_blocked_scan_does_not_end_deals(self):
        scan_once(self.db, self.cfg, FakeFetcher(lambda u: PAGE), now=NOW)

        def refuse(url):
            raise Blocked("robot check")
        # a day of refused scans: deals weren't seen, but only because we couldn't look
        scan_once(self.db, self.cfg, FakeFetcher(refuse), now=NOW + DAY)
        deals = {d["asin"]: d for d in self.db.deals()}
        self.assertIsNone(deals["B0DEAL0001"]["ended_at"])
        self.assertIsNone(deals["B0TYPE0001"]["ended_at"])
        # a passed countdown still ends the deal
        self.assertEqual(deals["B0LIGHT001"]["ended_at"], NOW + 8100)

    def test_reopen_deals_ended_only_for_not_being_seen(self):
        scan_once(self.db, self.cfg, FakeFetcher(lambda u: PAGE), now=NOW)
        self.db.end_stale_deals(NOW + DAY, stale_after=3 * 3600)  # the old behaviour
        self.assertEqual(self.db.reopen_unconfirmed_deals(NOW + DAY), 2)
        deals = {d["asin"]: d for d in self.db.deals()}
        self.assertIsNone(deals["B0DEAL0001"]["ended_at"])
        self.assertEqual(deals["B0LIGHT001"]["ended_at"], NOW + 8100)

    def test_import_saved_pages(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "deals.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(LIVE_DEALS)
            os.utime(path, (NOW, NOW))
            stats = import_files(self.db, self.cfg, [path])
        self.assertEqual((stats["pages"], stats["products"], stats["deals"]), (1, 4, 4))
        deals = {d["asin"]: d for d in self.db.deals()}
        self.assertEqual(deals["B0F2B94W29"]["source"], "Today's deals")
        self.assertEqual(deals["B0F2B94W29"]["first_seen"], NOW)

    def test_stops_when_blocked(self):
        def pages(url):
            raise Blocked("robot check")
        cfg = {"scan": {"queries": ["a", "b", "c"], "deals_page": False}}
        stats = scan_once(self.db, cfg, FakeFetcher(pages), now=NOW)
        self.assertEqual(stats["pages"], 0)
        self.assertIsNotNone(stats["stopped"])


if __name__ == "__main__":
    unittest.main()


class AlreadyOnSaleTests(unittest.TestCase):
    """A product that was already 'on sale' when we first saw it has no 'before' price."""

    def _deal(self, prices_after):
        db = PriceDB(":memory:")
        db.upsert_deal("B0ALWAYS01", NOW, prices_after[-1], list_price=350)
        for i, p in enumerate(prices_after):
            db.record_price("B0ALWAYS01", p, 350, ts=NOW + i * 6 * 3600)
        return db, db.deal("B0ALWAYS01")

    def test_unverified_at_first_with_days_needed(self):
        db, d = self._deal([156.5])
        j = judge(db, d, now=NOW + 3600)
        self.assertEqual(j["verdict"], "unverified")
        self.assertAlmostEqual(j["days_needed"], 3 - 1 / 24, places=2)

    def test_same_deal_price_for_days_is_fake(self):
        db, d = self._deal([156.5] * 16)          # 4 days at the "deal" price
        j = judge(db, d, now=NOW + 4 * DAY)
        self.assertEqual(j["verdict"], "fake")
        self.assertEqual(j["reference"], 156.5)

    def test_real_drop_after_long_steady_price(self):
        db, d = self._deal([200.0] * 15 + [150.0])  # dropped below its steady price
        j = judge(db, d, now=NOW + 4 * DAY)
        self.assertEqual(j["verdict"], "real")
