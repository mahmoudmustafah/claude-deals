"""Dashboard in your browser: Hot Deals (found automatically) + your watch list.

    python3 -m tracker.dashboard            # dashboard + deal scans + watch list checks
    python3 -m tracker.dashboard --no-track # dashboard only (no requests to Amazon)

Then open http://localhost:8000
"""
import argparse
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analyzer import DAY, Settings, analyze
from .cli import extract_asin, load, save
from .db import PriceDB
from .scanner import DEFAULT_SCAN_MINUTES, MIN_SCAN_MINUTES, judge
from .amazon_page import SHARED

PAGE = Path(__file__).with_name("dashboard.html")
MAX_POINTS = 400  # chart points per product


def _downsample(points, n=MAX_POINTS):
    if len(points) <= n:
        return points
    step = len(points) / n
    picked = [points[int(i * step)] for i in range(n)]
    # never drop the lowest price or the latest one from the chart
    low = min(points, key=lambda p: p["price"])
    if low not in picked:
        picked.append(low)
    picked.append(points[-1])
    return sorted({p["ts"]: p for p in picked}.values(), key=lambda p: p["ts"])


def summarize(db, product, settings, now=None):
    now = now or time.time()
    hist = db.history(product["asin"], now - 90 * DAY)
    out = {"asin": product["asin"], "title": product.get("title") or product["asin"],
           "url": product.get("url"), "price": None, "status": "waiting", "tags": [],
           "notes": [], "history": []}
    if not hist:
        return out
    last = hist[-1]
    v = analyze(hist[:-1], last["ts"], last["price"], last["list_price"], settings)
    if "PRICE_ERROR" in v.tags:
        status = "error"
    elif "REAL_DEAL" in v.tags:
        status = "deal"
    elif {"FAKE_LIST_PRICE", "INFLATED_BEFORE_SALE"} & set(v.tags):
        status = "fake"
    elif "LEARNING" in v.tags:
        status = "learning"
    else:
        status = "normal"
    out.update({
        "price": last["price"], "list_price": last["list_price"], "last_checked": last["ts"],
        "reference": v.reference, "low_90": v.low_90 if v.low_90 is not None else last["price"],
        "high_90": v.high_90 if v.high_90 is not None else last["price"],
        "real_discount": v.real_discount, "claimed_discount": v.claimed_discount,
        "status": status, "tags": v.tags, "notes": v.notes,
        "history": [{"t": p["ts"], "p": p["price"]} for p in _downsample(hist)],
    })
    if out["low_90"] is not None:
        out["low_90"] = min(out["low_90"], last["price"])
        out["high_90"] = max(out["high_90"], last["price"])
    return out


def deals_payload(db, cfg, now=None):
    now = now or time.time()
    settings = Settings(**cfg.get("settings", {}))
    out = []
    for d in db.deals(since_ended=now - DAY):
        j = judge(db, d, settings, now)
        hist = db.history(d["asin"], now - 30 * DAY)
        d.update(j)
        d["history"] = [{"t": p["ts"], "p": p["price"]} for p in _downsample(hist, 60)]
        d["title"] = d.get("title") or d["asin"]
        out.append(d)
    scan = cfg.get("scan", {})
    last = db.get_meta("last_scan")
    return {
        "currency": cfg.get("currency", "EGP"), "now": now,
        "scan": {"interval_minutes": max(scan.get("interval_minutes", DEFAULT_SCAN_MINUTES), MIN_SCAN_MINUTES),
                 "last_scan": float(last) if last else None,
                 "last_result": db.get_meta("last_scan_stats"),
                 "paused_minutes": round(SHARED.paused_for() / 60),
                 "products_seen": db.count_products()},
        "deals": out,
    }


class Handler(BaseHTTPRequestHandler):
    cfg_path = "config.json"

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ctx(self):
        cfg = load(self.cfg_path)
        return cfg, PriceDB(cfg.get("db_path", "prices.db"))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            cfg, db = self._ctx()
            settings = Settings(**cfg.get("settings", {}))
            self._json({
                "currency": cfg.get("currency", "EGP"),
                "interval_minutes": max(cfg.get("interval_minutes", 10), 5),
                "last_check": db.last_check(),
                "now": time.time(),
                "products": [summarize(db, p, settings) for p in db.products()],
                "alerts": db.recent_alerts(15),
            })
        elif self.path == "/api/deals":
            cfg, db = self._ctx()
            self._json(deals_payload(db, cfg))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/products":
            return self._json({"error": "not found"}, 404)
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            asin = extract_asin(data.get("link", ""))
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        cfg, db = self._ctx()
        title = (data.get("title") or "").strip() or None
        domain = cfg.get("domain", "www.amazon.eg")
        url = f"https://{domain}/dp/{asin}"
        if not any(p["asin"] == asin for p in cfg["products"]):
            cfg["products"].append({"asin": asin, "title": title, "url": url})
            save(self.cfg_path, cfg)
        db.add_product(asin, title, url)
        self._json({"ok": True, "asin": asin})

    def do_DELETE(self):
        if not self.path.startswith("/api/products/"):
            return self._json({"error": "not found"}, 404)
        asin = self.path.rsplit("/", 1)[1].upper()
        cfg, db = self._ctx()
        cfg["products"] = [p for p in cfg["products"] if p["asin"] != asin]
        save(self.cfg_path, cfg)
        db.remove_product(asin)
        self._json({"ok": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-track", action="store_true", help="don't contact Amazon (dashboard only)")
    ap.add_argument("--no-scan", action="store_true", help="only check the watch list, no deal scans")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    Handler.cfg_path = args.config
    server = None
    for port in range(args.port, args.port + 20):  # if the port is busy, try the next one
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            print(f"Port {port} is busy (maybe the tracker is already running in another window).")
    if server is None:
        raise SystemExit("No free port found. Close other tracker windows and try again.")
    url = f"http://localhost:{port}"
    if not args.no_track:
        from .main import run_loop
        from .scanner import run_scan_loop
        threading.Thread(target=run_loop, args=(load(args.config),), daemon=True).start()
        if not args.no_scan:
            threading.Thread(target=run_scan_loop, args=(load(args.config),), daemon=True).start()
    print(f"Dashboard running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
