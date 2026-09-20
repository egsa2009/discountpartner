"""
process_urls.py v4 — Scraping Amazon via ScraperAPI (bypass de bloqueo por IP).
Recibe PRODUCT_DEALS como JSON: [{url, sale_price?, orig_price?, discount_pct?}, ...]
Los precios del usuario son opcionales — si no se dan, se extraen de Amazon via ScraperAPI.
"""

import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

try:
    from deal_finder import add_affiliate_tag, _extract_asin, Deal
    from post_creator import create_post
except ImportError as e:
    print(f"❌ Error importando módulos: {e}")
    sys.exit(1)

# ─── Config ──────────────────────────────────────────────────────────────────

SCRAPER_KEY   = os.environ.get("SCRAPER_API_KEY", "")
AFFILIATE_TAG = os.environ.get("AMAZON_AFFILIATE_TAG", "discountpartn-20")
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# ─── ScraperAPI ──────────────────────────────────────────────────────────────

def resolve_short_url(url: str, session: requests.Session) -> str:
    """Resuelve URLs cortas (amzn.to, a.co) al link real de Amazon."""
    if "amzn.to" in url or "a.co/d/" in url:
        try:
            r = session.head(url, allow_redirects=True, timeout=10)
            resolved = r.url
            if "amazon.com" in resolved:
                print(f"   🔗 URL resuelta: {resolved[:80]}")
                return resolved
        except Exception as e:
            print(f"   ⚠️  No se pudo resolver URL corta: {e}")
    return url

def scraper_url(url: str) -> str:
    """Envuelve la URL con ScraperAPI si hay key disponible."""
    if SCRAPER_KEY:
        return (f"https://api.scraperapi.com?api_key={SCRAPER_KEY}"
                f"&url={quote_plus(url)}&render=true&country_code=us")
    return url  # fallback sin proxy (puede fallar en GitHub Actions)

# ─── Telegram helpers ────────────────────────────────────────────────────────

def send_telegram_text(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TG_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"   ⚠️  Telegram text error: {e}")
        return False

def send_telegram_photo(img_bytes: bytes, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    try:
        r = requests.post(url, data={
            "chat_id": TG_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML",
        }, files={"photo": ("deal.jpg", img_bytes, "image/jpeg")}, timeout=30)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"   ⚠️  Telegram photo error: {e}")
        return False

# ─── Amazon scraper (via ScraperAPI) ─────────────────────────────────────────

def _price(text: str) -> float:
    m = re.search(r"\$?([\d,]+\.?\d*)", text.replace(",", ""))
    return float(m.group(1)) if m else 0.0

def _clean(text: str) -> str:
    return " ".join(text.split()).strip()

def fetch_amazon_product(url: str, session: requests.Session) -> dict:
    """Scraping completo via ScraperAPI: título, precios, imagen."""
    result = {
        "title": "", "sale_price": 0.0, "original_price": 0.0,
        "discount_pct": 0, "image_url": "", "image_bytes": b"", "error": None,
    }
    try:
        time.sleep(2.0)
        url = resolve_short_url(url, session)
        fetch_url = scraper_url(url)
        resp = session.get(fetch_url, timeout=45, allow_redirects=True)
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Título ──────────────────────────────────────────────────────────
        title_searches = [
            ("span", {"id": "productTitle"}),
            ("h1",   {"id": "title"}),
            ("span", {"class": "product-title-word-break"}),
        ]
        for sel, attrs in title_searches:
            el = soup.find(sel, attrs)
            if el:
                t = _clean(el.get_text())
                if len(t) > 5:
                    result["title"] = t
                    break
        # Fallback: og:title
        if not result["title"]:
            og = soup.find("meta", property="og:title")
            if og and og.get("content", "").strip():
                result["title"] = _clean(og["content"])

        # ── Precio de venta ──────────────────────────────────────────────────
        for selector in [
            ".apexPriceToPay .a-offscreen",
            ".reinventPricePriceToPayMargin .a-offscreen",
            "#priceblock_dealprice", "#priceblock_saleprice",
            ".a-price.a-text-price .a-offscreen",
            ".a-price .a-offscreen", "#price_inside_buybox",
            "#priceblock_ourprice", "#tp_price_block_total_price_ww .a-offscreen",
            "#corePrice_feature_div .a-offscreen",
        ]:
            el = soup.select_one(selector)
            if el:
                p = _price(el.get_text())
                if p > 0:
                    result["sale_price"] = p
                    break

        # ── Precio original (tachado) ────────────────────────────────────────
        for selector in [
            ".a-text-strike", ".basisPrice .a-offscreen",
            ".priceBlockStrikePriceString",
            ".a-price[data-a-strike='true'] .a-offscreen",
        ]:
            el = soup.select_one(selector)
            if el:
                p = _price(el.get_text())
                if p > 0 and p != result["sale_price"]:
                    result["original_price"] = p
                    break

        # ── Descuento ────────────────────────────────────────────────────────
        if result["sale_price"] > 0 and result["original_price"] > result["sale_price"]:
            result["discount_pct"] = int(round(
                (1 - result["sale_price"] / result["original_price"]) * 100
            ))
        else:
            for selector in [".savingsPercentage", "#savingsPercentage"]:
                el = soup.select_one(selector)
                if el:
                    m = re.search(r"(\d+)\s*%", el.get_text())
                    if m:
                        result["discount_pct"] = int(m.group(1))
                        if result["sale_price"] > 0 and result["original_price"] == 0:
                            result["original_price"] = round(
                                result["sale_price"] / (1 - result["discount_pct"] / 100), 2
                            )
                        break

        if result["sale_price"] > 0 and result["original_price"] == 0:
            result["original_price"] = result["sale_price"]

        # ── Imagen ───────────────────────────────────────────────────────────
        img_url = ""
        for img_id in ["landingImage", "imgBlkFront", "main-image"]:
            img_el = soup.find("img", {"id": img_id})
            if img_el:
                img_url = (img_el.get("data-old-hires") or img_el.get("data-a-hires") or "")
                if not img_url or "data:" in img_url:
                    dyn = img_el.get("data-a-dynamic-image", "{}")
                    try:
                        imgs = json.loads(dyn)
                        if imgs:
                            img_url = max(imgs, key=lambda u: imgs[u][0])
                    except Exception:
                        img_url = img_el.get("src", "")
                if img_url and img_url.startswith("http"):
                    break
        if not img_url:
            og = soup.find("meta", property="og:image")
            if og:
                img_url = og.get("content", "")

        if img_url and img_url.startswith("http"):
            result["image_url"] = img_url
            try:
                r_img = session.get(img_url, timeout=15)
                if r_img.status_code == 200 and len(r_img.content) > 2000:
                    result["image_bytes"] = r_img.content
            except Exception as e:
                print(f"      ⚠️  Error descargando imagen: {e}")

        if not result["title"]:
            result["error"] = "No se pudo extraer el título"

    except Exception as e:
        result["error"] = str(e)

    return result

