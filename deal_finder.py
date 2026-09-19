"""
deal_finder.py v5 — Búsqueda por categorías: ropa de marca, zapatos deportivos, tecnología
Filtra automáticamente ofertas con ≥30% de descuento.
"""
import json, re, sys, time, random
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple

@dataclass
class Deal:
    title: str
    original_price: float
    sale_price: float
    discount_pct: int
    rating: float
    rating_count: int
    image_url: str
    product_url: str
    affiliate_url: str
    asin: str
    category: str = "General"
    category_emoji: str = "🛍️"
    timestamp: str = ""

    def __post_init__(self):
        self.timestamp = datetime.now().isoformat()

    def savings(self):
        return round(self.original_price - self.sale_price, 2)

    def caption_es(self):
        return "\n".join([
            f"{self.category_emoji} ¡OFERTA! {self.category}", "",
            f"🛍️ {self.title[:80]}{'...' if len(self.title)>80 else ''}", "",
            f"💰 Precio normal: ${self.original_price:.2f}",
            f"🔥 HOY SOLO: ${self.sale_price:.2f}",
            f"✅ ¡Ahorras ${self.savings():.2f} ({self.discount_pct}% OFF)!", "",
            f"⭐ {self.rating}/5 ({self.rating_count:,} reseñas)", "",
            "🔗 Link en BIO para comprar", "",
            "#deals #offersoftheday #amazon #amazondeal #amazonfind "
            "#discountpartner #shopping #sale #bargain #deal "
            "#savemoney #offer #onlineshopping #amazonfinds #descuentos "
            "#ofertas #compras #ahorra #mejoresprecios"
        ])

    def telegram_msg(self, index: int) -> str:
        return (
            f"{self.category_emoji} <b>OFERTA #{index} — {self.category}</b>\n"
            f"📦 {self.title[:100]}{'...' if len(self.title)>100 else ''}\n\n"
            f"💰 <s>${self.original_price:.2f}</s> → <b>${self.sale_price:.2f}</b>\n"
            f"🔥 <b>{self.discount_pct}% OFF</b> — Ahorras ${self.savings():.2f}\n"
            f"⭐ {self.rating}/5 ({self.rating_count:,} reseñas)\n\n"
            f"🔗 {self.affiliate_url}"
        )

    def to_dict(self):
        d = asdict(self)
        d["savings"] = self.savings()
        d["caption_es"] = self.caption_es()
        return d


# ─── Categorías de búsqueda ──────────────────────────────────────────────────
CATEGORY_SEARCHES: List[Tuple[str, str, str, str]] = [
    # Ropa de marca
    ("calvin klein",        "fashion",      "Ropa Calvin Klein",    "👗"),
    ("tommy hilfiger",      "fashion",      "Ropa Tommy Hilfiger",  "👔"),
    ("lacoste",             "fashion",      "Ropa Lacoste",         "🐊"),
    ("armani exchange",     "fashion",      "Ropa Armani",          "✨"),
    ("ralph lauren",        "fashion",      "Ropa Ralph Lauren",    "🏇"),
    # Zapatos deportivos
    ("nike shoes",          "shoes",        "Nike",                 "👟"),
    ("adidas shoes",        "shoes",        "Adidas",               "👟"),
    ("new balance shoes",   "shoes",        "New Balance",          "👟"),
    ("on cloud shoes",      "shoes",        "On Cloud",             "☁️"),
    ("under armour shoes",  "shoes",        "Under Armour",         "💪"),
    # Tecnología
    ("apple ipad macbook",  "electronics",  "Apple",                "🍎"),
    ("samsung galaxy",      "electronics",  "Samsung",              "📱"),
    ("lenovo laptop",       "computers",    "Lenovo",               "💻"),
    ("sony headphones",     "electronics",  "Sony Audio",           "🎧"),
    ("nintendo",            "electronics",  "Nintendo",             "🎮"),
]

DISCOUNT_FILTER = "p_n_pct-off-with-tax:2671309011|2671310011|2671311011|2671312011|2671313011"


def _num(text):
    if not text: return None
    c = re.sub(r'[^0-9.]', '', str(text).replace(",", ""))
    try: return float(c) if c else None
    except: return None


