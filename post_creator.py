"""
post_creator.py — Genera imágenes 1080x1080 listas para Instagram
Usa Pillow. Fuentes: sistema Ubuntu o fallback.
"""

import io
import os
import re
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

# ─── Paleta ───────────────────────────────────────────────────────────────────
BG        = (10,  10,  10)
SURFACE   = (22,  22,  22)
ORANGE    = (249, 115,  22)
ORANGE_DK = (200,  85,  10)
WHITE     = (255, 255, 255)
OFFWHITE  = (230, 230, 230)
MUTED     = (140, 140, 140)
GREEN     = ( 34, 197,  94)
STRIPE    = ( 30,  30,  30)   # franjas sutiles del fondo

SIZE = (1080, 1080)

# ─── Fuentes ──────────────────────────────────────────────────────────────────

def _load_fonts():
    """Carga fuentes del sistema o fallback de PIL."""
    candidates = [
        # Ubuntu / Debian
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Alternativas
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # macOS / Windows
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    bold_path = next((p for p in candidates if "Bold" in p and os.path.exists(p)), None)
    reg_path  = next((p for p in candidates if "Bold" not in p and os.path.exists(p)), None)

    def load(path, size):
        if path:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
        return ImageFont.load_default()

    return {
        "title":   load(bold_path, 42),
        "price_s": load(reg_path,  38),
        "price_b": load(bold_path, 80),
        "badge":   load(bold_path, 52),
        "label":   load(reg_path,  30),
        "brand":   load(bold_path, 36),
        "footer":  load(reg_path,  26),
    }


FONTS = None  # lazy load


def get_fonts():
    global FONTS
    if FONTS is None:
        FONTS = _load_fonts()
    return FONTS


# ─── Helpers de dibujo ───────────────────────────────────────────────────────

def _text_w(draw, text, font):
    try:
        return draw.textlength(text, font=font)
    except AttributeError:
        return draw.textsize(text, font=font)[0]


def _draw_centered(draw, y, text, font, color, canvas_w=1080):
    w = _text_w(draw, text, font)
    draw.text(((canvas_w - w) / 2, y), text, font=font, fill=color)
    return y + font.size + 6


def _draw_strikethrough(draw, y, text, font, color, canvas_w=1080):
    w = _text_w(draw, text, font)
    x = (canvas_w - w) / 2
    draw.text((x, y), text, font=font, fill=color)
    mid = y + font.size // 2
    draw.line([(x, mid), (x + w, mid)], fill=color, width=3)
    return y + font.size + 8


def _rounded_rect(draw, xy, radius, fill, outline=None, outline_w=0):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill,
                           outline=outline, width=outline_w)


# ─── Fondo ───────────────────────────────────────────────────────────────────

def _make_background(w=1080, h=1080) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    # Franjas diagonales sutiles
    for i in range(0, w + h, 60):
        draw.line([(i, 0), (0, i)], fill=STRIPE, width=1)
    return img


# ─── Imagen del producto ──────────────────────────────────────────────────────

def _paste_product_image(canvas: Image.Image, img_bytes: bytes,
                         box_x=90, box_y=110, box_w=900, box_h=520):
    """Centra y pega la imagen del producto sobre el canvas."""
    if not img_bytes:
        return

    try:
        prod = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

        # Quitar fondo blanco si la imagen tiene alpha transparente
        bg = Image.new("RGBA", prod.size, (10, 10, 10, 255))
        combined = Image.alpha_composite(bg, prod).convert("RGB")

        # Thumbnail proporcional
        combined.thumbnail((box_w, box_h), Image.LANCZOS)

        # Brillo / contraste ligero
        combined = ImageEnhance.Contrast(combined).enhance(1.05)

        # Pegar centrado en la caja
        px = box_x + (box_w - combined.width)  // 2
        py = box_y + (box_h - combined.height) // 2
        canvas.paste(combined, (px, py))
    except Exception as e:
        print(f"   ⚠️  Error pegando imagen: {e}")


# ─── Función principal ────────────────────────────────────────────────────────

