"""
deal_finder.py v9 — Slickdeals RSS → Amazon URL real + imagen del producto
- Extrae la URL real de Amazon desde la página de Slickdeals
- Añade tag de afiliado a la URL de Amazon
- Descarga la imagen del producto para Instagram
"""

import io
import re
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


# ─── Marcas objetivo ────────────────────────────────────────────────────────

CATEGORIES = [
    {
        "name": "Ropa de Marca",
        "emoji": "👗",
        "keywords": [
            "calvin klein", "tommy hilfiger", "lacoste", "armani", "ralph lauren",
            "boss", "versace", "gucci", "puma", "champion", "fila", "levis",
        ],
        "search_terms": [
            "calvin klein amazon",
            "tommy hilfiger amazon",
            "lacoste amazon",
            "ralph lauren amazon",
            "armani amazon",
        ],
    },
    {
        "name": "Zapatos Deportivos",
        "emoji": "👟",
        "keywords": [
            "nike", "adidas", "new balance", "on cloud", "under armour",
            "reebok", "asics", "skechers", "hoka", "brooks", "vans", "converse",
        ],
        "search_terms": [
            "nike shoes amazon",
            "adidas shoes amazon",
            "new balance amazon",
            "under armour amazon",
            "hoka amazon",
        ],
    },
    {
        "name": "Tecnología",
        "emoji": "💻",
        "keywords": [
            "apple", "ipad", "iphone", "macbook", "airpods", "samsung", "lenovo",
            "sony", "nintendo", "dell", "hp", "asus", "bose", "jabra", "logitech",
            "kindle", "echo", "fire tv", "pixel",
        ],
        "search_terms": [
            "apple amazon deal",
            "samsung amazon deal",
            "sony amazon deal",
            "nintendo amazon deal",
            "lenovo amazon deal",
        ],
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ─── Dataclass Deal ─────────────────────────────────────────────────────────

@dataclass
class Deal:
    title: str
    original_price: float
    sale_price: float
    discount_pct: int
    product_url: str          # URL real de Amazon
    affiliate_url: str        # Con tag de afiliado
    category: str
    category_emoji: str
    image_url: str = ""       # URL de la imagen del producto
    image_bytes: bytes = field(default=b"", repr=False)
    source: str = "Slickdeals"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def savings(self) -> float:
        return max(0.0, self.original_price - self.sale_price)

    def telegram_caption(self, index: int) -> str:
        title_short = self.title[:120] + ("…" if len(self.title) > 120 else "")
        short_url = self.affiliate_url
        # Acortar URL larga para Telegram
        if len(short_url) > 100:
            asin = _extract_asin(self.product_url)
            if asin:
                short_url = f"https://www.amazon.com/dp/{asin}?tag={_tag_from_url(self.affiliate_url)}"
        lines = [
            f"{self.category_emoji} <b>OFERTA #{index} — {self.category}</b>",
            f"📦 {title_short}",
            "",
            f"💰 <s>${self.original_price:.2f}</s> → <b>${self.sale_price:.2f}</b>",
            f"🔥 <b>{self.discount_pct}% OFF</b> — Ahorras ${self.savings():.2f}",
            "",
            f"🔗 {short_url}",
            "",
            f"✈️ <i>Verifica envío a Colombia en Amazon Global</i>",
        ]
        return "\n".join(lines)

    # Mantener compatibilidad con run_pipeline.py antiguo
    def telegram_msg(self, index: int) -> str:
        return self.telegram_caption(index)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "original_price": self.original_price,
            "sale_price": self.sale_price,
            "discount_pct": self.discount_pct,
            "product_url": self.product_url,
            "affiliate_url": self.affiliate_url,
            "image_url": self.image_url,
            "category": self.category,
            "category_emoji": self.category_emoji,
            "source": self.source,
            "timestamp": self.timestamp,
        }


# ─── Utilidades ─────────────────────────────────────────────────────────────

def _extract_asin(url: str) -> str:
    for pat in [r"/dp/([A-Z0-9]{10})", r"/gp/product/([A-Z0-9]{10})", r"asin=([A-Z0-9]{10})"]:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return ""


def _tag_from_url(url: str) -> str:
    try:
        params = parse_qs(urlparse(url).query)
        return params.get("tag", [""])[0]
    except Exception:
        return ""


def add_affiliate_tag(url: str, tag: str) -> str:
    """Agrega o reemplaza el tag de afiliado en una URL de Amazon."""
    try:
        parsed = urlparse(url)
        if "amazon.com" not in parsed.netloc:
            return url
        params = parse_qs(parsed.query, keep_blank_values=True)
        params["tag"] = [tag]
        new_query = urlencode({k: v[0] for k, v in params.items()})
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def extract_prices(text: str):
    """Extrae (precio_orig, precio_venta, descuento_pct) de texto o (None,None,None)."""
    text = text.replace(",", "")
    prices = [float(p) for p in re.findall(r"\$(\d+(?:\.\d+)?)", text)]
    pct_m  = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)

    if len(prices) >= 2:
        prices_sorted = sorted(set(prices), reverse=True)
        if len(prices_sorted) >= 2:
            orig, sale = prices_sorted[0], prices_sorted[1]
            if orig > 0 and 0 < sale < orig:
                disc = int(round((1 - sale / orig) * 100))
                if 5 <= disc <= 95:
                    return orig, sale, disc

    if len(prices) == 1 and pct_m:
        sale = prices[0]
        disc = int(pct_m.group(1))
        if 5 <= disc <= 95 and sale > 0:
            return round(sale / (1 - disc / 100), 2), sale, disc

    return None, None, None


