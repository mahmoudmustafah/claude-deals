"""Creators API tests with a fake HTTP layer (shapes follow Amazon's OffersV2 docs)."""
import unittest

from tracker.analyzer import DAY
from tracker.creators_api import (ApiError, CreatorsAPI, CreatorsApiSource, api_scan_once,
                                  item_to_card)
from tracker.db import PriceDB
from tracker.scanner import judge

NOW = 1_800_000_000.0
CFG = {"domain": "www.amazon.eg", "scan": {"queries": ["laptops"]},
       "creators_api": {"credential_id": "id", "credential_secret": "secret", "partner_tag": "tag-21",
                        "version": "3.2", "pages_per_query": 2, "seconds_between_requests": 0}}


def item(asin, price, basis=None, basis_type="LIST_PRICE", deal=None, kind=None, title="Thing"):
    listing = {"isBuyBoxWinner": True, "availability": {"type": "IN_STOCK"},
               "price": {"money": {"amount": price, "currency": "EGP"}}}
    if basis:
        listing["price"]["savingBasis"] = {"money": {"amount": basis, "currency": "EGP"},
                                           "savingBasisType": basis_type}
    if deal:
        listing["dealDetails"] = deal
    if kind:
        listing["type"] = kind
    return {"asin": asin, "itemInfo": {"title": {"displayValue": title}},
            "images": {"primary": {"medium": {"url": f"https://m.media-amazon.com/images/I/{asin}.jpg"}}},
            "offersV2": {"listings": [listing]}}


class FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        if url.endswith("/token"):
            return {"access_token": "tok", "expires_in": 3600}
        if url.endswith("getItems"):
            return {"itemsResult": {"items": [item(a, 1000) for a in body["itemIds"]]}}
        return {"searchResult": {"items": self.pages.get(body["itemPage"], [])}}


class ApiTests(unittest.TestCase):
    def test_token_endpoint_headers_and_body(self):
        http = FakeHTTP({})
        api = CreatorsAPI(CFG, post=http, sleep=lambda s: None)
        api.search_deals("laptops", 1, 15)
        token_call, call = http.calls
        self.assertEqual(token_call[0], "https://api.amazon.co.uk/auth/o2/token")  # Egypt = EU
        self.assertEqual(token_call[1]["scope"], "creatorsapi::default")
        self.assertEqual(call[0], "https://creatorsapi.amazon/catalog/v1/searchItems")
        self.assertEqual(call[2], {"Authorization": "Bearer tok", "x-marketplace": "www.amazon.eg"})
        self.assertEqual((call[1]["minSavingPercent"], call[1]["partnerTag"], call[1]["keywords"]),
                         (15, "tag-21", "laptops"))
        api.search_deals("laptops", 2)
        self.assertEqual(len(http.calls), 3)  # token is reused

    def test_item_to_card(self):
        c = item_to_card(item("B0LIGHT001", 1200, 2000, deal={
            "badge": "Lightning Deal", "endTime": "2027-01-15T08:00:00Z", "percentClaimed": "62"}))
        self.assertEqual((c.price, c.list_price, c.badge, c.claimed), (1200, 2000, "Lightning Deal", 0.62))
        self.assertEqual(c.ends_at, 1800000000.0)
        w = item_to_card(item("B0WAS00001", 900, 1100, basis_type="LOWEST_PRICE"))
        self.assertEqual((w.typical_price, w.list_price), (1100, None))
        self.assertIsNone(item_to_card({"asin": "B0NOPRICE1"}))

    def test_watch_list_prices_in_batches_of_10(self):
        http = FakeHTTP({})
        src = CreatorsApiSource(CFG, api=CreatorsAPI(CFG, post=http, sleep=lambda s: None))
        out = src.get_prices([f"B0{i:08d}" for i in range(23)])
        self.assertEqual(len(out), 23)
        self.assertEqual(sum(1 for c in http.calls if c[0].endswith("getItems")), 3)

    def test_api_scan_records_deals(self):
        db = PriceDB(":memory:")
        for i in range(100):
            db.record_price("B0DEAL0001", 5200, ts=NOW - 10 * DAY + i * 7200)
        pages = {1: [item("B0DEAL0001", 3850, 6990, title="Air fryer")] + [item(f"B0FILL{i:04d}", 500, 600) for i in range(9)],
                 2: [item("B0LAST0001", 100, 150)]}
        http = FakeHTTP(pages)
        stats = api_scan_once(db, CFG, api=CreatorsAPI(CFG, post=http, sleep=lambda s: None), now=NOW)
        self.assertEqual((stats["pages"], stats["deals"]), (2, 11))
        deal = db.deal("B0DEAL0001")
        self.assertEqual(judge(db, deal, now=NOW)["verdict"], "real")
        self.assertEqual(db.products(), [])

    def test_missing_credentials(self):
        with self.assertRaises(SystemExit):
            CreatorsAPI({"creators_api": {}})


if __name__ == "__main__":
    unittest.main()
