"""
process_urls.py v3 — Procesa deals con precios provistos manualmente por el usuario.
Recibe PRODUCT_DEALS como JSON: [{url, sale_price, orig_price, discount_pct}, ...]
Extrae título e imagen de Amazon (best-effort), genera imagen Instagram, envía a Telegram.
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
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language":  "es-CO,es;q=0.9,en;q=0.8",
    "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding":  "gzip, deflate, br",
    "DNT":              "1",
    "Connection":       "keep-alive",
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

# ─── Amazon scraper (best-effort) ───────────────────────────────────────────

def _price(text: str) -> float:
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
    Intenta extraer título e imagen de Amazon (best-effort).
    Los precios NO se intentan extraer aquí — los provee el usuario.
    """
    result = {
        "title": "", "image_url": "", "image_bytes": b"", "error": None,
    }
    try:
        time.sleep(1.5)
        resp = session.get(url, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Título ──────────────────────────────────────────────────────────
        for selector in [
            ("span", {"id": "productTitle"}),
            ("h1",   {"id": "title"}),
            ("h1",   {"class": "a-size-large"}),
        ]:
            el = soup.find(*selector)
            if el:
                t = _clean(el.get_text())
                if len(t) > 5:
                    result["title"] = t
                    break

        # ── Imagen ──────────────────────────────────────────────────────────
        img_url = ""
        for img_id in ["landingImage", "imgBlkFront", "main-image"]:
            img_el = soup.find("img", {"id": img_id})
            if img_el:
                img_url = (img_el.get("data-old-hires") or
                           img_el.get("data-a-hires") or
                           img_el.get("src", ""))
                if not img_url or "data:" in img_url:
                    dynamic = img_el.get("data-a-dynamic-image", "{}")
                    try:
                        imgs = json.loads(dynamic)
                        if imgs:
                            img_url = max(imgs, key=lambda u: imgs[u][0])
                    except Exception:
                        pass
                if img_url and img_url.startswith("http"):
                    break

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
            result["error"] = "Título no encontrado (Amazon bloqueó el acceso)"

    except Exception as e:
        result["error"] = str(e)

    return result

# ─── Caption de Telegram ────────────────────────────────────────────────────

def make_caption(index: int, title: str, sale_price: float, original_price: float,
                 discount_pct: int, affiliate_url: str, asin: str,
                 affiliate_tag: str) -> str:
    title_short = title[:120] + ("…" if len(title) > 120 else "")
    short_url = f"https://www.amazon.com/dp/{asin}?tag={affiliate_tag}" if asin else affiliate_url
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
        f"&ship-to-country=CO&tag={affiliate_tag}"
    )

    lines = [
        f"{emoji} <b>OFERTA #{index} — {cat_name}</b>",
        f"📦 {title_short}",
        "",
    ]
    if discount_pct > 0 and sale_price > 0:
        lines += [
            f"💰 <s>${original_price:.2f}</s> → <b>${sale_price:.2f}</b>",
            f"🔥 <b>{discount_pct}% OFF</b>" + (f" — Ahorras ${savings:.2f}" if savings > 0 else ""),
            "",
        ]
    elif sale_price > 0:
        lines += [f"💰 <b>${sale_price:.2f}</b>", ""]

    lines += [
        f"🛒 <b>Comprar:</b> {short_url}",
        f"🌎 <b>Buscar en Amazon Colombia:</b> {colombia_url}",
    ]
    return "\n".join(lines)

# ─── Pipeline ────────────────────────────────────────────────────────────────