def create_post(deal, output_path: str | None = None) -> Image.Image:
    """
    Genera una imagen 1080×1080 lista para Instagram.
    Si `output_path` se provee, guarda el archivo allí también.
    Retorna el objeto PIL.Image.
    """
    fonts = get_fonts()
    canvas = _make_background()
    draw   = ImageDraw.Draw(canvas)

    # ── Barra superior naranja ────────────────────────────────────────────────
    draw.rectangle([(0, 0), (1080, 88)], fill=ORANGE)
    logo_text = "🛍  DISCOUNT PARTNER"
    lw = _text_w(draw, logo_text, fonts["brand"])
    draw.text(((1080 - lw) / 2, 24), logo_text, font=fonts["brand"], fill=WHITE)

    # ── Badge de categoría ────────────────────────────────────────────────────
    cat_text = f"{deal.category_emoji}  {deal.category.upper()}"
    cw = _text_w(draw, cat_text, fonts["label"]) + 32
    cx = (1080 - cw) / 2
    _rounded_rect(draw, (cx, 96, cx + cw, 140), 20, fill=SURFACE, outline=ORANGE, outline_w=2)
    draw.text((cx + 16, 107), cat_text, font=fonts["label"], fill=ORANGE)

    # ── Imagen del producto ───────────────────────────────────────────────────
    # Área de la imagen: y=148 a y=668 (520 px de alto)
    img_bytes = getattr(deal, "image_bytes", b"")
    if img_bytes:
        _paste_product_image(canvas, img_bytes, 90, 148, 900, 520)
    else:
        # Placeholder
        draw.rectangle([(90, 148), (990, 668)], fill=SURFACE)
        ph = "Sin imagen disponible"
        pw = _text_w(draw, ph, fonts["label"])
        draw.text(((1080 - pw) / 2, 400), ph, font=fonts["label"], fill=MUTED)

    # ── Separador ────────────────────────────────────────────────────────────
    draw.rectangle([(0, 670), (1080, 672)], fill=ORANGE)

    # ── Título del producto ───────────────────────────────────────────────────
    y = 686
    max_chars = 44
    lines = textwrap.wrap(deal.title, width=max_chars)[:3]
    for line in lines:
        lw = _text_w(draw, line, fonts["title"])
        draw.text(((1080 - lw) / 2, y), line, font=fonts["title"], fill=OFFWHITE)
        y += fonts["title"].size + 6
    if len(textwrap.wrap(deal.title, width=max_chars)) > 3:
        # Puntos suspensivos
        draw.text((540 - 10, y - 10), "…", font=fonts["title"], fill=MUTED)

    # ── Precios ───────────────────────────────────────────────────────────────
    y += 18
    orig_text = f"${deal.original_price:.2f}"
    _draw_strikethrough(draw, y, orig_text, fonts["price_s"], MUTED)
    y += fonts["price_s"].size + 14

    sale_text = f"${deal.sale_price:.2f}"
    sw = _text_w(draw, sale_text, fonts["price_b"])
    draw.text(((1080 - sw) / 2, y), sale_text, font=fonts["price_b"], fill=WHITE)
    y += fonts["price_b"].size + 20

    # ── Badge de descuento ────────────────────────────────────────────────────
    badge_txt = f"  {deal.discount_pct}% OFF  "
    bw = _text_w(draw, badge_txt, fonts["badge"]) + 20
    bx = (1080 - bw) / 2
    _rounded_rect(draw, (bx, y, bx + bw, y + 74), 14, fill=ORANGE)
    draw.text((bx + 10, y + 10), badge_txt, font=fonts["badge"], fill=WHITE)
    y += 84

    # Ahorro
    savings_txt = f"Ahorras ${deal.savings():.2f}"
    aw = _text_w(draw, savings_txt, fonts["label"])
    draw.text(((1080 - aw) / 2, y), savings_txt, font=fonts["label"], fill=GREEN)

    # ── Footer ────────────────────────────────────────────────────────────────
    draw.rectangle([(0, 1022), (1080, 1080)], fill=SURFACE)
    footer_txt = "✈️  Verifica envío a Colombia · discountpartner.vercel.app"
    fw = _text_w(draw, footer_txt, fonts["footer"])
    draw.text(((1080 - fw) / 2, 1039), footer_txt, font=fonts["footer"], fill=MUTED)

    # ── Guardar si se indicó ruta ──────────────────────────────────────────────
    if output_path:
        canvas.save(output_path, "JPEG", quality=92)
        print(f"   💾 Imagen guardada: {output_path}")

    return canvas