# ─── Clase principal ─────────────────────────────────────────────────────────

class AmazonDealFinder:
    SLICKDEALS_BASE = "https://slickdeals.net/newsearch.php"

    def __init__(self, affiliate_tag: str, min_discount: int = 30):
        self.affiliate_tag = affiliate_tag
        self.min_discount  = min_discount
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ── RSS ──────────────────────────────────────────────────────────────────

    def _fetch_rss(self, term: str) -> list:
        params = {
            "src": "SearchBarV2", "q": term,
            "searcharea": "deals", "searchin": "first", "rss": "1",
        }
        try:
            feed = feedparser.parse(
                self.SLICKDEALS_BASE + "?" + urlencode(params)
            )
            return feed.entries
        except Exception as e:
            print(f"   ⚠️  RSS error ({term}): {e}")
            return []

    # ── URL de Amazon ─────────────────────────────────────────────────────────

    def _amazon_url_from_slickdeals(self, sd_url: str) -> str:
        """
        Intenta obtener la URL real de Amazon desde la página de Slickdeals.
        1. Sigue el redirect HTTP (a veces ya lleva a Amazon)
        2. Parsea el HTML y busca el link "Go to Deal"
        3. Regex sobre el HTML completo
        """
        try:
            resp = self.session.get(sd_url, timeout=12, allow_redirects=True)
            final_url = resp.url

            # Redirect directo a Amazon
            if "amazon.com" in final_url:
                return final_url

            # Buscar en el HTML el botón "Go to Deal" o link de Amazon
            soup = BeautifulSoup(resp.text, "html.parser")

            # Candidatos: links con /dp/ o /gp/product/
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "amazon.com" in href and (
                    "/dp/" in href or "/gp/product/" in href
                ):
                    return href

            # Regex más amplio sobre todo el HTML
            matches = re.findall(
                r'https?://(?:www\.)?amazon\.com/[^\s"\'<>)\]]+', resp.text
            )
            for m in matches:
                if "/dp/" in m or "/gp/product/" in m:
                    return m.rstrip(".,;)")

        except Exception as e:
            print(f"      ⚠️  No se pudo resolver Amazon URL: {e}")

        return ""  # no encontrado

    # ── Imagen del producto ───────────────────────────────────────────────────

    def _get_product_image(self, amazon_url: str) -> tuple[str, bytes]:
        """Retorna (image_url, image_bytes). Intenta CDN primero, luego scrape."""
        asin = _extract_asin(amazon_url)
        if not asin:
            return "", b""

        # CDN directo (sin scraping)
        cdn_url = f"https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg"
        try:
            r = self.session.get(cdn_url, timeout=8)
            if r.status_code == 200 and len(r.content) > 2000:
                return cdn_url, r.content
        except Exception:
            pass

        # Scraping de la página del producto
        try:
            r = self.session.get(amazon_url, timeout=12)
            soup = BeautifulSoup(r.text, "html.parser")

            img_tag = (
                soup.find("img", id="landingImage")
                or soup.find("img", id="imgBlkFront")
                or soup.find("img", {"data-old-hires": True})
            )
            img_url = ""
            if img_tag:
                img_url = (
                    img_tag.get("data-old-hires")
                    or img_tag.get("data-src")
                    or img_tag.get("src", "")
                )

            if img_url and img_url.startswith("http"):
                r2 = self.session.get(img_url, timeout=8)
                if r2.status_code == 200:
                    return img_url, r2.content
        except Exception:
            pass

        return "", b""

    # ── Pipeline principal ────────────────────────────────────────────────────

    def find_deals(self, count: int = 10) -> list:
        print("🔍 Buscando ofertas en Slickdeals RSS...")
        deals = []
        seen  = set()

        for cat in CATEGORIES:
            cat_deals = []

            for term in cat["search_terms"]:
                print(f"   Buscando: {term}")
                entries = self._fetch_rss(term)
                time.sleep(0.4)

                for entry in entries:
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", "")
                    sd_link = entry.get("link", "")

                    key = title.lower()
                    if key in seen or not title:
                        continue

                    full_text = f"{title} {summary} {sd_link}".lower()
                    if "amazon" not in full_text:
                        continue

                    if not any(kw in title.lower() for kw in cat["keywords"]):
                        continue

                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None or disc < self.min_discount:
                        continue

                    # ── Obtener URL real de Amazon ──
                    print(f"      → Resolviendo URL Amazon para: {title[:50]}…")
                    amazon_url = ""

                    # 1. Intentar en el texto de la entrada primero (rápido)
                    for pat in [r'https?://(?:www\.)?amazon\.com/dp/[^\s"\'<>)]+',
                                r'https?://(?:www\.)?amazon\.com/gp/product/[^\s"\'<>)]+'
                                ]:
                        ms = re.findall(pat, f"{title} {summary}")
                        if ms:
                            amazon_url = ms[0].rstrip(".,;)")
                            break

                    # 2. Si no, ir a la página de Slickdeals
                    if not amazon_url:
                        amazon_url = self._amazon_url_from_slickdeals(sd_link)
                        time.sleep(0.5)

                    if not amazon_url:
                        print(f"         ⚠️  Sin URL Amazon, omitiendo")
                        continue

                    affiliate_url = add_affiliate_tag(amazon_url, self.affiliate_tag)

                    # ── Imagen del producto ──
                    img_url, img_bytes = self._get_product_image(amazon_url)
                    if img_url:
                        print(f"         🖼️  Imagen obtenida ({len(img_bytes)//1024} KB)")
                    else:
                        print(f"         ⚠️  Sin imagen")
                    time.sleep(0.3)

                    deal = Deal(
                        title          = title,
                        original_price = orig if orig else sale,
                        sale_price     = sale if sale else 0.0,
                        discount_pct   = disc,
                        product_url    = amazon_url,
                        affiliate_url  = affiliate_url,
                        image_url      = img_url,
                        image_bytes    = img_bytes,
                        category       = cat["name"],
                        category_emoji = cat["emoji"],
                    )
                    cat_deals.append(deal)
                    seen.add(key)

                if len(cat_deals) >= 4:
                    break

            cat_deals.sort(key=lambda d: d.discount_pct, reverse=True)
            deals.extend(cat_deals[:max(1, count // len(CATEGORIES) + 1)])

        # Relleno genérico si faltan
        if len(deals) < count:
            print("   Buscando deals generales de Amazon…")
            for term in ["amazon deal 50% off", "amazon clearance sale"]:
                for entry in self._fetch_rss(term):
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", "")
                    sd_link = entry.get("link", "")
                    key     = title.lower()
                    if key in seen or not title:
                        continue
                    if "amazon" not in f"{title} {summary} {sd_link}".lower():
                        continue
                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None or disc < self.min_discount:
                        continue
                    amazon_url = self._amazon_url_from_slickdeals(sd_link)
                    if not amazon_url:
                        continue
                    affiliate_url = add_affiliate_tag(amazon_url, self.affiliate_tag)
                    img_url, img_bytes = self._get_product_image(amazon_url)
                    deals.append(Deal(
                        title=title, original_price=orig or sale, sale_price=sale or 0.0,
                        discount_pct=disc, product_url=amazon_url,
                        affiliate_url=affiliate_url, image_url=img_url,
                        image_bytes=img_bytes, category="Oferta General", category_emoji="🛒",
                    ))
                    seen.add(key)
                    if len(deals) >= count:
                        break
                if len(deals) >= count:
                    break

        deals.sort(key=lambda d: d.discount_pct, reverse=True)
        result = deals[:count]
        print(f"\n✅ {len(result)} deals encontrados con URLs de Amazon")
        return result
