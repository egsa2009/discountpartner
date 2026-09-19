"""
deal_finder.py v10 — Slickdeals RSS con imágenes desde CDN de Slickdeals
(no depende de Amazon para las imágenes → sin bloqueo en GitHub Actions)
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
from email.utils import parsedate_to_datetime


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
            "calvin klein amazon", "tommy hilfiger amazon",
            "lacoste amazon", "ralph lauren amazon", "armani amazon",
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
            "nike shoes amazon", "adidas shoes amazon",
            "new balance amazon", "under armour amazon", "hoka amazon",
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
            "apple amazon deal", "samsung amazon deal",
            "sony amazon deal", "nintendo amazon deal", "lenovo amazon deal",
        ],
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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
    product_url: str
    affiliate_url: str
    category: str
    category_emoji: str
    image_url: str = ""
    image_bytes: bytes = field(default=b"", repr=False)
    source: str = "Slickdeals"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def savings(self) -> float:
        return max(0.0, self.original_price - self.sale_price)

    def telegram_caption(self, index: int) -> str:
        title_short = self.title[:120] + ("…" if len(self.title) > 120 else "")
        asin = _extract_asin(self.product_url)
        tag  = _tag_from_url(self.affiliate_url)
        short_url = (
            f"https://www.amazon.com/dp/{asin}?tag={tag}"
            if asin and tag else self.affiliate_url
        )
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
        return parse_qs(urlparse(url).query).get("tag", [""])[0]
    except Exception:
        return ""


def add_affiliate_tag(url: str, tag: str) -> str:
    try:
        parsed = urlparse(url)
        if "amazon.com" not in parsed.netloc:
            return url
        params = parse_qs(parsed.query, keep_blank_values=True)
        params["tag"] = [tag]
        return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in params.items()})))
    except Exception:
        return url


def extract_prices(text: str):
    text = text.replace(",", "")
    prices = [float(p) for p in re.findall(r"\$(\d+(?:\.\d+)?)", text)]
    pct_m  = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)

    if len(prices) >= 2:
        s = sorted(set(prices), reverse=True)
        if len(s) >= 2 and s[0] > 0 and 0 < s[1] < s[0]:
            disc = int(round((1 - s[1] / s[0]) * 100))
            if 5 <= disc <= 95:
                return s[0], s[1], disc

    if len(prices) == 1 and pct_m:
        sale = prices[0]
        disc = int(pct_m.group(1))
        if 5 <= disc <= 95 and sale > 0:
            return round(sale / (1 - disc / 100), 2), sale, disc

    return None, None, None


# ─── AmazonDealFinder ────────────────────────────────────────────────────────

class AmazonDealFinder:
    SLICKDEALS_BASE = "https://slickdeals.net/newsearch.php"

    def __init__(self, affiliate_tag: str, min_discount: int = 30):
        self.affiliate_tag = affiliate_tag
        self.min_discount  = min_discount
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ── RSS ──────────────────────────────────────────────────────────────────

    def _fetch_rss(self, term: str) -> list:
        params = {"src": "SearchBarV2", "q": term,
                  "searcharea": "deals", "searchin": "first", "rss": "1"}
        try:
            return feedparser.parse(
                self.SLICKDEALS_BASE + "?" + urlencode(params)
            ).entries
        except Exception as e:
            print(f"   ⚠️  RSS error ({term}): {e}")
            return []

    # ── Imagen desde el RSS entry ────────────────────────────────────────────

    def _image_from_entry(self, entry) -> tuple[str, bytes]:
        """
        Extrae la imagen del producto desde el RSS entry o la página del deal.
        Orden: media_content → media_thumbnail → <img> en summary → og:image en página SD.
        """
        def _download(url: str) -> bytes:
            try:
                r = self.session.get(url, timeout=8)
                if r.status_code == 200 and len(r.content) > 2000:
                    return r.content
            except Exception:
                pass
            return b""

        # 1. media_content (feedparser)
        for media in entry.get("media_content", []):
            url = media.get("url", "")
            if url.startswith("http"):
                data = _download(url)
                if data:
                    return url, data

        # 2. media_thumbnail
        for thumb in entry.get("media_thumbnail", []):
            url = thumb.get("url", "")
            if url.startswith("http"):
                data = _download(url)
                if data:
                    return url, data

        # 3. <img> en el HTML del summary/description
        html = entry.get("summary", "") or entry.get("description", "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src.startswith("http") and any(x in src.lower() for x in [".jpg", ".jpeg", ".png", ".webp"]):
                    data = _download(src)
                    if data:
                        return src, data

        # 4. og:image en la página del deal de Slickdeals
        sd_link = entry.get("link", "")
        if sd_link and "slickdeals.net" in sd_link:
            try:
                resp = self.session.get(sd_link, timeout=10)
                page_soup = BeautifulSoup(resp.text, "html.parser")
                # og:image (la mejor imagen del deal)
                og = page_soup.find("meta", property="og:image")
                if og and og.get("content","").startswith("http"):
                    data = _download(og["content"])
                    if data:
                        return og["content"], data
                # imagen principal del producto en la página
                for img in page_soup.find_all("img"):
                    src = img.get("src","")
                    if src.startswith("http") and any(x in src for x in ["images-na", "m.media-amazon", "cloudfront", "ssl-images"]):
                        data = _download(src)
                        if data:
                            return src, data
            except Exception as e:
                print(f"      ⚠️  Error buscando imagen en página SD: {e}")

        return "", b""

    # ── URL real de Amazon ───────────────────────────────────────────────────

    def _amazon_url_from_entry(self, entry) -> str:
        """Intenta extraer la URL de Amazon directamente del RSS entry."""
        full_text = " ".join([
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("link", ""),
        ])
        # Extraer ASIN directamente (10 chars alfanuméricos después de /dp/ o /gp/product/)
        for pat in [
            r'amazon\.com/(?:[^/]+/)?dp/([A-Z0-9]{10})',
            r'amazon\.com/gp/product/([A-Z0-9]{10})',
            r'asin=([A-Z0-9]{10})',
        ]:
            m = re.search(pat, full_text, re.IGNORECASE)
            if m:
                asin = m.group(1).upper()
                return f"https://www.amazon.com/dp/{asin}"
        return ""

    def _amazon_url_from_page(self, sd_url: str) -> str:
        """Obtiene URL de Amazon desde la página de Slickdeals (redirect + HTML parse)."""
        try:
            resp = self.session.get(sd_url, timeout=12, allow_redirects=True)
            # Intentar extraer ASIN de la URL final (redirect)
            asin = _extract_asin(resp.url)
            if asin:
                return f"https://www.amazon.com/dp/{asin}"
            # Buscar en el HTML
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "amazon.com" in href:
                    asin = _extract_asin(href)
                    if asin:
                        return f"https://www.amazon.com/dp/{asin}"
            # Regex fallback en texto crudo
            for pat in [r'amazon\.com/(?:[^/]+/)?dp/([A-Z0-9]{10})', r'asin=([A-Z0-9]{10})']:
                m = re.search(pat, resp.text, re.IGNORECASE)
                if m:
                    return f"https://www.amazon.com/dp/{m.group(1).upper()}"
        except Exception as e:
            print(f"      ⚠️  Error resolviendo página SD: {e}")
        return ""

    # ── Pipeline principal ────────────────────────────────────────────────────

    def find_deals(self, count: int = 10) -> list:
        print("🔍 Buscando ofertas en Slickdeals RSS…")
        deals, seen = [], set()

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
                    key     = title.lower()

                    if key in seen or not title:
                        continue
                    if "amazon" not in f"{title} {summary} {sd_link}".lower():
                        continue
                    if not any(kw in title.lower() for kw in cat["keywords"]):
                        continue

                    # ── Filtro de antigüedad: solo deals de últimas 24 horas ──
                    pub_str = entry.get("published", "")
                    if pub_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            from datetime import datetime, timezone
                            pub_dt = parsedate_to_datetime(pub_str)
                            age_h = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                            if age_h > 24:
                                print(f"      ⏰ Deal expirado ({age_h:.0f}h): {title[:40]}")
                                continue
                        except Exception:
                            pass  # Sin fecha → incluir igual

                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None or disc < self.min_discount:
                        continue
                    # Si el precio en el título tiene "*" → coupon/clip, descuento puede ser irreal
                    # Solo rechazar si el descuento calculado es absurdo (> 90%)
                    if disc > 90 and "*" in title:
                        print(f"      ⚠️  Precio coupon irreal ({disc}%*): {title[:40]}")
                        continue

                    # URL de Amazon
                    amazon_url = self._amazon_url_from_entry(entry)
                    if not amazon_url:
                        print(f"      → Resolviendo página SD: {title[:45]}…")
                        amazon_url = self._amazon_url_from_page(sd_link)
                        time.sleep(0.5)
                    if not amazon_url:
                        print(f"         ⚠️  Sin URL Amazon, omitiendo")
                        continue

                    affiliate_url = add_affiliate_tag(amazon_url, self.affiliate_tag)

                    # Imagen desde el RSS entry (no bloqueable)
                    img_url, img_bytes = self._image_from_entry(entry)
                    if img_bytes:
                        print(f"      🖼️  Imagen OK ({len(img_bytes)//1024} KB)")
                    else:
                        print(f"      ⚠️  Sin imagen en entry")
                    time.sleep(0.2)

                    cat_deals.append(Deal(
                        title=title, original_price=orig or sale, sale_price=sale or 0.0,
                        discount_pct=disc, product_url=amazon_url, affiliate_url=affiliate_url,
                        image_url=img_url, image_bytes=img_bytes,
                        category=cat["name"], category_emoji=cat["emoji"],
                    ))
                    seen.add(key)

                if len(cat_deals) >= 4:
                    break

            cat_deals.sort(key=lambda d: d.discount_pct, reverse=True)
            deals.extend(cat_deals[:max(1, count // len(CATEGORIES) + 1)])

        # Relleno genérico
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
                    amazon_url = self._amazon_url_from_entry(entry) or self._amazon_url_from_page(sd_link)
                    if not amazon_url:
                        continue
                    img_url, img_bytes = self._image_from_entry(entry)
                    deals.append(Deal(
                        title=title, original_price=orig or sale, sale_price=sale or 0.0,
                        discount_pct=disc, product_url=amazon_url,
                        affiliate_url=add_affiliate_tag(amazon_url, self.affiliate_tag),
                        image_url=img_url, image_bytes=img_bytes,
                        category="Oferta General", category_emoji="🛒",
                    ))
                    seen.add(key)
                    if len(deals) >= count:
                        break
                if len(deals) >= count:
                    break

        deals.sort(key=lambda d: d.discount_pct, reverse=True)
        result = deals[:count]
        with_img = sum(1 for d in result if d.image_bytes)
        print(f"\n✅ {len(result)} deals — {with_img} con imagen, {len(result)-with_img} sin imagen")
        return result