# ─── Caption Telegram ────────────────────────────────────────────────────────

def make_caption(index: int, title: str, sale_price: float, original_price: float,
                 discount_pct: int, affiliate_url: str, asin: str) -> str:
    title_short = title[:120] + ("…" if len(title) > 120 else "")
    short_url = f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}" if asin else affiliate_url
    savings   = max(0.0, original_price - sale_price) if original_price > sale_price else 0

    cat_lower = title.lower()
    if any(w in cat_lower for w in ["shoe", "sneaker", "boot", "zapato", "nike", "adidas",
                                     "new balance", "hoka", "vans", "converse", "reebok"]):
        emoji, cat_name = "👟", "Zapatos Deportivos"
    elif any(w in cat_lower for w in ["shirt", "jacket", "pants", "dress", "coat", "jeans",
                                       "hoodie", "calvin", "tommy", "lacoste", "ralph", "armani",
                                       "boss", "levi", "puma", "champion"]):
        emoji, cat_name = "👗", "Ropa de Marca"
    elif any(w in cat_lower for w in ["ipad", "iphone", "macbook", "airpod", "samsung", "laptop",
                                       "tablet", "headphone", "speaker", "kindle", "echo",
                                       "nintendo", "sony", "lenovo", "dell", "hp", "asus",
                                       "pixel", "logitech", "bose"]):
        emoji, cat_name = "💻", "Tecnología"
    else:
        emoji, cat_name = "🛍️", "Oferta"

    brand_query  = title.split("|")[0].strip()[:40].replace(" ", "+")
    colombia_url = (
        f"https://www.amazon.com/s?k={brand_query}"
        f"&i=fashion&deals-widget=%7B%22version%22%3A1%7D"
        f"&ship-to-country=CO&tag={AFFILIATE_TAG}"
    )

    lines = [f"{emoji} <b>OFERTA #{index} — {cat_name}</b>", f"📦 {title_short}", ""]
    if discount_pct > 0 and sale_price > 0:
        lines += [
            f"💰 <s>${original_price:.2f}</s> → <b>${sale_price:.2f}</b>",
            f"🔥 <b>{discount_pct}% OFF</b>" + (f" — Ahorras ${savings:.2f}" if savings > 0 else ""),
            "",
        ]
    elif sale_price > 0:
        lines += [f"💰 <b>${sale_price:.2f}</b>", ""]
    lines += [f"🛒 <b>Comprar:</b> {short_url}", f"🌎 <b>Amazon Colombia:</b> {colombia_url}"]
    return "\n".join(lines)

# ─── Pipeline ────────────────────────────────────────────────────────────────

