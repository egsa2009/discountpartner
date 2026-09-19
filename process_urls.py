"""
process_urls.py — Procesa URLs de Amazon pegadas manualmente por el usuario.
Extrae título, precio, imagen → genera imagen Instagram → envía a Telegram.

Recibe PRODUCT_URLS como variable de entorno (URLs separadas por coma).
"""

import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    from deal_finder import add_affiliate_tag, _extract_asin, Deal
    from post_creator import create_post
except ImportError as e:
    print(f"❌ Error importando módulos: {e}")
    sys.exit(1)

# ─── Configuración ──────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding":  "gzip, deflate, br",
    "DNT":              "1",
    "Connection":       "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ─── Telegram helpers ────────────────────────────────────────────────────────

def send_telegram_text(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"   ⚠️  Telegram text error: {e}")
        return False

def send_telegram_photo(token: str, chat_id: str, img_bytes: bytes, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        r = requests.post(url, data={
            "chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML",
        }, files={"photo": ("deal.jpg", img_bytes, "image/jpeg")}, timeout=30)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"   ⚠️  Telegram photo error: {e}")
        return False

# ─── Amazon scraper ──────────────────────────────────────────────────────────

def _price(text: str) -> float:
    """Extrae el primer precio en USD de un texto."""
    m = re.search(r"\$?([\d,]+\.?\d*)", text.replace(",", ""))
    if m:
        try:
            return float(m.group(1))
        except Exception:
            pass
    return 0.0

def _clean(text: str) -> str:
    return " ".join(text.split()).strip()

