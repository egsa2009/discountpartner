"""
deal_finder.py v11 — Deduplicación persistente: evita repetir los mismos productos
Los ASINs enviados se guardan en sent_asins.json y se saltan por 48 horas.
"""

import io
import json
import re
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
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
            "boss hugo amazon", "champion amazon deal",
        ],
        "reddit_subreddits": ["frugalmalefashion", "frugalfemininity", "deals"],
        "reddit_queries": ["calvin klein", "tommy hilfiger", "lacoste", "ralph lauren"],
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
            "vans amazon deal", "converse amazon deal",
        ],
        "reddit_subreddits": ["frugalmalefashion", "RunningShoeDeals", "deals"],
        "reddit_queries": ["nike amazon", "adidas amazon", "new balance", "hoka"],
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
            "bose amazon deal", "logitech amazon deal",
        ],
        "reddit_subreddits": ["buildapcsales", "GameDeals", "deals"],
        "reddit_queries": ["apple amazon", "samsung amazon", "sony amazon", "nintendo amazon"],
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Ruta del archivo de ASINs enviados (en el mismo directorio que este script)
SENT_ASINS_PATH = Path(__file__).parent / "sent_asins.json"
# Cuántas horas antes de que un ASIN pueda repetirse
ASIN_COOLDOWN_HOURS = 48


# ─── Gestión de ASINs enviados ──────────────────────────────────────────────

def load_sent_asins() -> dict:
    """Carga el historial de ASINs enviados. Formato: {asin: iso_timestamp}"""
    if SENT_ASINS_PATH.exists():
        try:
            data = json.loads(SENT_ASINS_PATH.read_text(encoding="utf-8"))
            # Limpiar entradas expiradas (> 48h)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=ASIN_COOLDOWN_HOURS)
            cleaned = {
                asin: ts for asin, ts in data.items()
                if datetime.fromisoformat(ts) > cutoff
            }
            return cleaned
        except Exception as e:
            print(f"   ⚠️  Error cargando sent_asins.json: {e}")
    return {}


def save_sent_asins(sent: dict):
    """Guarda el historial actualizado de ASINs enviados."""
    try:
        SENT_ASINS_PATH.write_text(
            json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"   💾 sent_asins.json actualizado ({len(sent)} ASINs)")
    except Exception as e:
        print(f"   ⚠️  Error guardando sent_asins.json: {e}")


