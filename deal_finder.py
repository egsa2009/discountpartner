"""
deal_finder.py v8 — Slickdeals RSS (sin CAPTCHA de Amazon)
Busca ofertas de Amazon vía Slickdeals RSS y agrega tag de afiliado.
"""

import re
import time
import feedparser
import requests
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
        "search_terms": ["calvin klein amazon", "tommy hilfiger amazon",
                         "lacoste amazon", "ralph lauren amazon", "armani amazon"],
    },
    {
        "name": "Zapatos Deportivos",
        "emoji": "👟",
        "keywords": [
            "nike", "adidas", "new balance", "on cloud", "under armour",
            "reebok", "asics", "skechers", "hoka", "brooks", "vans", "converse",
        ],
        "search_terms": ["nike amazon", "adidas amazon", "new balance amazon",
                         "under armour amazon", "hoka amazon"],
    },
    {
        "name": "Tecnología",
        "emoji": "💻",
        "keywords": [
            "apple", "ipad", "iphone", "macbook", "airpods", "samsung", "lenovo",
            "sony", "nintendo", "dell", "hp", "asus", "bose", "jabra", "logitech",
            "kindle", "echo", "fire tv", "pixel",
        ],
        "search_terms": ["apple amazon", "samsung amazon", "sony amazon",
                         "nintendo amazon", "lenovo amazon", "bose amazon"],
    },
]


# ─── Dataclass Deal ─────────────────────────────────────────────────────────

@dataclass
class Deal:
    title: str
    original_price: float
    sale_price: float
    discount_pct: int
    product_url: str
    affiliate_url: str
    category: str
    category_emoji: str
    source: str = "Slickdeals"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def savings(self) -> float:
        return max(0.0, self.original_price - self.sale_price)

    def telegram_msg(self, index: int) -> str:
        title_short = self.title[:120] + ("…" if len(self.title) > 120 else "")
        lines = [
            f"{self.category_emoji} <b>OFERTA #{index} — {self.category}</b>",
            f"📦 {title_short}",
            "",
            f"💰 <s>${self.original_price:.2f}</s> → <b>${self.sale_price:.2f}</b>",
            f"🔥 <b>{self.discount_pct}% OFF</b> — Ahorras ${self.savings():.2f}",
            "",
            f"🔗 {self.affiliate_url}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "original_price": self.original_price,
            "sale_price": self.sale_price,
            "discount_pct": self.discount_pct,
            "product_url": self.product_url,
            "affiliate_url": self.affiliate_url,
            "category": self.category,
            "category_emoji": self.category_emoji,
            "source": self.source,
            "timestamp": self.timestamp,
        }


# ─── Utilidades ─────────────────────────────────────────────────────────────

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


def resolve_final_url(url: str, timeout: int = 8) -> str:
    """Sigue redirects para obtener la URL final (busca amazon.com)."""
    try:
        resp = requests.get(
            url, allow_redirects=True, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        final = resp.url
        if "amazon.com" in final:
            return final
        # Buscar link de Amazon en el HTML
        amazon_links = re.findall(r'https?://[^"\s\'<>]*amazon\.com/[^"\s\'<>]{10,}', resp.text)
        if amazon_links:
            return amazon_links[0]
        return final
    except Exception:
        return url


def extract_prices(text: str):
    """
    Extrae (precio_original, precio_venta, descuento_pct) de texto.
    Retorna (None, None, None) si no puede.
    """
    text = text.replace(",", "")

    # Patrón: $X.XX → $Y.YY  o  was $X.XX, now $Y.YY
    two_prices = re.findall(r"\$(\d+(?:\.\d+)?)", text)
    pct_match  = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)

    if len(two_prices) >= 2:
        prices = [float(p) for p in two_prices[:4]]
        prices_sorted = sorted(set(prices), reverse=True)
        if len(prices_sorted) >= 2:
            orig = prices_sorted[0]
            sale = prices_sorted[1]
            if orig > 0 and sale < orig:
                disc = int(round((1 - sale / orig) * 100))
                if 5 <= disc <= 95:
                    return orig, sale, disc

    if len(two_prices) == 1 and pct_match:
        sale = float(two_prices[0])
        disc = int(pct_match.group(1))
        if 5 <= disc <= 95 and sale > 0:
            orig = round(sale / (1 - disc / 100), 2)
            return orig, sale, disc

    return None, None, None


def is_amazon_entry(entry: dict) -> bool:
    """Verifica si la entrada de Slickdeals tiene un link a Amazon."""
    text = " ".join([
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("link", ""),
        " ".join(getattr(entry, "links", [{}])[0].get("href", "") for _ in [0])
            if hasattr(entry, "links") and entry.get("links") else "",
    ]).lower()
    return "amazon.com" in text or "amazon" in entry.get("link", "").lower()


# ─── Clase principal ─────────────────────────────────────────────────────────

