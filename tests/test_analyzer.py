import unittest

from tracker.analyzer import DAY, analyze
from tracker.db import PriceDB
from tracker.main import check_once
from tracker.analyzer import Settings
from tracker.sources import PriceQuote

NOW = 100 * DAY


def flat(price, days=60, every_hours=6):
    n = int(days * 24 / every_hours)
    return [{"ts": NOW - days * DAY + i * every_hours * 3600, "price": price} for i in range(n)]


class AnalyzerTests(unittest.TestCase):
    def test_learning_with_little_history(self):
        v = analyze(flat(1000, days=1), NOW, 700)
        self.assertIn("LEARNING", v.tags)
        self.assertFalse(v.is_alert)

    def test_real_deal(self):
        v = analyze(flat(1000), NOW, 800, list_price=1200)
        self.assertIn("REAL_DEAL", v.tags)
        self.assertIn("LOWEST_90D", v.tags)
        self.assertAlmostEqual(v.reference, 1000)

    def test_fake_list_price(self):
        v = analyze(flat(1000), NOW, 990, list_price=1500)
        self.assertIn("FAKE_LIST_PRICE", v.tags)
        self.assertFalse(v.is_alert)

    def test_inflated_before_sale(self):
        h = flat(1000, days=60) + [{"ts": NOW - 5 * DAY + i * 3600, "price": 1300} for i in range(100)]
        v = analyze(h, NOW, 1000, list_price=1300)
        self.assertIn("INFLATED_BEFORE_SALE", v.tags)
        self.assertFalse(v.is_alert)

    def test_pricing_error(self):
        v = analyze(flat(1000), NOW, 300)
        self.assertEqual(v.kind, "PRICE_ERROR")
        self.assertIn("BIG_DROP", v.tags)


class FakeSource:
    def __init__(self):
        self.price = 1000

    def get_prices(self, asins):
        return {a: PriceQuote(a, self.price) for a in asins}


class Collect:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


class EndToEnd(unittest.TestCase):
    def test_alert_once_then_again_on_further_drop(self):
        db, src, out = PriceDB(":memory:"), FakeSource(), Collect()
        db.add_product("B000TEST01", "Test kettle")
        t = NOW - 10 * DAY
        while t < NOW:                     # 10 days of normal prices, every 10 min
            check_once(db, src, out, Settings(), now=t)
            t += 600
        self.assertEqual(out.sent, [])
        src.price = 800
        check_once(db, src, out, Settings(), now=NOW)
        check_once(db, src, out, Settings(), now=NOW + 600)
        self.assertEqual(len(out.sent), 1)  # no repeat for the same price
        src.price = 700
        check_once(db, src, out, Settings(), now=NOW + 1200)
        self.assertEqual(len(out.sent), 2)
        self.assertIn("Real deal", out.sent[0])


if __name__ == "__main__":
    unittest.main()
