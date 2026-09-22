"""
process_urls.py v5 — Scraping Amazon via ScraperAPI + Telegram + Instagram (Zernio).
Recibe PRODUCT_DEALS como JSON: [{url, sale_price?, orig_price?, discount_pct?}, ...]
Los precios del usuario son opcionales — si no se dan, se extraen de Amazon via ScraperAPI.
"""

import base64
import hashlib
import hmac
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

# Zernio (Instagram)
ZERNIO_KEY     = os.environ.get("ZERNIO_API_KEY", "")
ZERNIO_IG_ACCT = os.environ.get("ZERNIO_IG_ACCOUNT_ID", "6ab15f748d284ffb2127d103")
ZERNIO_API     = "https://api.zernio.com"

# Cloudinary
CLD_NAME   = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLD_KEY    = os.environ.get("CLOUDINARY_API_KEY", "")
CLD_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# ─── Resolver links cortos ───────────────────────────────────────────────────

def resolve_amazon_url(url: str) -> str:
    """
    Sigue redirects de links cortos (amzn.to, a.co/d/) hasta obtener
    la URL completa de Amazon con ASIN. Sin ScraperAPI (solo HEAD request).
    """
    short_domains = ("amzn.to", "a.co", "amzn.com")
    if not any(d in url for d in short_domains):
        return url
    try:
        # HEAD request rápido para seguir redirects
        r = requests.head(url, allow_redirects=True, timeout=10, headers={
            "User-Agent": HEADERS["User-Agent"]
        })
        final = r.url
        # Limpiar parámetros innecesarios pero conservar el path con ASIN
        if "amazon.com" in final and "/dp/" in final:
            print(f"   🔗 Resuelto: {url} → {final[:80]}")
            return final
    except Exception as e:
        print(f"   ⚠️  No se pudo resolver URL corta: {e}")
    return url

# ─── ScraperAPI ──────────────────────────────────────────────────────────────

def scraper_url(url: str) -> str:
    """Envuelve la URL con ScraperAPI si hay key disponible."""
    if SCRAPER_KEY:
        return f"https://api.scraperapi.com?api_key={SCRAPER_KEY}&url={quote_plus(url)}"
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

# ─── Cloudinary upload ───────────────────────────────────────────────────────