def run_url_pipeline():
    print("\n" + "═" * 55)
    print("   🔗  Discount Partner — Modo Manual v3")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} COT")
    print("═" * 55 + "\n")

    deals_raw     = os.environ.get("PRODUCT_DEALS", "")
    tg_token      = os.environ.get("TELEGRAM_TOKEN", "")
    tg_chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")
    affiliate_tag = os.environ.get("AMAZON_AFFILIATE_TAG", "discountpartn-20")

    if not deals_raw:
        print("❌ No se recibieron deals (PRODUCT_DEALS vacío)")
        sys.exit(1)
    if not tg_token or not tg_chat_id:
        print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        sys.exit(1)

    try:
        deal_list = json.loads(deals_raw)
        if not isinstance(deal_list, list):
            raise ValueError("PRODUCT_DEALS debe ser un array JSON")
    except Exception as e:
        print(f"❌ Error parseando PRODUCT_DEALS: {e}")
        sys.exit(1)

    # Filtrar URLs válidas
    def _is_amz(u):
        u = u.lower()
        return any(d in u for d in ["amazon", "amzn.to", "a.co/d/"])

    deal_list = [d for d in deal_list if d.get("url") and _is_amz(d["url"])]
    print(f"📋 {len(deal_list)} deal(s) recibido(s):\n")
    for i, d in enumerate(deal_list, 1):
        sp = d.get("sale_price", 0) or 0
        op = d.get("orig_price", 0) or 0
        dc = d.get("discount_pct", 0) or 0
        prices_str = f"${sp:.2f}" + (f" / ${op:.2f} ({dc}% OFF)" if dc else "")
        print(f"   {i}. {d['url'][:70]}  {prices_str}")

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
    send_telegram_text(tg_token, tg_chat_id, header)
    time.sleep(1)

    sent    = 0
    results = []

    for i, deal in enumerate(deal_list, 1):
        product_url  = deal["url"]
        sale_price   = float(deal.get("sale_price") or 0)
        orig_price   = float(deal.get("orig_price") or 0)
        discount_pct = int(deal.get("discount_pct") or 0)

        print(f"\n── Producto #{i} ─────────────────────────────────")
        print(f"   URL: {product_url[:70]}…" if len(product_url) > 70 else f"   URL: {product_url}")

        asin = _extract_asin(product_url)
        print(f"   ASIN: {asin or '(no encontrado)'}")

        # Calcular descuento si faltan datos
        if sale_price > 0 and orig_price > sale_price and discount_pct == 0:
            discount_pct = int(round((1 - sale_price / orig_price) * 100))
        if sale_price > 0 and discount_pct > 0 and orig_price == 0:
            orig_price = round(sale_price / (1 - discount_pct / 100), 2)

        # Extraer título e imagen de Amazon (best-effort)
        print("   🔍 Intentando obtener título e imagen de Amazon…")
        data = fetch_amazon_product(product_url, session)

        title     = data["title"]
        img_bytes = data["image_bytes"]
        img_url   = data["image_url"]

        if data["error"]:
            print(f"   ⚠️  Scraping: {data['error']}")

        # Fallback de título si Amazon bloquea
        if not title:
            if asin:
                title = f"Producto Amazon (ASIN: {asin})"
            else:
                title = "Oferta Seleccionada en Amazon"
            print(f"   ℹ️  Usando título de respaldo: {title}")
        else:
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
                print(f"   🎨 Imagen Instagram generada: {len(post_bytes)//1024} KB")
            except Exception as e:
                print(f"   ⚠️  Error generando imagen Instagram: {e}")

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

        results.append({
            "url": product_url, "asin": asin,
            "title": title, "sale_price": sale_price,
            "orig_price": orig_price, "discount_pct": discount_pct,
            "image_found": bool(img_bytes), "affiliate_url": affiliate_url, "sent": ok,
        })
        time.sleep(0.8)

    print(f"\n{'═'*55}")
    print(f"✨ Completado: {sent}/{len(deal_list)} productos enviados a Telegram")

    out = {
        "generated_at": datetime.now().isoformat(),
        "mode": "manual_v3", "sent": sent, "total": len(deal_list),
        "results": results,
    }
    Path(__file__).parent.joinpath("deals_output.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

if __name__ == "__main__":
    run_url_pipeline()
