"""
deal_finder.py v7 — Usa amazon.com/deals (sin CAPTCHA)
Busca en las páginas oficiales de ofertas de Amazon, filtra ≥30% descuento.
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


# ─── Páginas de ofertas de Amazon (sin CAPTCHA) ──────────────────────────────
DEAL_URLS = [
    "https://www.amazon.com/deals?language=en_US",
    "https://www.amazon.com/gp/goldbox?language=en_US",
    "https://www.amazon.com/deals?deals-widget=%7B%22version%22%3A1%2C%22viewIndex%22%3A0%2C%22presetId%22%3A%22deals-collection-all-deals%22%2C%22sorting%22%3A%22BY_DISCOUNT_PERCENTAGE%22%7D&language=en_US",
    "https://www.amazon.com/gp/goldbox?gb_f_deals1=sortOrder:BY_DISCOUNT_PERCENTAGE&language=en_US",
]

# Categorización automática por palabras clave en el título
CATEGORIES = [
    (["calvin klein", "tommy hilfiger", "lacoste", "armani", "ralph lauren",
      "polo", "dress", "blouse", "shirt", "jeans", "pants", "jacket",
      "sweater", "hoodie", "cardigan", "blazer", "coat", "skirt",
      "clothing", "fashion", "apparel", "levi", "guess", "dkny"],
     "Ropa de Marca", "👗"),
    (["nike", "adidas", "new balance", "on cloud", "under armour", "puma",
      "reebok", "converse", "vans", "skechers", "brooks", "asics",
      "running shoes", "sneakers", "athletic shoes", "boots", "sandals",
      "footwear", "saucony", "hoka"],
     "Zapatos Deportivos", "👟"),
    (["apple", "ipad", "macbook", "airpods", "iphone", "samsung", "galaxy",
      "lenovo", "sony", "nintendo", "switch", "xbox", "playstation",
      "laptop", "tablet", "headphone", "earbuds", "monitor", "keyboard",
      "mouse", "speaker", "camera", "tv", "television", "kindle", "echo",
      "alexa", "fire tv", "roku", "smart watch", "smartwatch"],
     "Tecnología", "💻"),
    (["vacuum", "blender", "coffee", "air fryer", "instant pot", "kitchen",
      "cookware", "bedding", "pillow", "mattress", "furniture", "lamp",
      "storage", "organizer", "home"],
     "Hogar", "🏠"),
    (["protein", "vitamin", "supplement", "gym", "yoga", "fitness",
      "workout", "dumbbell", "resistance band", "health"],
     "Fitness & Salud", "💪"),
]


def _categorize(title: str) -> Tuple[str, str]:
    title_lower = title.lower()
    for keywords, label, emoji in CATEGORIES:
        if any(kw in title_lower for kw in keywords):
            return label, emoji
    return "Amazon Deal", "🛍️"


def _num(text) -> Optional[float]:
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

    def _card_to_deal(self, card):
        try:
            asin = card.get('data-asin', '')
            if len(asin) != 10:
                return None

            # Título
            title_el = (
                card.select_one('a[data-hook="title"]') or
                card.select_one('.a-truncate-full.a-offscreen') or
                card.select_one('.a-truncate-cut') or
                card.select_one('h2 a span') or
                card.select_one('[class*="title"] span')
            )
            title = title_el.get_text(strip=True) if title_el else None
            if not title or len(title) < 5:
                return None

            txt = card.get_text(' ', strip=True)

            # Descuento
            disc = 0
            for pat in [r'[-–]\s*(\d+)\s*%', r'(\d+)\s*%\s*off', r'(\d+)%\s*off']:
                m = re.search(pat, txt, re.I)
                if m:
                    disc = int(m.group(1))
                    break

            # Precios desde spans
            sale_price, orig_price = None, None
            for sp in card.select('span.a-price'):
                color = sp.get('data-a-color', '')
                whole = sp.select_one('.a-price-whole')
                frac  = sp.select_one('.a-price-fraction')
                if whole:
                    val_str = whole.get_text(strip=True).replace(',','').rstrip('.')
                    if frac:
                        val_str += '.' + frac.get_text(strip=True)
                    val = _num(val_str)
                    if val is None or val > 5000: continue
                    if color == 'secondary':
                        if orig_price is None: orig_price = val
                    else:
                        if sale_price is None: sale_price = val

            # Fallback regex
            if not sale_price or not orig_price:
                prices = [_num(p) for p in re.findall(r'\$\s*([\d,]+\.?\d*)', txt)]
                prices = [p for p in prices if p and 0.5 < p < 5000]
                if len(prices) >= 2 and prices[1] > prices[0]:
                    if not sale_price: sale_price = prices[0]
                    if not orig_price: orig_price = prices[1]

            # Calcular descuento si falta
            if disc == 0 and sale_price and orig_price and orig_price > sale_price:
                disc = round((1 - sale_price / orig_price) * 100)
            if not orig_price and sale_price and disc > 0:
                orig_price = round(sale_price / (1 - disc / 100), 2)

            if disc < self.min_discount:
                return None
            if not sale_price or not orig_price or orig_price <= sale_price:
                return None

            # Rating
            rating, count = 0.0, 0
            ra = card.select_one('.a-icon-alt')
            if ra:
                m = re.search(r'(\d+\.?\d*)', ra.get_text())
                if m: rating = float(m.group(1))
            rc_m = re.search(r'([\d,]+)\s*(?:global\s+)?ratings?', txt, re.I)
            if rc_m:
                count = int(rc_m.group(1).replace(',', ''))

            img = card.select_one('img')
            image_url = img.get('src', '') if img else ''

            link = (card.select_one(f'a[href*="/dp/{asin}"]') or
                    card.select_one('a[href*="/dp/"]'))
            href = link['href'] if link else f"/dp/{asin}"
            if '?' in href: href = href.split('?')[0]
            if href.startswith('/'): href = 'https://www.amazon.com' + href

            category, emoji = _categorize(title)

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

        print(f"\n🔍 Buscando top {count} deals con ≥{self.min_discount}% descuento...\n")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                      "--disable-dev-shm-usage", "--window-size=1280,900"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )
            page = ctx.new_page()

            for url in DEAL_URLS:
                if len(deals) >= count * 3:
                    break

                label = "Deals" if "goldbox" not in url else "GoldBox"
                print(f"   🛒 Revisando {label}...")
                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector('[data-asin]', timeout=8000)
                    except:
                        pass
                    time.sleep(random.uniform(2, 3))

                    # Scroll para cargar más deals
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 600)")
                        time.sleep(0.5)

                    html = page.content()

                    if 'captcha' in html.lower() or 'robot' in html.lower():
                        print(f"      ⚠️  CAPTCHA detectado. Saltando...")
                        continue

                    soup = BeautifulSoup(html, "html.parser")
                    cards = [c for c in soup.select('[data-asin]')
                             if len(c.get('data-asin', '')) == 10]
                    print(f"      📦 {len(cards)} productos en página")

                    found = 0
                    for card in cards:
                        deal = self._card_to_deal(card)
                        if deal and deal.asin not in seen:
                            seen.add(deal.asin)
                            deals.append(deal)
                            found += 1
                            print(f"      ✅ [{deal.discount_pct}% OFF] ${deal.sale_price:.2f} — {deal.title[:50]}...")

                    print(f"      → {found} deals ≥{self.min_discount}% encontrados")

                except Exception as e:
                    print(f"      ⚠️  Error: {e}")

            browser.close()

        # Ordenar: primero por descuento, luego rating
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