def upload_to_cloudinary(img_bytes: bytes, public_id: str) -> str:
    """
    Sube img_bytes a Cloudinary y retorna la URL pública.
    Usa la API REST directamente (sin SDK).
    Retorna "" si falla.
    """
    if not (CLD_NAME and CLD_KEY and CLD_SECRET):
        print("   ⚠️  Cloudinary: credenciales no configuradas")
        return ""
    try:
        timestamp = str(int(time.time()))
        # Firma: sha1("public_id=...&timestamp=...&secret")
        to_sign   = f"public_id={public_id}&timestamp={timestamp}{CLD_SECRET}"
        signature = hashlib.sha1(to_sign.encode()).hexdigest()

        upload_url = f"https://api.cloudinary.com/v1_1/{CLD_NAME}/image/upload"
        resp = requests.post(upload_url, data={
            "api_key":   CLD_KEY,
            "timestamp": timestamp,
            "public_id": public_id,
            "signature": signature,
        }, files={"file": ("deal.jpg", img_bytes, "image/jpeg")}, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            url  = data.get("secure_url", "")
            print(f"   ☁️  Cloudinary OK → {url[:60]}…")
            return url
        else:
            print(f"   ⚠️  Cloudinary error {resp.status_code}: {resp.text[:200]}")
            return ""
    except Exception as e:
        print(f"   ⚠️  Cloudinary excepción: {e}")
        return ""

# ─── Zernio / Instagram ──────────────────────────────────────────────────────

def make_instagram_caption(title: str, sale_price: float, orig_price: float,
                           discount_pct: int, affiliate_url: str) -> str:
    """Caption para Instagram: texto plano, sin HTML, con hashtags."""
    title_short = title[:100] + ("…" if len(title) > 100 else "")
    lines = [f"🔥 {discount_pct}% OFF — {title_short}"] if discount_pct else [f"🛍️ {title_short}"]

    if sale_price > 0:
        if orig_price > sale_price:
            savings = orig_price - sale_price
            lines.append(f"💰 ${orig_price:.2f} → ${sale_price:.2f}  (ahorras ${savings:.2f})")
        else:
            lines.append(f"💰 ${sale_price:.2f}")

    lines.append(f"🛒 {affiliate_url}")
    lines.append("")
    lines.append(
        "#discountpartner #ofertas #amazon #deals #ofertasamazon "
        "#ahorra #compras #descuentos #amazondeal #bargain"
    )
    return "\n".join(lines)


def post_to_instagram(img_url: str, caption: str) -> bool:
    """
    Publica en Instagram vía Zernio REST API.
    img_url debe ser una URL pública accesible (Cloudinary).
    Retorna True si se publicó exitosamente.
    """
    if not ZERNIO_KEY:
        print("   ⚠️  Zernio: ZERNIO_API_KEY no configurada")
        return False
    if not img_url:
        print("   ⚠️  Zernio: sin URL de imagen para Instagram")
        return False
    try:
        resp = requests.post(
            f"{ZERNIO_API}/v1/posts",
            headers={
                "Authorization": f"Bearer {ZERNIO_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "content": caption,
                "mediaItems": [{"type": "image", "url": img_url}],
                "platforms": [{"platform": "instagram", "accountId": ZERNIO_IG_ACCT}],
                "publishNow": True,
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            post_id = data.get("id") or data.get("postId") or "ok"
            print(f"   📸 Instagram OK → post {post_id}")
            return True
        else:
            print(f"   ⚠️  Zernio error {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"   ⚠️  Zernio excepción: {e}")
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
        time.sleep(1.0)
        fetch_url = scraper_url(url)
        resp = session.get(fetch_url, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Título ──────────────────────────────────────────────────────────
        for sel, attrs in [("span", {"id": "productTitle"}), ("h1", {"id": "title"})]:
            el = soup.find(sel, attrs)
            if el:
                t = _clean(el.get_text())
                if len(t) > 5:
                    result["title"] = t
                    break

        # ── Precio de venta ──────────────────────────────────────────────────
        # PRIMERO intentar JSON-LD (más confiable, datos estructurados de Amazon)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                offers = None
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    offers = ld.get("offers", {})
                elif isinstance(ld, list):
                    for item in ld:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            offers = item.get("offers", {})
                            break
                if offers and isinstance(offers, dict):
                    p = float(offers.get("price", 0) or 0)
                    if p > 0:
                        result["sale_price"] = p
                        break
            except Exception:
                pass

        # Si JSON-LD no dio precio, usar selectores CSS
        # NOTA: .a-text-price es el PRECIO TACHADO (MSRP), NO el precio de oferta
        if result["sale_price"] == 0:
            for selector in [
                ".apexPriceToPay .a-offscreen",
                ".reinventPricePriceToPayMargin .a-offscreen",
                "#priceblock_dealprice",
                "#priceblock_saleprice",
                "#price_inside_buybox",
                "#priceblock_ourprice",
                "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen",
                "#tp_price_block_total_price_ww .a-offscreen",
                "#newBuyBoxPrice",
            ]:
                el = soup.select_one(selector)
                if el:
                    p = _price(el.get_text())
                    if p > 0:
                        result["sale_price"] = p
                        break

        # ── Precio original / Precio recomendado (tachado) ─────────────────
        # ORDEN IMPORTANTE: basisPrice primero = "Precio recomendado" de Amazon.
        # .a-text-price puede capturar precios por unidad (onza, ml) — va al final.
        # Condición: el precio original DEBE ser mayor al precio de venta.
        for selector in [
            "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen",
            ".basisPrice .a-offscreen",              # "Precio recomendado" Amazon
            ".a-price[data-a-strike=\'true\'] .a-offscreen",
            "#listPrice",
            ".priceBlockStrikePriceString",
            ".a-text-strike",
            ".a-text-price .a-offscreen",            # último recurso (puede ser por unidad)
        ]:
            el = soup.select_one(selector)
            if el:
                p = _price(el.get_text())
                # Solo aceptar si es MAYOR al precio de venta (evita precios por unidad)
                if p > 0 and p > result["sale_price"] and p != result["sale_price"]:
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
    print("   🔗  Discount Partner — Manual v5 + ScraperAPI + Instagram")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   ScraperAPI: {'✅ activo' if SCRAPER_KEY else '❌ sin key'}")
    print(f"   Instagram:  {'✅ activo' if ZERNIO_KEY else '❌ sin ZERNIO_API_KEY'}")
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

    sent, ig_sent, results = 0, 0, []

    for i, deal in enumerate(deal_list, 1):
        product_url = deal["url"]
        # Precios provistos por el usuario (opcionales)
        user_sale = float(deal.get("sale_price") or 0)
        user_orig = float(deal.get("orig_price") or 0)
        user_disc = int(deal.get("discount_pct") or 0)

        print(f"\n── Producto #{i} ────────────────────────────────")
        print(f"   URL: {product_url[:70]}")

        # Resolver links cortos (amzn.to, a.co) antes de scrapearlo
        product_url = resolve_amazon_url(product_url)

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

        # VALIDACIÓN: orig_price no puede ser ≤ sale_price (error de scraping)
        if orig_price > 0 and orig_price <= sale_price:
            print(f"   ⚠️  Precio original (${orig_price:.2f}) ≤ venta (${sale_price:.2f}) — error scraping, recalculando desde discount_pct")
            orig_price = 0

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

        # ── Imagen diseñada para Instagram ───────────────────────────────────
        post_bytes = b""
        if sale_price > 0:
            try:
                deal_obj = Deal(
                    title=title, original_price=orig_price or sale_price,
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
                print(f"   🎨 Imagen diseñada: {len(post_bytes)//1024} KB")
            except Exception as e:
                print(f"   ⚠️  Error imagen: {e}")

        # ── Enviar a Telegram ─────────────────────────────────────────────────
        if post_bytes:
            tg_ok = send_telegram_photo(post_bytes, caption)
        elif img_bytes:
            tg_ok = send_telegram_photo(img_bytes, caption)
        else:
            tg_ok = send_telegram_text(caption)

        status = "✅" if tg_ok else "❌"
        print(f"   {status} Telegram")
        if tg_ok:
            sent += 1

        # ── Publicar en Instagram (solo si tenemos imagen diseñada) ───────────
        ig_ok = False
        if post_bytes and ZERNIO_KEY:
            print("   📤 Subiendo imagen a Cloudinary…")
            public_id = f"discountpartner/deal_{asin or datetime.now().strftime('%Y%m%d%H%M%S')}_{i}"
            img_public_url = upload_to_cloudinary(post_bytes, public_id)

            if img_public_url:
                ig_caption = make_instagram_caption(
                    title, sale_price, orig_price, discount_pct, affiliate_url
                )
                print("   📸 Publicando en Instagram vía Zernio…")
                ig_ok = post_to_instagram(img_public_url, ig_caption)
                if ig_ok:
                    ig_sent += 1
        elif not ZERNIO_KEY:
            print("   ⏭️  Instagram omitido (ZERNIO_API_KEY no configurada)")
        elif not post_bytes:
            print("   ⏭️  Instagram omitido (sin imagen diseñada)")

        ig_status = "✅" if ig_ok else ("⏭️" if not ZERNIO_KEY or not post_bytes else "❌")
        print(f"   {ig_status} Instagram")

        results.append({
            "url": product_url, "asin": asin, "title": title,
            "sale_price": sale_price, "orig_price": orig_price, "discount_pct": discount_pct,
            "image_found": bool(img_bytes), "sent_telegram": tg_ok, "sent_instagram": ig_ok,
        })
        time.sleep(0.8)

    print(f"\n{'═'*55}")
    print(f"✨ Telegram:  {sent}/{len(deal_list)} enviados")
    print(f"📸 Instagram: {ig_sent}/{len(deal_list)} publicados")

    Path(__file__).parent.joinpath("deals_output.json").write_text(
        json.dumps({"generated_at": datetime.now().isoformat(),
                    "mode": "manual_v5", "sent_telegram": sent, "sent_instagram": ig_sent,
                    "total": len(deal_list), "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8"
    )

if __name__ == "__main__":
    run_url_pipeline()
