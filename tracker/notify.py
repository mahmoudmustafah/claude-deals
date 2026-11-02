"""Telegram alerts via the Telegram Bot API (no extra libraries needed)."""
import json
import urllib.parse
import urllib.request

from .amazon_page import SSL_CONTEXT


def format_alert(product, v, currency="EGP"):
    title = product.get("title") or product["asin"]
    head = {"PRICE_ERROR": "🚨 Possible pricing error", "REAL_DEAL": "✅ Real deal",
            "BIG_DROP": "📉 Big price drop"}.get(v.kind, "Price update")
    lines = [f"{head}: {title}", f"Now: {v.price:,.2f} {currency}"]
    if v.reference:
        lines.append(f"Usual price: {v.reference:,.2f} {currency} ({v.real_discount:.0%} off)")
    if v.low_90:
        lines.append(f"90-day range: {v.low_90:,.2f} to {v.high_90:,.2f}")
    lines += [f"• {n}" for n in v.notes]
    if product.get("url"):
        lines.append(product["url"])
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text):
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen(self.url, data=data, timeout=15, context=SSL_CONTEXT) as r:
            return json.load(r)


class ConsoleNotifier:
    def send(self, text):
        print("\n" + text + "\n" + "-" * 40)
