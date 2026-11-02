"""Where prices come from. Each source returns a PriceQuote for an ASIN.

The real source (Amazon Creators API or Keepa) gets added here once chosen.
DemoSource produces fake prices so you can try the whole system today.
"""
import random
from dataclasses import dataclass


@dataclass
class PriceQuote:
    asin: str
    price: float
    list_price: float | None = None
    in_stock: bool = True
    title: str | None = None
    url: str | None = None
    seller: str | None = None


class PriceSource:
    def get_prices(self, asins):
        """Return {asin: PriceQuote} for the asins it could fetch."""
        raise NotImplementedError


class DemoSource(PriceSource):
    """Random-walk prices around a base, with occasional sales. For testing only."""

    def __init__(self, base_prices, seed=None):
        self.current = dict(base_prices)
        self.base = dict(base_prices)
        self.rng = random.Random(seed)

    def get_prices(self, asins):
        out = {}
        for asin in asins:
            base = self.base.setdefault(asin, 1000.0)
            p = self.current.get(asin, base) * self.rng.uniform(0.98, 1.02)
            if self.rng.random() < 0.02:
                p = base * self.rng.uniform(0.6, 0.85)   # a sale
            p = min(max(p, base * 0.4), base * 1.4)
            self.current[asin] = p
            out[asin] = PriceQuote(asin, round(p, 2), list_price=round(base * 1.3, 2),
                                   title=f"Demo product {asin}",
                                   url=f"https://www.amazon.eg/dp/{asin}")
        return out
