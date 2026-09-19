"""
deal_finder.py v6 — Búsqueda por marca: ropa, zapatos deportivos, tecnología
Estrategia: buscar por keyword sin filtro de departamento,
parsear precios originales y de oferta de los spans de precio de Amazon.
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


# ─── Categorías ───────────────────────────────────────────────────────────────
# (keyword, label, emoji)
CATEGORY_SEARCHES: List[Tuple[str, str, str]] = [
    # Ropa de marca
    ("calvin klein clothing sale",   "Ropa Calvin Klein",   "👗"),
    ("tommy hilfiger clothing sale", "Ropa Tommy Hilfiger", "👔"),
    ("lacoste clothing sale",        "Ropa Lacoste",        "🐊"),
    ("armani exchange clothing",     "Ropa Armani",         "✨"),
    ("ralph lauren clothing sale",   "Ropa Ralph Lauren",   "🏇"),
    # Zapatos deportivos
    ("nike running shoes sale",      "Nike",                "👟"),
    ("adidas running shoes sale",    "Adidas",              "👟"),
    ("new balance shoes sale",       "New Balance",         "👟"),
    ("on cloud shoes sale",          "On Cloud",            "☁️"),
    ("under armour shoes sale",      "Under Armour",        "💪"),
    # Tecnología
    ("apple ipad sale",              "Apple iPad",          "🍎"),
    ("samsung galaxy phone sale",    "Samsung Galaxy",      "📱"),
    ("lenovo laptop sale",           "Lenovo",              "💻"),
    ("sony headphones sale",         "Sony Audio",          "🎧"),
    ("nintendo switch sale",         "Nintendo",            "🎮"),
]


def _num(text) -> Optional[float]:
    if not text: return None
    c = re.sub(r'[^0-9.]', '', str(text).replace(",", ""))
    try: return float(c) if c else None
    except: return None


def _parse_prices(card):
    """
    Extrae (sale_price, orig_price) de los spans de precio de Amazon.
    Amazon muestra:
      - .a-price[data-a-color="base"]   → precio de oferta
      - .a-price[data-a-color="secondary"] → precio tachado (original)
    """
    sale, orig = None, None

    # Precio de oferta: primer span.a-price sin clase secondary
    price_spans = card.select('span.a-price')
    for sp in price_spans:
        color = sp.get('data-a-color', '')
        whole = sp.select_one('.a-price-whole')
        frac  = sp.select_one('.a-price-fraction')
        if whole:
            val_str = whole.get_text(strip=True).replace(',', '').replace('.', '')
            if frac:
                val_str += '.' + frac.get_text(strip=True)
            val = _num(val_str)
            if val is None: continue
            if color == 'secondary':
                if orig is None: orig = val
            else:
                if sale is None: sale = val

    # Fallback: buscar con regex en texto
    if not sale or not orig:
        txt = card.get_text(' ', strip=True)
        prices = re.findall(r'\$\s*([\d,]+\.?\d*)', txt)
        nums = [_num(p) for p in prices if _num(p) and _num(p) < 5000]
        if len(nums) >= 2:
            # Asumimos que primero aparece la oferta, luego el original
            if sale is None: sale = nums[0]
            if orig is None and nums[1] > nums[0]: orig = nums[1]

    return sale, orig


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

            # Título
            title_el = (
                card.select_one('h2 a span') or
                card.select_one('.a-truncate-full.a-offscreen') or
                card.select_one('.a-truncate-cut') or
                card.select_one('[class*="title"] span')
            )
            title = title_el.get_text(strip=True) if title_el else None
            if not title or len(title) < 5:
                return None

            txt = card.get_text(' ', strip=True)

            # Descuento: buscar "-XX%" o "XX% off"
            disc = 0
            pct_m = re.search(r'[-–]\s*(\d+)\s*%', txt)
            if not pct_m:
                pct_m = re.search(r'(\d+)\s*%\s*off', txt, re.I)
            if pct_m:
                disc = int(pct_m.group(1))

            # Precios
            sale_price, orig_price = _parse_prices(card)

            # Calcular descuento desde precios si no lo tenemos
            if disc == 0 and sale_price and orig_price and orig_price > sale_price:
                disc = round((1 - sale_price / orig_price) * 100)

            # Calcular precio original desde descuento si no lo tenemos
            if not orig_price and sale_price and disc > 0:
                orig_price = round(sale_price / (1 - disc / 100), 2)

            if disc < self.min_discount:
                return None

            if not sale_price or not orig_price or orig_price <= sale_price:
                return None

            # Convertir COP a USD si el precio parece estar en COP
            COP_TO_USD = 4100.0
            if sale_price > 5000:
                sale_price = round(sale_price / COP_TO_USD, 2)
            if orig_price > 5000:
                orig_price = round(orig_price / COP_TO_USD, 2)

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
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126"',
                    "sec-ch-ua-platform": '"Windows"',
                }
            )
            page = ctx.new_page()

            for keyword, label, emoji in CATEGORY_SEARCHES:
                if len(deals) >= count * 2:
                    break

                kw_encoded = keyword.replace(' ', '+')
                # URL simple: solo keyword + ordenar por % descuento
                url = (
                    f"https://www.amazon.com/s?k={kw_encoded}"
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
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.4)")
                    time.sleep(0.8)

                    html = page.content()

                    # Detectar CAPTCHA
                    if 'captcha' in html.lower() or 'robot' in html.lower():
                        print(f"      ⚠️  Amazon detectó bot (CAPTCHA). Saltando...")
                        continue

                    soup = BeautifulSoup(html, "html.parser")
                    cards = [c for c in soup.select('[data-asin]')
                             if len(c.get('data-asin', '')) == 10]

                    print(f"      📦 {len(cards)} productos encontrados en página")

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
