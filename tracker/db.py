"""Price history storage (SQLite: a single file, no server needed)."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    asin        TEXT PRIMARY KEY,
    title       TEXT,
    url         TEXT,
    added_at    REAL
);
CREATE TABLE IF NOT EXISTS prices (
    asin        TEXT NOT NULL,
    ts          REAL NOT NULL,          -- unix time (seconds)
    price       REAL NOT NULL,          -- what you actually pay
    list_price  REAL,                   -- Amazon's crossed-out "was" price, if any
    in_stock    INTEGER DEFAULT 1,
    seller      TEXT
);
CREATE INDEX IF NOT EXISTS idx_prices_asin_ts ON prices(asin, ts);
CREATE TABLE IF NOT EXISTS alerts (
    asin        TEXT NOT NULL,
    ts          REAL NOT NULL,
    price       REAL NOT NULL,
    kind        TEXT NOT NULL
);
"""


class PriceDB:
    def __init__(self, path="prices.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def add_product(self, asin, title=None, url=None):
        self.conn.execute(
            "INSERT OR IGNORE INTO products(asin, title, url, added_at) VALUES (?,?,?,?)",
            (asin, title, url, time.time()),
        )
        if title or url:
            self.conn.execute(
                "UPDATE products SET title=COALESCE(?, title), url=COALESCE(?, url) WHERE asin=?",
                (title, url, asin),
            )
        self.conn.commit()

    def products(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM products ORDER BY added_at")]

    def record_price(self, asin, price, list_price=None, in_stock=True, seller=None, ts=None):
        self.conn.execute(
            "INSERT INTO prices(asin, ts, price, list_price, in_stock, seller) VALUES (?,?,?,?,?,?)",
            (asin, ts if ts is not None else time.time(), price, list_price, int(in_stock), seller),
        )
        self.conn.commit()

    def history(self, asin, since_ts=0.0):
        """All in-stock price points for a product since a time, oldest first."""
        rows = self.conn.execute(
            "SELECT ts, price, list_price FROM prices WHERE asin=? AND ts>=? AND in_stock=1 ORDER BY ts",
            (asin, since_ts),
        )
        return [dict(r) for r in rows]

    def last_alert(self, asin):
        r = self.conn.execute(
            "SELECT * FROM alerts WHERE asin=? ORDER BY ts DESC LIMIT 1", (asin,)
        ).fetchone()
        return dict(r) if r else None

    def record_alert(self, asin, price, kind, ts=None):
        self.conn.execute(
            "INSERT INTO alerts(asin, ts, price, kind) VALUES (?,?,?,?)",
            (asin, ts if ts is not None else time.time(), price, kind),
        )
        self.conn.commit()