def fetch_amazon_product(url: str, session: requests.Session) -> dict:
    """
    Extrae datos del producto desde una página de Amazon.
    Retorna dict con: title, sale_price, original_price, discount_pct, image_url, image_bytes
    """
    result = {
        "title": "", "sale_price": 0.0, "original_price": 0.0,
        "discount_pct": 0, "image_url": "", "image_bytes": b"",
        "error": None,
    }

    try:
        # Pequeña pausa para no saturar Amazon
        time.sleep(1.5)
        resp = session.get(url, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Título ──────────────────────────────────────────────────────────
        title_el = soup.find("span", {"id": "productTitle"})
        if not title_el:
            title_el = soup.find("h1", {"id": "title"})
        if title_el:
            result["title"] = _clean(title_el.get_text())

        # ── Precio de venta (precio actual) ────────────────────────────────
        # Orden de prioridad de selectores
        price_selectors = [
            # Precio deal / oferta del día
            ("#priceblock_dealprice",      "text"),
            ("#priceblock_saleprice",      "text"),
            # Precio "core" de Amazon
            (".a-price .a-offscreen",      "text"),
            ("#price_inside_buybox",       "text"),
            ("#priceblock_ourprice",       "text"),
            (".apexPriceToPay .a-offscreen", "text"),
            (".reinventPricePriceToPayMargin .a-offscreen", "text"),
        ]
        for selector, _ in price_selectors:
            el = soup.select_one(selector)
            if el:
                p = _price(el.get_text())
                if p > 0:
                    result["sale_price"] = p
                    break

        # ── Precio original (tachado) ───────────────────────────────────────
        orig_selectors = [
            ".a-text-strike",
            "#priceblock_ourprice",          # a veces el "was" está aquí
            ".basisPrice .a-offscreen",
            ".priceBlockStrikePriceString",
            ".a-price[data-a-strike='true'] .a-offscreen",
            ".aok-relative .a-size-small .a-offscreen",
        ]
        for selector in orig_selectors:
            el = soup.select_one(selector)
            if el:
                p = _price(el.get_text())
                if p > 0 and p != result["sale_price"]:
                    result["original_price"] = p
                    break

        # ── Descuento desde la etiqueta de ahorro ──────────────────────────
        save_selectors = [
            ".savingsPercentage",
            "#savingsPercentage",
            ".a-color-price .a-size-large",  # "Save 45%"
        ]
        if result["original_price"] == 0 or result["sale_price"] == 0:
            for selector in save_selectors:
                el = soup.select_one(selector)
                if el:
                    m = re.search(r"(\d+)\s*%", el.get_text())
                    if m:
                        result["discount_pct"] = int(m.group(1))
                        break

        # Calcular descuento si tenemos ambos precios
        if result["sale_price"] > 0 and result["original_price"] > result["sale_price"]:
            result["discount_pct"] = int(round(
                (1 - result["sale_price"] / result["original_price"]) * 100
            ))
        elif result["sale_price"] > 0 and result["original_price"] == 0 and result["discount_pct"] > 0:
            # Calcular original desde el descuento
            result["original_price"] = round(
                result["sale_price"] / (1 - result["discount_pct"] / 100), 2
            )

        # Si solo hay precio de venta sin original, poner el mismo (no hay descuento calculable)
        if result["sale_price"] > 0 and result["original_price"] == 0:
            result["original_price"] = result["sale_price"]

        # ── Imagen ──────────────────────────────────────────────────────────
        img_url = ""
        # Imagen principal de alta resolución
        img_el = soup.find("img", {"id": "landingImage"})
        if not img_el:
            img_el = soup.find("img", {"id": "imgBlkFront"})
        if not img_el:
            img_el = soup.find("img", {"id": "main-image"})

        if img_el:
            # data-old-hires → imagen de mayor resolución
            img_url = (img_el.get("data-old-hires") or
                       img_el.get("data-a-hires") or
                       img_el.get("src", ""))
            # A veces está en JSON dentro del data-a-dynamic-image
            if not img_url or "data:" in img_url:
                dynamic = img_el.get("data-a-dynamic-image", "{}")
                try:
                    imgs = json.loads(dynamic)
                    if imgs:
                        # Elegir la de mayor resolución (mayor ancho)
                        img_url = max(imgs, key=lambda u: imgs[u][0])
                except Exception:
                    pass

        # Fallback: og:image
        if not img_url:
            og = soup.find("meta", property="og:image")
            if og:
                img_url = og.get("content", "")

        if img_url and img_url.startswith("http"):
            result["image_url"] = img_url
            try:
                r_img = session.get(img_url, timeout=10)
                if r_img.status_code == 200 and len(r_img.content) > 2000:
                    result["image_bytes"] = r_img.content
            except Exception as e:
                print(f"      ⚠️  Error descargando imagen: {e}")

        if not result["title"]:
            result["error"] = "No se pudo extraer el título (posible bloqueo de Amazon)"

    except Exception as e:
        result["error"] = str(e)

    return result

# ─── Caption de Telegram ────────────────────────────────────────────────────

def make_caption(index: int, title: str, sale_price: float, original_price: float,
                 discount_pct: int, affiliate_url: str, asin: str,
                 affiliate_tag: str, category: str = "Oferta") -> str:
    title_short = title[:120] + ("…" if len(title) > 120 else "")
    short_url = f"https://www.amazon.com/dp/{asin}?tag={affiliate_tag}" if asin else affiliate_url
    savings   = max(0.0, original_price - sale_price)

    # Emoji por categoría detectada
    cat_lower = title.lower()
    if any(w in cat_lower for w in ["shoe", "sneaker", "boot", "zapato", "nike", "adidas", "new balance", "hoka", "vans", "converse", "reebok"]):
        emoji, cat_name = "👟", "Zapatos Deportivos"
    elif any(w in cat_lower for w in ["shirt", "jacket", "pants", "dress", "coat", "jeans", "hoodie", "calvin", "tommy", "lacoste", "ralph", "armani", "boss", "levi", "puma", "champion"]):
        emoji, cat_name = "👗", "Ropa de Marca"
    elif any(w in cat_lower for w in ["ipad", "iphone", "macbook", "airpod", "samsung", "laptop", "tablet", "headphone", "speaker", "kindle", "echo", "nintendo", "sony", "lenovo", "dell", "hp", "asus", "pixel", "logitech", "bose"]):
        emoji, cat_name = "💻", "Tecnología"
    else:
        emoji, cat_name = "🛍️", "Oferta"

    # URL búsqueda Colombia
    brand_query = title.split("|")[0].strip()[:40].replace(" ", "+")
    colombia_url = (
        f"https://www.amazon.com/s?k={brand_query}"
        f"&i=fashion&deals-widget=%7B%22version%22%3A1%7D"
        f"&ship-to-country=CO&tag={affiliate_tag}"
    )

    lines = [
        f"{emoji} <b>OFERTA #{index} — {cat_name}</b>",
        f"📦 {title_short}",
        "",
    ]
    if discount_pct > 0:
        lines += [
            f"💰 <s>${original_price:.2f}</s> → <b>${sale_price:.2f}</b>",
            f"🔥 <b>{discount_pct}% OFF</b>" + (f" — Ahorras ${savings:.2f}" if savings > 0 else ""),
            "",
        ]
    else:
        lines += [f"💰 <b>${sale_price:.2f}</b>", ""]

    lines += [
        f"🛒 <b>Comprar:</b> {short_url}",
        f"🌎 <b>Buscar en Amazon Colombia:</b> {colombia_url}",
    ]
    return "\n".join(lines)

# ─── Pipeline ────────────────────────────────────────────────────────────────

def run_url_pipeline():
    print("\n" + "═" * 55)
    print("   🔗  Discount Partner — Modo Manual (URLs)")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} COT")
    print("═" * 55 + "\n")

    # Config
    urls_raw      = os.environ.get("PRODUCT_URLS", "")
    tg_token      = os.environ.get("TELEGRAM_TOKEN", "")
    tg_chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")
    affiliate_tag = os.environ.get("AMAZON_AFFILIATE_TAG", "discountpartn-20")

    if not urls_raw:
        print("❌ No se recibieron URLs (PRODUCT_URLS vacío)")
        sys.exit(1)
    if not tg_token or not tg_chat_id:
        print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        sys.exit(1)

    urls = [u.strip() for u in urls_raw.split(",") if "amazon" in u.strip().lower()]
    print(f"📋 {len(urls)} URL(s) recibida(s):\n")
    for i, u in enumerate(urls, 1):
        print(f"   {i}. {u[:80]}…" if len(u) > 80 else f"   {i}. {u}")

    session = requests.Session()
    session.headers.update(HEADERS)

    img_dir = Path(__file__).parent / "instagram_posts"
    img_dir.mkdir(exist_ok=True)

    # Encabezado Telegram
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    header  = (
        f"🔗 <b>Discount Partner</b> — {len(urls)} oferta{'' if len(urls)==1 else 's'}\n"
        f"🕐 {now_str} COT\n"
        f"─────────────────────────\n"
        f"Selección manual · Link afiliado incluido"
    )
    send_telegram_text(tg_token, tg_chat_id, header)
    time.sleep(1)

    sent = 0
    results = []

    for i, product_url in enumerate(urls, 1):
        print(f"\n── Producto #{i} ─────────────────────────────────")
        print(f"   URL: {product_url[:70]}…" if len(product_url) > 70 else f"   URL: {product_url}")

        asin = _extract_asin(product_url)
        print(f"   ASIN: {asin or '(no encontrado)'}")

        # Extraer datos del producto
        data = fetch_amazon_product(product_url, session)

        if data["error"]:
            print(f"   ⚠️  Error: {data['error']}")
            if not data["title"]:
                print("   ⚠️  Sin título — omitiendo")
                continue

        title        = data["title"]
        sale_price   = data["sale_price"]
        orig_price   = data["original_price"]
        discount_pct = data["discount_pct"]
        img_bytes    = data["image_bytes"]
        img_url      = data["image_url"]

        print(f"   📦 {title[:70]}")
        print(f"   💰 ${sale_price:.2f} (original: ${orig_price:.2f}, {discount_pct}% OFF)")
        print(f"   🖼️  Imagen: {'✅ ' + str(len(img_bytes)//1024) + ' KB' if img_bytes else '❌ sin imagen'}")

        # URL afiliado
        clean_url     = f"https://www.amazon.com/dp/{asin}" if asin else product_url
        affiliate_url = add_affiliate_tag(clean_url, affiliate_tag)

        # Caption
        caption = make_caption(
            index=i, title=title, sale_price=sale_price,
            original_price=orig_price, discount_pct=discount_pct,
            affiliate_url=affiliate_url, asin=asin, affiliate_tag=affiliate_tag,
        )

        # Crear imagen Instagram
        try:
            deal_obj = Deal(
                title=title, original_price=orig_price or sale_price,
                sale_price=sale_price or 0.0, discount_pct=discount_pct,
                product_url=clean_url, affiliate_url=affiliate_url,
                image_url=img_url, image_bytes=img_bytes,
                category="Manual", category_emoji="🔗", asin=asin,
            )
            out_path = img_dir / f"post_manual_{datetime.now().strftime('%Y%m%d_%H%M')}_{i:02d}.jpg"
            img_canvas = create_post(deal_obj, str(out_path))
            buf = io.BytesIO()
            img_canvas.save(buf, "JPEG", quality=92)
            post_bytes = buf.getvalue()
            print(f"   🎨 Imagen Instagram generada: {len(post_bytes)//1024} KB")
        except Exception as e:
            print(f"   ⚠️  Error generando imagen Instagram: {e}")
            post_bytes = b""

        # Enviar a Telegram
        if post_bytes:
            ok = send_telegram_photo(tg_token, tg_chat_id, post_bytes, caption)
        elif img_bytes:
            ok = send_telegram_photo(tg_token, tg_chat_id, img_bytes, caption)
        else:
            ok = send_telegram_text(tg_token, tg_chat_id, caption)

        if ok:
            print(f"   ✅ Enviado a Telegram")
            sent += 1
        else:
            print(f"   ❌ Error enviando a Telegram")

        results.append({**data, "url": product_url, "asin": asin,
                        "affiliate_url": affiliate_url, "sent": ok})
        time.sleep(0.8)

    print(f"\n{'═'*55}")
    print(f"✨ Completado: {sent}/{len(urls)} productos enviados a Telegram")

    # Guardar resultado
    out = {
        "generated_at": datetime.now().isoformat(),
        "mode": "manual", "sent": sent, "total": len(urls),
        "results": [{k: v for k, v in r.items() if k != "image_bytes"} for r in results],
    }
    Path(__file__).parent.joinpath("deals_output.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

if __name__ == "__main__":
    run_url_pipeline()