class AmazonDealFinder:
    SLICKDEALS_BASE = "https://slickdeals.net/newsearch.php"

    def __init__(self, affiliate_tag: str, min_discount: int = 30):
        self.affiliate_tag = affiliate_tag
        self.min_discount  = min_discount

    def _fetch_rss(self, search_term: str) -> list:
        """Fetches Slickdeals RSS for a given search term."""
        params = {
            "src":        "SearchBarV2",
            "q":          search_term,
            "searcharea": "deals",
            "searchin":   "first",
            "rss":        "1",
        }
        url = self.SLICKDEALS_BASE + "?" + urlencode(params)
        try:
            feed = feedparser.parse(url)
            return feed.entries
        except Exception as e:
            print(f"   ⚠️  RSS error ({search_term}): {e}")
            return []

    def find_deals(self, count: int = 10) -> list:
        print("🔍 Buscando ofertas en Slickdeals RSS...")
        deals    = []
        seen     = set()

        for cat in CATEGORIES:
            cat_deals = []
            for term in cat["search_terms"]:
                print(f"   Buscando: {term}")
                entries = self._fetch_rss(term)
                time.sleep(0.3)  # gentil con el servidor

                for entry in entries:
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", "")
                    link    = entry.get("link", "")

                    key = title.lower()
                    if key in seen or not title:
                        continue

                    # Debe mencionar Amazon
                    full_text = f"{title} {summary} {link}".lower()
                    if "amazon" not in full_text:
                        continue

                    # Debe contener una de nuestras marcas en el título
                    if not any(kw in title.lower() for kw in cat["keywords"]):
                        continue

                    # Extraer precios
                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None:
                        # Intentar solo con summary
                        orig, sale, disc = extract_prices(summary)
                    if disc is None or disc < self.min_discount:
                        continue

                    # Resolver URL de Amazon
                    amazon_url = ""
                    # Buscar URL de Amazon en el texto
                    amazon_matches = re.findall(
                        r'https?://(?:www\.)?amazon\.com/[^\s"\'<>]{10,}', full_text
                    )
                    if amazon_matches:
                        amazon_url = amazon_matches[0].rstrip(".,;)")
                    else:
                        # Seguir redirect del link de Slickdeals
                        print(f"      → Resolviendo redirect para: {title[:50]}…")
                        resolved = resolve_final_url(link)
                        if "amazon.com" in resolved:
                            amazon_url = resolved

                    if not amazon_url:
                        # Usar link original con nota
                        amazon_url = link

                    affiliate_url = add_affiliate_tag(amazon_url, self.affiliate_tag)

                    deal = Deal(
                        title         = title,
                        original_price= orig  if orig  else sale,
                        sale_price    = sale  if sale  else 0.0,
                        discount_pct  = disc,
                        product_url   = amazon_url,
                        affiliate_url = affiliate_url,
                        category      = cat["name"],
                        category_emoji= cat["emoji"],
                    )
                    cat_deals.append(deal)
                    seen.add(key)

                if len(cat_deals) >= 4:
                    break  # suficientes para esta categoría

            # Ordenar por descuento y tomar los mejores de cada categoría
            cat_deals.sort(key=lambda d: d.discount_pct, reverse=True)
            deals.extend(cat_deals[:max(1, count // len(CATEGORIES) + 1)])

        # Si no hay suficientes, buscar cualquier deal de Amazon
        if len(deals) < count:
            print("   Buscando deals generales de Amazon…")
            for term in ["amazon deal today", "amazon sale 50% off", "amazon clearance"]:
                entries = self._fetch_rss(term)
                time.sleep(0.3)
                for entry in entries:
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", "")
                    link    = entry.get("link", "")
                    key     = title.lower()
                    if key in seen or not title:
                        continue
                    full_text = f"{title} {summary} {link}".lower()
                    if "amazon" not in full_text:
                        continue
                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None or disc < self.min_discount:
                        continue
                    amazon_matches = re.findall(
                        r'https?://(?:www\.)?amazon\.com/[^\s"\'<>]{10,}', full_text
                    )
                    amazon_url = amazon_matches[0].rstrip(".,;)") if amazon_matches else link
                    affiliate_url = add_affiliate_tag(amazon_url, self.affiliate_tag)
                    deals.append(Deal(
                        title=title, original_price=orig or sale, sale_price=sale or 0.0,
                        discount_pct=disc, product_url=amazon_url, affiliate_url=affiliate_url,
                        category="Oferta General", category_emoji="🛒",
                    ))
                    seen.add(key)
                    if len(deals) >= count:
                        break
                if len(deals) >= count:
                    break

        # Ordenar globalmente por descuento
        deals.sort(key=lambda d: d.discount_pct, reverse=True)
        result = deals[:count]
        print(f"\n✅ {len(result)} deals encontrados")
        return result
