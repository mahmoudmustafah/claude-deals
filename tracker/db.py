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
CREATE TABLE IF NOT EXISTS deals (
    asin          TEXT PRIMARY KEY,     -- one row per product's current/most recent deal
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    price         REAL NOT NULL,
    list_price    REAL,
    typical_price REAL,                 -- Amazon's own "Typical price" / "Was" label, if shown
    badge         TEXT,                 -- e.g. "Limited time deal", "Lightning deal"
    ends_at       REAL,                 -- only when Amazon shows a countdown
    claimed_by    REAL,                 -- % of Lightning Deal stock already claimed
    source        TEXT,                 -- which scan found it (deals page / search term)
    ended_at      REAL                  -- NULL while the deal is still running
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    asin        TEXT NOT NULL,
    ts          REAL NOT NULL,
    price       REAL NOT NULL,
    kind        TEXT NOT NULL
);
"""


class PriceDB:
    def __init__(self, path="prices.db"):
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(products)")}
        for col, decl in (("watched", "INTEGER DEFAULT 1"), ("image", "TEXT"), ("category", "TEXT")):
            if col not in cols:  # upgrade older databases
                self.conn.execute(f"ALTER TABLE products ADD COLUMN {col} {decl}")
        self.conn.commit()

    def add_product(self, asin, title=None, url=None, watched=True):
        """watched=True: on your watch list. watched=False: just seen by the deals scanner."""
        self.conn.execute(
            "INSERT OR IGNORE INTO products(asin, title, url, added_at, watched) VALUES (?,?,?,?,?)",
            (asin, title, url, time.time(), int(watched)),
        )
        if watched:
            self.conn.execute("UPDATE products SET watched=1 WHERE asin=?", (asin,))
        if title or url:
            self.conn.execute(
                "UPDATE products SET title=COALESCE(?, title), url=COALESCE(?, url) WHERE asin=?",
                (title, url, asin),
            )
        self.conn.commit()

    def remove_product(self, asin):
        """Take a product off the watch list (its price history is kept)."""
        self.conn.execute("UPDATE products SET watched=0 WHERE asin=?", (asin,))
        self.conn.commit()

    def seen_product(self, asin, title=None, url=None, image=None, category=None):
        """Record a product the scanner saw, without adding it to the watch list."""
        self.conn.execute(
            "INSERT OR IGNORE INTO products(asin, title, url, added_at, watched) VALUES (?,?,?,?,0)",
            (asin, title, url, time.time()))
        self.conn.execute(
            "UPDATE products SET title=COALESCE(title, ?), url=COALESCE(url, ?), "
            "image=COALESCE(?, image), category=COALESCE(category, ?) WHERE asin=?",
            (title, url, image, category, asin))

    def product(self, asin):
        r = self.conn.execute("SELECT * FROM products WHERE asin=?", (asin,)).fetchone()
        return dict(r) if r else None

    def upsert_deal(self, asin, ts, price, list_price=None, typical_price=None, badge=None,
                    ends_at=None, claimed_by=None, source=None):
        cur = self.conn.execute("SELECT * FROM deals WHERE asin=?", (asin,)).fetchone()
        if cur and cur["ended_at"] is None:
            self.conn.execute(
                "UPDATE deals SET last_seen=?, price=?, list_price=?, typical_price=COALESCE(?, typical_price), "
                "badge=COALESCE(?, badge), ends_at=COALESCE(?, ends_at), claimed_by=COALESCE(?, claimed_by), "
                "source=COALESCE(source, ?) WHERE asin=?",
                (ts, price, list_price, typical_price, badge, ends_at, claimed_by, source, asin))
        else:  # new deal (or the old one ended and a new one started)
            self.conn.execute(
                "INSERT OR REPLACE INTO deals(asin, first_seen, last_seen, price, list_price, typical_price, "
                "badge, ends_at, claimed_by, source, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
                (asin, ts, ts, price, list_price, typical_price, badge, ends_at, claimed_by, source))

    def deal(self, asin):
        r = self.conn.execute("SELECT * FROM deals WHERE asin=?", (asin,)).fetchone()
        return dict(r) if r else None

    def end_deal(self, asin, ts):
        self.conn.execute(
            "UPDATE deals SET ended_at = CASE WHEN ends_at IS NOT NULL AND ends_at < ? THEN ends_at ELSE ? END "
            "WHERE asin=? AND ended_at IS NULL", (ts, ts, asin))

    def end_stale_deals(self, now, stale_after, expired_only=False):
        """A deal ends when its countdown passes, or when no scan has seen it for a while.
        expired_only: only end deals whose countdown passed (used when the scan was blocked,
        so "not seen" just means we couldn't look)."""
        self.conn.execute(
            "UPDATE deals SET ended_at = ends_at WHERE ended_at IS NULL AND ends_at IS NOT NULL "
            "AND ends_at < ?", (now,))
        if not expired_only:
            self.conn.execute(
                "UPDATE deals SET ended_at = last_seen WHERE ended_at IS NULL AND last_seen < ?",
                (now - stale_after,))
        self.conn.commit()

    def reopen_unconfirmed_deals(self, now):
        """Undo endings that only happened because scans were blocked: deals with no passed
        countdown that were ended for not being seen go back to active."""
        cur = self.conn.execute(
            "UPDATE deals SET ended_at = NULL WHERE ended_at IS NOT NULL AND ended_at = last_seen "
            "AND (ends_at IS NULL OR ends_at > ?)", (now,))
        self.conn.commit()
        return cur.rowcount

    def deals(self, since_ended=0.0):
        rows = self.conn.execute(
            "SELECT d.*, p.title, p.url, p.image, p.category, p.watched FROM deals d "
            "LEFT JOIN products p USING(asin) WHERE d.ended_at IS NULL OR d.ended_at >= ? "
            "ORDER BY d.first_seen DESC", (since_ended,))
        return [dict(r) for r in rows]

    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, str(value)))
        self.conn.commit()

    def get_meta(self, key, default=None):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def count_products(self):
        return self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    def commit(self):
        self.conn.commit()

    def recent_alerts(self, limit=20):
        rows = self.conn.execute(
            "SELECT a.*, p.title, p.url FROM alerts a LEFT JOIN products p USING(asin) "
            "ORDER BY a.ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def last_check(self):
        r = self.conn.execute("SELECT MAX(ts) FROM prices").fetchone()
        return r[0]

    def products(self):
        """Your watch list."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM products WHERE watched=1 ORDER BY added_at")]

    def record_price(self, asin, price, list_price=None, in_stock=True, seller=None, ts=None,
                     commit=True):
        self.conn.execute(
            "INSERT INTO prices(asin, ts, price, list_price, in_stock, seller) VALUES (?,?,?,?,?,?)",
            (asin, ts if ts is not None else time.time(), price, list_price, int(in_stock), seller),
        )
        if commit:
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