def run_url_pipeline():
    print("\n" + "═" * 55)
    print("   🔗  Discount Partner — Manual v4 + ScraperAPI")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   ScraperAPI: {'✅ activo' if SCRAPER_KEY else '❌ sin key'}")
    print("═" * 55 + "\n")

    deals_raw = os.environ.get("PRODUCT_DEALS", "")
    if not deals_raw:
        print("❌ PRODUCT_DEALS vacío")
        sys.exit(1)
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        sys.exit(1)

    try:
        deal_list = json.loads(deals_raw)
        if not isinstance(deal_list, list):
            raise ValueError("Debe ser array JSON")
    except Exception as e:
        print(f"❌ Error parseando PRODUCT_DEALS: {e}")
        sys.exit(1)

    def _is_amz(u):
        u = u.lower()
        return any(d in u for d in ["amazon", "amzn.to", "a.co/d/"])

    deal_list = [d for d in deal_list if d.get("url") and _is_amz(d["url"])]
    print(f"📋 {len(deal_list)} deal(s) recibido(s):")
    for i, d in enumerate(deal_list, 1):
        print(f"   {i}. {d['url'][:80]}")

    session = requests.Session()
    session.headers.update(HEADERS)

    img_dir = Path(__file__).parent / "instagram_posts"
    img_dir.mkdir(exist_ok=True)

    # Encabezado Telegram
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    header  = (
        f"🔗 <b>Discount Partner</b> — {len(deal_list)} oferta{'' if len(deal_list)==1 else 's'}\n"
        f"🕐 {now_str} COT\n"
        f"─────────────────────────\n"
        f"Selección manual · Link afiliado incluido"
    )
    send_telegram_text(header)
    time.sleep(1)

    sent, results = 0, []

    for i, deal in enumerate(deal_list, 1):
        product_url = deal["url"]
        # Precios provistos por el usuario (opcionales)
        user_sale = float(deal.get("sale_price") or 0)
        user_orig = float(deal.get("orig_price") or 0)
        user_disc = int(deal.get("discount_pct") or 0)

        print(f"\n── Producto #{i} ────────────────────────────────")
        print(f"   URL: {product_url[:70]}")
        asin = _extract_asin(product_url)
        print(f"   ASIN: {asin or '(no encontrado)'}")

        # Scraping via ScraperAPI
        print("   🔍 Scrapeando Amazon via ScraperAPI…")
        data = fetch_amazon_product(product_url, session)

        if data["error"]:
            print(f"   ⚠️  {data['error']}")

        title = data["title"] or "Oferta Amazon"
        print(f"   📦 {title[:70]}")

        # Precios: usuario > scraped
        sale_price   = user_sale   if user_sale   > 0 else data["sale_price"]
        orig_price   = user_orig   if user_orig   > 0 else data["original_price"]
        discount_pct = user_disc   if user_disc   > 0 else data["discount_pct"]

        # Calcular descuento si tenemos ambos precios
        if sale_price > 0 and orig_price > sale_price and discount_pct == 0:
            discount_pct = int(round((1 - sale_price / orig_price) * 100))
        if sale_price > 0 and discount_pct > 0 and orig_price == 0:
            orig_price = round(sale_price / (1 - discount_pct / 100), 2)

        img_bytes = data["image_bytes"]
        img_url   = data["image_url"]
        print(f"   💰 ${sale_price:.2f} (original: ${orig_price:.2f}, {discount_pct}% OFF)")
        print(f"   🖼️  {'✅ ' + str(len(img_bytes)//1024) + ' KB' if img_bytes else '❌ sin imagen'}")

        clean_url     = f"https://www.amazon.com/dp/{asin}" if asin else product_url
        affiliate_url = add_affiliate_tag(clean_url, AFFILIATE_TAG)
        caption = make_caption(i, title, sale_price, orig_price, discount_pct, affiliate_url, asin)

        # Imagen Instagram
        post_bytes = b""
        if img_bytes:
            try:
                deal_obj = Deal(
                    title=title, original_price=orig_price,
                    sale_price=sale_price, discount_pct=discount_pct,
                    product_url=clean_url, affiliate_url=affiliate_url,
                    image_url=img_url, image_bytes=img_bytes,
                    category="Manual", category_emoji="🔗", asin=asin,
                )
                out_path = img_dir / f"post_manual_{datetime.now().strftime('%Y%m%d_%H%M')}_{i:02d}.jpg"
                img_canvas = create_post(deal_obj, str(out_path))
                buf = io.BytesIO()
                img_canvas.save(buf, "JPEG", quality=92)
                post_bytes = buf.getvalue()
                print(f"   🎨 Imagen Instagram: {len(post_bytes)//1024} KB")
            except Exception as e:
                print(f"   ⚠️  Error imagen Instagram: {e}")

        # Enviar a Telegram
        if post_bytes:
            ok = send_telegram_photo(post_bytes, caption)
        elif img_bytes:
            ok = send_telegram_photo(img_bytes, caption)
        else:
            ok = send_telegram_text(caption)

        status = "✅" if ok else "❌"
        print(f"   {status} Telegram")
        if ok:
            sent += 1

        results.append({
            "url": product_url, "asin": asin, "title": title,
            "sale_price": sale_price, "orig_price": orig_price, "discount_pct": discount_pct,
            "image_found": bool(img_bytes), "sent": ok,
        })
        time.sleep(2.5)

    print(f"\n{'═'*55}")
    print(f"✨ {sent}/{len(deal_list)} productos enviados a Telegram")

    Path(__file__).parent.joinpath("deals_output.json").write_text(
        json.dumps({"generated_at": datetime.now().isoformat(),
                    "mode": "manual_v4", "sent": sent,
                    "total": len(deal_list), "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8"
    )

if __name__ == "__main__":
    run_url_pipeline()