class AmazonDealFinder:
    def __init__(self, affiliate_tag, min_discount=30):
        self.tag = affiliate_tag
        self.min_discount = min_discount

    def _aff(self, asin):
        return f"https://www.amazon.com/dp/{asin}?tag={self.tag}"

    def _card_to_deal(self, card, category: str, emoji: str):
        try:
            asin = card.get('data-asin', '')
            if len(asin) != 10:
                return None

            title_el = (card.select_one('.a-truncate-full.a-offscreen') or
                        card.select_one('.a-truncate-cut') or
                        card.select_one('[class*="title"] span'))
            title = title_el.get_text(strip=True) if title_el else None
            if not title or len(title) < 5:
                return None

            txt = card.get_text(' ', strip=True)

            pct_m = re.search(r'(\d+)%\s*off', txt, re.I)
            disc = int(pct_m.group(1)) if pct_m else 0
            if disc < self.min_discount:
                return None

            deal_m = re.search(r'Deal Price[:\s]+(?:[A-Z]{2,3}\s+)?([\d,]+\.?\d*)', txt, re.I)
            sale_price = _num(deal_m.group(1)) if deal_m else None

            if not sale_price:
                p_m = re.search(r'(?:\$|USD)\s*([\d,]+\.?\d*)', txt)
                sale_price = _num(p_m.group(1)) if p_m else None

            list_m = re.search(r'List:\s*(?:List:\s*)?(?:[A-Z]{2,3}\s+)?([\d,]+\.?\d*)', txt, re.I)
            orig_price = _num(list_m.group(1)) if list_m else None

            if not orig_price and sale_price and disc > 0:
                orig_price = round(sale_price / (1 - disc / 100), 2)

            COP_TO_USD = 4100.0
            if sale_price and sale_price > 5000:
                sale_price = round(sale_price / COP_TO_USD, 2)
            if orig_price and orig_price > 5000:
                orig_price = round(orig_price / COP_TO_USD, 2)

            if not sale_price or not orig_price or orig_price <= sale_price:
                return None

            rating, count = 0.0, 0
            ra = card.select_one('.a-icon-alt')
            if ra:
                m = re.search(r'(\d+\.?\d*)', ra.get_text())
                if m: rating = float(m.group(1))

            rc_m = re.search(r'([\d,]+)\s*global ratings', txt, re.I)
            if rc_m:
                count = int(rc_m.group(1).replace(',', ''))

            img = card.select_one('img')
            image_url = img.get('src', '') if img else ''

            link = card.select_one(f'a[href*="/dp/{asin}"]') or card.select_one('a[href*="/dp/"]')
            href = link['href'] if link else f"/dp/{asin}"
            if href.startswith('/'): href = 'https://www.amazon.com' + href

            return Deal(
                title=title, original_price=orig_price, sale_price=sale_price,
                discount_pct=disc, rating=rating, rating_count=count,
                image_url=image_url, product_url=href,
                affiliate_url=self._aff(asin), asin=asin,
                category=category, category_emoji=emoji
            )
        except Exception:
            return None

    def find_deals(self, count=10) -> List[Deal]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "-q"])
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
            from playwright.sync_api import sync_playwright

        from bs4 import BeautifulSoup

        deals, seen = [], set()
        per_category = max(1, count // len(CATEGORY_SEARCHES) + 1)

        print(f"\n🔍 Buscando top {count} deals con ≥{self.min_discount}% descuento...\n")
        print("   Categorías: Ropa de marca | Zapatos deportivos | Tecnología\n")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--window-size=1280,900",
                ]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126"',
                    "sec-ch-ua-platform": '"Windows"',
                }
            )
            page = ctx.new_page()

            for keyword, dept, label, emoji in CATEGORY_SEARCHES:
                if len(deals) >= count * 2:
                    break
                kw_encoded = keyword.replace(' ', '+')
                url = (
                    f"https://www.amazon.com/s?k={kw_encoded}"
                    f"&i={dept}"
                    f"&rh={DISCOUNT_FILTER.replace('|', '%7C')}"
                    f"&s=price-asc-rank"
                    f"&language=en_US"
                )
                print(f"   {emoji} Buscando {label}...")
                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector('[data-asin]', timeout=8000)
                    except:
                        pass
                    time.sleep(random.uniform(1.5, 2.5))
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                    time.sleep(1)

                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    cards = [c for c in soup.select('[data-asin]') if len(c.get('data-asin', '')) == 10]

                    found = 0
                    for card in cards:
                        if found >= per_category:
                            break
                        deal = self._card_to_deal(card, label, emoji)
                        if deal and deal.asin not in seen:
                            seen.add(deal.asin)
                            deals.append(deal)
                            found += 1
                            print(f"      ✅ [{deal.discount_pct}% OFF] ${deal.sale_price:.2f} — {deal.title[:50]}...")

                    if found == 0:
                        print(f"      (sin ofertas ≥{self.min_discount}% en esta búsqueda)")

                except Exception as e:
                    print(f"      ⚠️  Error: {e}")

            browser.close()

        deals.sort(key=lambda d: (d.discount_pct, d.rating), reverse=True)
        top = deals[:count]

        print(f"\n✨ Top {len(top)} deals seleccionados:\n")
        for i, d in enumerate(top, 1):
            print(f"   {i}. {d.category_emoji} [{d.category}] {d.discount_pct}% OFF")
            print(f"      {d.title[:60]}")
            print(f"      ${d.sale_price:.2f} (antes ${d.original_price:.2f})")
            print(f"      🔗 {d.affiliate_url}\n")

        return top


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--min-discount", type=int, default=30)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--output", default="deals_output.json")
    args = parser.parse_args()

    finder = AmazonDealFinder(affiliate_tag=args.tag, min_discount=args.min_discount)
    deals = finder.find_deals(count=args.count)
    if not deals:
        print("⚠️  Sin deals.")
        sys.exit(1)

    out = {"generated_at": datetime.now().isoformat(), "deals": [d.to_dict() for d in deals]}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"💾 Guardado en: {args.output}")
