"""Decides whether a price is a real deal, a fake discount, or a likely pricing error.

Core idea: never trust Amazon's crossed-out "was" price. Compare the current price
with what the product has *actually* sold for recently (the reference price).
"""
from dataclasses import dataclass, field
from statistics import median

DAY = 86400.0


@dataclass
class Settings:
    real_deal_pct: float = 0.15        # at least 15% below the reference price
    big_drop_pct: float = 0.20         # 20%+ drop since the previous check
    error_pct: float = 0.50            # 50%+ below reference = likely pricing error
    inflation_pct: float = 0.15        # price raised 15%+ above normal before a "sale"
    inflation_window_days: int = 14
    min_history_days: float = 3.0      # need this much history before judging


@dataclass
class Verdict:
    price: float
    reference: float | None = None     # the "normal" price (median)
    median_30: float | None = None
    median_90: float | None = None
    low_90: float | None = None
    high_90: float | None = None
    real_discount: float | None = None     # vs reference (0.25 = 25% off)
    claimed_discount: float | None = None  # vs Amazon's list price
    tags: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def is_alert(self):
        return any(t in self.tags for t in ("REAL_DEAL", "PRICE_ERROR", "BIG_DROP"))

    @property
    def kind(self):
        for t in ("PRICE_ERROR", "REAL_DEAL", "BIG_DROP"):
            if t in self.tags:
                return t
        return None


def _median(points):
    return median(p["price"] for p in points) if points else None


def analyze(history, now, price, list_price=None, s: Settings = Settings()):
    """history: past price points (dicts with ts, price), NOT including the current one."""
    v = Verdict(price=price)
    h90 = [p for p in history if p["ts"] >= now - 90 * DAY]
    h30 = [p for p in h90 if p["ts"] >= now - 30 * DAY]
    v.median_30, v.median_90 = _median(h30), _median(h90)
    if h90:
        v.low_90 = min(p["price"] for p in h90)
        v.high_90 = max(p["price"] for p in h90)

    if list_price and list_price > price:
        v.claimed_discount = 1 - price / list_price

    span_days = (now - h90[0]["ts"]) / DAY if h90 else 0
    if span_days < s.min_history_days:
        v.tags.append("LEARNING")
        v.notes.append(f"Only {span_days:.1f} days of history; still learning the normal price.")
        return v

    # Use the 90-day median as the "normal" price, but if the price has settled at a
    # new level in the last 30 days, the 30-day median is more honest.
    v.reference = min(v.median_90, v.median_30) if v.median_30 else v.median_90
    v.real_discount = 1 - price / v.reference

    # Previous check: sudden drops. Only counts if it also lands below the normal price,
    # otherwise it's just a temporary price hike being undone.
    prev = history[-1]["price"] if history else None
    if prev and price <= prev * (1 - s.big_drop_pct):
        msg = f"Dropped {1 - price / prev:.0%} since the last check ({prev:g} -> {price:g})."
        if v.real_discount >= 0.05:
            v.tags.append("BIG_DROP")
        else:
            msg += " But that's only back to its usual price."
        v.notes.append(msg)

    # Fake discount #1: the crossed-out price is inflated
    if v.claimed_discount and v.claimed_discount >= 0.10:
        if v.real_discount < s.real_deal_pct / 2:
            v.tags.append("FAKE_LIST_PRICE")
            v.notes.append(
                f"Amazon claims {v.claimed_discount:.0%} off, but it's only "
                f"{max(v.real_discount, 0):.0%} below its usual price."
            )
        if v.high_90 and list_price > v.high_90 * 1.05:
            v.notes.append(f"The 'was' price {list_price:g} is higher than any price seen in 90 days.")

    # Fake discount #2: price was raised shortly before the "sale"
    recent = [p for p in history if p["ts"] >= now - s.inflation_window_days * DAY]
    if recent:
        recent_high = max(p["price"] for p in recent)
        if recent_high >= v.reference * (1 + s.inflation_pct) and v.real_discount < s.real_deal_pct:
            v.tags.append("INFLATED_BEFORE_SALE")
            v.notes.append(
                f"Price was pushed up to {recent_high:g} recently, so this 'drop' only brings it "
                f"back near normal ({v.reference:g})."
            )

    # Real opportunities
    if v.real_discount >= s.error_pct:
        v.tags.append("PRICE_ERROR")
        v.notes.append(f"{v.real_discount:.0%} below normal: possibly a pricing error. Act fast.")
    elif v.real_discount >= s.real_deal_pct:
        v.tags.append("REAL_DEAL")
        v.notes.append(f"{v.real_discount:.0%} below the usual price of {v.reference:g}.")
    if v.low_90 is not None and price < v.low_90:
        v.tags.append("LOWEST_90D")
        v.notes.append(f"Lowest price in 90 days (previous low {v.low_90:g}).")
    return v