def mark_asin_sent(sent: dict, asin: str):
    """Marca un ASIN como enviado ahora."""
    sent[asin] = datetime.now(timezone.utc).isoformat()


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
    asin: str = ""

    def savings(self) -> float:
        return max(0.0, self.original_price - self.sale_price)

    def telegram_caption(self, index: int) -> str:
        title_short = self.title[:120] + ("…" if len(self.title) > 120 else "")
        asin = self.asin or _extract_asin(self.product_url)
        tag  = _tag_from_url(self.affiliate_url) or "discountpartn-20"
        short_url = (
            f"https://www.amazon.com/dp/{asin}?tag={tag}"
            if asin and tag else self.affiliate_url
        )
        brand_query = self.title.split("|")[0].strip()[:40].replace(" ", "+")
        colombia_url = (
            f"https://www.amazon.com/s?k={brand_query}"
            f"&i=fashion&deals-widget=%7B%22version%22%3A1%7D"
            f"&ship-to-country=CO&tag={tag}"
        )
        lines = [
            f"{self.category_emoji} <b>OFERTA #{index} — {self.category}</b>",
            f"📦 {title_short}",
            "",
            f"💰 <s>${self.original_price:.2f}</s> → <b>${self.sale_price:.2f}</b>",
            f"🔥 <b>{self.discount_pct}% OFF</b> — Ahorras ${self.savings():.2f}",
            "",
            f"🛒 <b>Comprar:</b> {short_url}",
            f"🌎 <b>Buscar en Amazon Colombia:</b> {colombia_url}",
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
            "asin": self.asin,
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

    def _fetch_reddit(self, subreddit: str, query: str = "") -> list:
        """Posts de Reddit — comunidad verifica precios en tiempo real."""
        if query:
            url = (f"https://www.reddit.com/r/{subreddit}/search.rss"
                   f"?q={query.replace(' ', '+')}&sort=new&restrict_sr=1&limit=25")
        else:
            url = f"https://www.reddit.com/r/{subreddit}/new.rss?limit=25"
        try:
            feed = feedparser.parse(url)
            return feed.entries
        except Exception as e:
            print(f"   ⚠️  Reddit r/{subreddit}: {e}")
            return []

    # ── Imagen ───────────────────────────────────────────────────────────────

    def _image_from_entry(self, entry) -> tuple:
        def _download(url: str) -> bytes:
            try:
                r = self.session.get(url, timeout=8)
                if r.status_code == 200 and len(r.content) > 2000:
                    return r.content
            except Exception:
                pass
            return b""

        for media in entry.get("media_content", []):
            url = media.get("url", "")
            if url.startswith("http"):
                data = _download(url)
                if data:
                    return url, data

        for thumb in entry.get("media_thumbnail", []):
            url = thumb.get("url", "")
            if url.startswith("http"):
                data = _download(url)
                if data:
                    return url, data

        html = entry.get("summary", "") or entry.get("description", "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src.startswith("http") and any(x in src.lower() for x in [".jpg", ".jpeg", ".png", ".webp"]):
                    data = _download(src)
                    if data:
                        return src, data

        sd_link = entry.get("link", "")
        if sd_link and "slickdeals.net" in sd_link:
            try:
                resp = self.session.get(sd_link, timeout=10)
                page_soup = BeautifulSoup(resp.text, "html.parser")
                og = page_soup.find("meta", property="og:image")
                if og and og.get("content","").startswith("http"):
                    data = _download(og["content"])
                    if data:
                        return og["content"], data
                for img in page_soup.find_all("img"):
                    src = img.get("src","")
                    if src.startswith("http") and any(x in src for x in ["images-na", "m.media-amazon", "cloudfront", "ssl-images"]):
                        data = _download(src)
                        if data:
                            return src, data
            except Exception as e:
                print(f"      ⚠️  Error buscando imagen en página SD: {e}")

        return "", b""

    # ── URL Amazon ───────────────────────────────────────────────────────────

    def _amazon_url_from_entry(self, entry) -> str:
        full_text = " ".join([
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("link", ""),
        ])
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
        try:
            resp = self.session.get(sd_url, timeout=12, allow_redirects=True)
            asin = _extract_asin(resp.url)
            if asin:
                return f"https://www.amazon.com/dp/{asin}"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "amazon.com" in href:
                    asin = _extract_asin(href)
                    if asin:
                        return f"https://www.amazon.com/dp/{asin}"
            for pat in [r'amazon\.com/(?:[^/]+/)?dp/([A-Z0-9]{10})', r'asin=([A-Z0-9]{10})']:
                m = re.search(pat, resp.text, re.IGNORECASE)
                if m:
                    return f"https://www.amazon.com/dp/{m.group(1).upper()}"
        except Exception as e:
            print(f"      ⚠️  Error resolviendo página SD: {e}")
        return ""

    # ── Pipeline principal ────────────────────────────────────────────────────

    def find_deals(self, count: int = 10, sent_asins: dict = None) -> list:
        """
        Busca deals frescos. sent_asins = {asin: timestamp} — productos ya enviados
        recientemente que se deben omitir para evitar repeticiones.
        """
        if sent_asins is None:
            sent_asins = {}

        skipped_asins = set(sent_asins.keys())
        print(f"🔍 Buscando ofertas… ({len(skipped_asins)} ASINs en cooldown)")
        deals, seen_titles, seen_asins = [], set(), set(skipped_asins)

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

                    if key in seen_titles or not title:
                        continue
                    if "amazon" not in f"{title} {summary} {sd_link}".lower():
                        continue
                    if not any(kw in title.lower() for kw in cat["keywords"]):
                        continue

                    # ── Filtro de antigüedad: solo deals de últimas 4 horas ──
                    pub_str = entry.get("published", "")
                    if pub_str:
                        try:
                            pub_dt = parsedate_to_datetime(pub_str)
                            age_h = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                            if age_h > 4:
                                print(f"      ⏰ Muy viejo ({age_h:.0f}h): {title[:40]}")
                                continue
                        except Exception:
                            pass

                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None or disc < self.min_discount:
                        continue
                    if disc > 90 and "*" in title:
                        print(f"      ⚠️  Coupon irreal ({disc}%*): {title[:40]}")
                        continue

                    # URL de Amazon
                    amazon_url = self._amazon_url_from_entry(entry)
                    if not amazon_url:
                        print(f"      → Resolviendo página SD: {title[:45]}…")
                        amazon_url = self._amazon_url_from_page(sd_link)
                        time.sleep(0.5)
                    if not amazon_url:
                        continue

                    asin = _extract_asin(amazon_url)

                    # ── DEDUPLICACIÓN PERSISTENTE: saltar ASIN ya enviado ──
                    if asin and asin in seen_asins:
                        print(f"      🔄 ASIN {asin} ya enviado recientemente, omitiendo")
                        continue

                    affiliate_url = add_affiliate_tag(amazon_url, self.affiliate_tag)
                    img_url, img_bytes = self._image_from_entry(entry)
                    if img_bytes:
                        print(f"      🖼️  Imagen OK ({len(img_bytes)//1024} KB)")
                    else:
                        print(f"      ⚠️  Sin imagen")
                    time.sleep(0.2)

                    cat_deals.append(Deal(
                        title=title, original_price=orig or sale, sale_price=sale or 0.0,
                        discount_pct=disc, product_url=amazon_url, affiliate_url=affiliate_url,
                        image_url=img_url, image_bytes=img_bytes,
                        category=cat["name"], category_emoji=cat["emoji"],
                        asin=asin,
                    ))
                    seen_titles.add(key)
                    if asin:
                        seen_asins.add(asin)

                if len(cat_deals) >= 4:
                    break

            # ── Reddit RSS ────────────────────────────────────────────────────
            if len(cat_deals) < 3:
                reddit_subs = cat.get("reddit_subreddits", [])
                reddit_qs   = cat.get("reddit_queries", [])
                for sub, rq in zip(reddit_subs, reddit_qs):
                    print(f"   Reddit r/{sub}: {rq}")
                    r_entries = self._fetch_reddit(sub, rq)
                    time.sleep(0.3)
                    for entry in r_entries:
                        title   = entry.get("title", "").strip()
                        summary = entry.get("summary", "") or ""
                        sd_link = entry.get("link", "")
                        key     = title.lower()
                        if key in seen_titles or not title:
                            continue
                        if "amazon" not in f"{title} {summary} {sd_link}".lower():
                            continue
                        if not any(kw in title.lower() for kw in cat["keywords"]):
                            continue
                        pub_str = entry.get("published", "")
                        if pub_str:
                            try:
                                pub_dt = parsedate_to_datetime(pub_str)
                                age_h = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                                if age_h > 4:
                                    continue
                            except Exception:
                                pass
                        orig, sale, disc = extract_prices(f"{title} {summary}")
                        if disc is None or disc < self.min_discount:
                            continue
                        amazon_url = self._amazon_url_from_entry(entry) or self._amazon_url_from_page(sd_link)
                        if not amazon_url:
                            continue
                        asin = _extract_asin(amazon_url)
                        if asin and asin in seen_asins:
                            print(f"      🔄 ASIN {asin} ya enviado, omitiendo (Reddit)")
                            continue
                        img_url, img_bytes = self._image_from_entry(entry)
                        cat_deals.append(Deal(
                            title=title, original_price=orig or sale, sale_price=sale or 0.0,
                            discount_pct=disc, product_url=amazon_url,
                            affiliate_url=add_affiliate_tag(amazon_url, self.affiliate_tag),
                            image_url=img_url, image_bytes=img_bytes,
                            category=cat["name"], category_emoji=cat["emoji"],
                            source="Reddit", asin=asin,
                        ))
                        seen_titles.add(key)
                        if asin:
                            seen_asins.add(asin)
                        if len(cat_deals) >= 4:
                            break
                    if len(cat_deals) >= 4:
                        break

            cat_deals.sort(key=lambda d: d.discount_pct, reverse=True)
            deals.extend(cat_deals[:max(1, count // len(CATEGORIES) + 1)])

        # Relleno genérico
        if len(deals) < count:
            print("   Buscando deals generales de Amazon…")
            for term in ["amazon deal 50% off", "amazon clearance sale", "amazon lightning deal"]:
                for entry in self._fetch_rss(term):
                    title   = entry.get("title", "").strip()
                    summary = entry.get("summary", "")
                    sd_link = entry.get("link", "")
                    key     = title.lower()
                    if key in seen_titles or not title:
                        continue
                    if "amazon" not in f"{title} {summary} {sd_link}".lower():
                        continue
                    orig, sale, disc = extract_prices(f"{title} {summary}")
                    if disc is None or disc < self.min_discount:
                        continue
                    amazon_url = self._amazon_url_from_entry(entry) or self._amazon_url_from_page(sd_link)
                    if not amazon_url:
                        continue
                    asin = _extract_asin(amazon_url)
                    if asin and asin in seen_asins:
                        continue
                    img_url, img_bytes = self._image_from_entry(entry)
                    deals.append(Deal(
                        title=title, original_price=orig or sale, sale_price=sale or 0.0,
                        discount_pct=disc, product_url=amazon_url,
                        affiliate_url=add_affiliate_tag(amazon_url, self.affiliate_tag),
                        image_url=img_url, image_bytes=img_bytes,
                        category="Oferta General", category_emoji="🛒", asin=asin,
                    ))
                    seen_titles.add(key)
                    if asin:
                        seen_asins.add(asin)
                    if len(deals) >= count:
                        break
                if len(deals) >= count:
                    break

        deals.sort(key=lambda d: d.discount_pct, reverse=True)
        result = deals[:count]
        with_img = sum(1 for d in result if d.image_bytes)
        print(f"\n✅ {len(result)} deals — {with_img} con imagen, {len(result)-with_img} sin imagen")
        return result
