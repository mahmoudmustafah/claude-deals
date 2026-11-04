import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tracker.analyzer import DAY
from tracker.dashboard import Handler, summarize
from tracker.analyzer import Settings
from tracker.db import PriceDB


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.json")
        self.dbp = os.path.join(self.dir, "p.db")
        json.dump({"db_path": self.dbp, "products": []}, open(self.cfg, "w"))
        db = PriceDB(self.dbp)
        db.add_product("B0DEAL0001", "Air fryer")
        now = time.time()
        for i in range(200):
            db.record_price("B0DEAL0001", 1000, ts=now - 20 * DAY + i * 2400)
        db.record_price("B0DEAL0001", 750, 1200, ts=now)

    def test_summarize_marks_real_deal(self):
        db = PriceDB(self.dbp)
        s = summarize(db, db.products()[0], Settings())
        self.assertEqual(s["status"], "deal")
        self.assertEqual(s["reference"], 1000)
        self.assertEqual(s["history"][-1]["p"], 750)

    def test_api_add_list_remove(self):
        Handler.cfg_path = self.cfg
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            req = urllib.request.Request(base + "/api/products", method="POST",
                                         data=json.dumps({"link": "https://www.amazon.eg/dp/B0NEW00001"}).encode())
            self.assertTrue(json.load(urllib.request.urlopen(req))["ok"])
            data = json.load(urllib.request.urlopen(base + "/api/data"))
            self.assertEqual({p["asin"] for p in data["products"]}, {"B0DEAL0001", "B0NEW00001"})
            urllib.request.urlopen(urllib.request.Request(base + "/api/products/B0NEW00001", method="DELETE"))
            data = json.load(urllib.request.urlopen(base + "/api/data"))
            self.assertEqual([p["asin"] for p in data["products"]], ["B0DEAL0001"])
            self.assertIn(b"Price Watch", urllib.request.urlopen(base + "/").read())
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
