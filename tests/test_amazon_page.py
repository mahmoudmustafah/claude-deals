"""Parser tests on a trimmed copy of the structure Amazon product pages use.
(Live pages could not be fetched from the build machine; confirm with a real run.)"""
import unittest

from tracker.amazon_page import AmazonPageSource, Blocked, PoliteFetcher, parse_product_page

DEAL_PAGE = """
<html><body>
<span id="productTitle" class="a-size-large product-title-word-break">
   Tornado Electric Kettle 1.7 L, 2200 W &amp; Steel
</span>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price aok-align-center reinventPricePriceToPayMargin priceToPay" data-a-size="xl">
    <span class="a-offscreen">EGP1,299.00</span>
    <span aria-hidden="true"><span class="a-price-whole">1,299<span class="a-price-decimal">.</span></span><span class="a-price-fraction">00</span></span>
  </span>
  <span class="a-size-small aok-offscreen">List Price:</span>
  <span class="a-price a-text-price" data-a-size="s" data-a-strike="true" data-a-color="secondary">
    <span class="a-offscreen">EGP1,850.00</span>
  </span>
</div>
<div id="availability"><span class="a-size-medium a-color-success">In Stock</span></div>
</body></html>
"""

NO_LIST_PAGE = """
<span id="productTitle">Mouse</span>
<div id="corePrice_feature_div">
  <span class="a-price aok-align-center" data-a-size="xl">
    <span aria-hidden="true"><span class="a-price-whole">349.</span><span class="a-price-fraction">50</span></span>
  </span>
</div>
"""

OUT_OF_STOCK = """
<span id="productTitle">Headphones</span>
<div id="availability"><span class="a-color-price a-text-bold">Currently unavailable.</span>
We don't know when or if this item will be back in stock.</div>
"""

# Trimmed from a live amazon.eg product page (2026-11-03): the price's a-offscreen is empty
# and the strike-through span has extra classes.
LIVE_PAGE = """
<span id="productTitle" class="a-size-large product-title-word-break">        ANKER Soundcore Q11i Wireless Headphones       </span>
<div id="corePriceDisplay_desktop_feature_div" class="celwidget" data-csa-c-asin="B0DJW5G283"> <div class="a-section apex-core-price-identifier">
<span id="apex-pricetopay-accessibility-label" class="aok-offscreen"> EGP&nbsp;1,900.60 with 5 percent savings </span>
<span class="apex-savings-container "> <span aria-hidden="true" class="a-size-large a-color-price savingsPercentage">-5%</span> </span>
<span class="a-price aok-align-center reinventPricePriceToPayMargin priceToPay apex-pricetopay-value" data-a-size="xl" data-a-color="base"><span class="a-offscreen"> </span><span aria-hidden="true"><span class="a-price-symbol">EGP</span><span class="a-price-whole">1,900<span class="a-price-decimal">.</span></span><span class="a-price-fraction">60</span></span></span>
<span class="apex-basisprice-feature"><span class="aok-relative"><span class="a-size-small aok-offscreen apex-basisprice-offscreen-label">List Price: EGP 1,999.00</span><span aria-hidden="true" class="a-size-small a-color-secondary aok-align-center basisPrice"><span class="apex-basisprice-label">List Price:</span> <span class="a-price a-text-price apex-basisprice-value" data-a-size="s" data-a-strike="true" data-a-color="secondary"><span class="a-offscreen">EGP1,999.00</span><span aria-hidden="true">EGP1,999.00</span></span> </span></span></span>
</div></div>
<div id="availability" class="a-section a-spacing-base a-spacing-top-micro }">   In Stock   </div>
"""

CAPTCHA = """<form method="get" action="/errors/validateCaptcha">
<h4>Type the characters you see in this image:</h4></form>"""


class ParseTests(unittest.TestCase):
    def test_deal_page(self):
        q = parse_product_page(DEAL_PAGE, "B0TEST0001")
        self.assertEqual(q.price, 1299.0)
        self.assertEqual(q.list_price, 1850.0)
        self.assertTrue(q.in_stock)
        self.assertEqual(q.title, "Tornado Electric Kettle 1.7 L, 2200 W & Steel")

    def test_whole_fraction_fallback(self):
        q = parse_product_page(NO_LIST_PAGE, "B0TEST0002")
        self.assertEqual(q.price, 349.5)
        self.assertIsNone(q.list_price)

    def test_live_page(self):
        q = parse_product_page(LIVE_PAGE, "B0DJW5G283")
        self.assertEqual((q.price, q.list_price), (1900.6, 1999.0))
        self.assertTrue(q.in_stock)
        self.assertEqual(q.title, "ANKER Soundcore Q11i Wireless Headphones")

    def test_out_of_stock(self):
        q = parse_product_page(OUT_OF_STOCK, "B0TEST0003")
        self.assertFalse(q.in_stock)

    def test_captcha(self):
        with self.assertRaises(Blocked):
            parse_product_page(CAPTCHA, "B0TEST0004")


class PolitenessTests(unittest.TestCase):
    def test_pauses_between_products_and_backs_off_on_captcha(self):
        pages = {"A": DEAL_PAGE, "B": CAPTCHA, "C": DEAL_PAGE}
        fetched, slept = [], []

        def fetch(url):
            asin = url.rsplit("/", 1)[1]
            fetched.append(asin)
            return pages[asin]

        fetcher = PoliteFetcher(fetch=fetch, sleep=slept.append)
        src = AmazonPageSource(fetcher=fetcher)
        out = src.get_prices(["A", "B", "C"])
        self.assertEqual(list(out), ["A"])
        self.assertEqual(fetched, ["A", "B"])          # stopped at the robot check
        self.assertEqual(len(slept), 1)
        self.assertTrue(4 <= slept[0] <= 10)
        self.assertEqual(src.get_prices(["A"]), {})    # still paused
        self.assertEqual(fetched, ["A", "B"])
        self.assertEqual(fetcher.backoff_min, 60)          # next pause is longer


if __name__ == "__main__":
    unittest.main()
