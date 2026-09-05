"""
post_creator.py v4 — Discount Partner
Formato 1080x1350 (4:5) — sin emojis, imagen grande, sin espacio muerto.
"""

import argparse, json, sys
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "Pillow", "--break-system-packages", "-q"])
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    import requests
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "--break-system-packages", "-q"])
    import requests

W, H = 1080, 1350

# ── Paleta ────────────────────────────────────────────────────────────────────
ORANGE   = (255, 140,   0)
ORANGE_L = (255, 165,  40)
NAVY     = ( 10,  20,  50)
WHITE    = (255, 255, 255)
RED      = (215,  30,  30)
GREEN    = ( 28, 165,  75)
GOLD     = (255, 190,  15)
GRAY     = (155, 160, 175)
OFFWHITE = (247, 247, 251)
SHADOW   = (  0,   0,   0)

def font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf"  if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arial Bold.ttf" if bold else "C:/Windows/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try: return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default(size=size)

def cx(draw, text, fnt, width=W):
    return int((width - draw.textlength(text, font=fnt)) // 2)

def wrap_text(text, fnt, max_w, draw, max_lines=2):
    words, lines, cur = text.split(), [], []
    for w in words:
        test = " ".join(cur + [w])
        if draw.textlength(test, font=fnt) <= max_w:
            cur.append(w)
        else:
            if cur: lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines: break
    if cur and len(lines) < max_lines:
        line = " ".join(cur)
        if draw.textlength(line, font=fnt) > max_w:
            ratio = max_w / draw.textlength(line, font=fnt)
            line = line[:int(len(line)*ratio)-3] + "..."
        lines.append(line)
    return lines[:max_lines]

def rr(draw, xy, r, **kw):
    draw.rounded_rectangle(xy, radius=r, **kw)

def shadow_rect(canvas, xy, r, color=(0,0,0), alpha=40, blur=16):
    s = Image.new("RGBA", (W, H), (0,0,0,0))
    sd = ImageDraw.Draw(s)
    x1,y1,x2,y2 = xy
    sd.rounded_rectangle([x1+6, y1+6, x2+6, y2+6], radius=r, fill=(*color, alpha))
    s = s.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste(s, (0,0), s)

def get_product_img(url, size=640):
    if not url: return None
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=14)
        img = Image.open(BytesIO(r.content)).convert("RGBA")
        data = img.load()
        for x in range(img.width):
            for y in range(img.height):
                px = data[x, y]
                if px[0] > 226 and px[1] > 226 and px[2] > 226:
                    data[x, y] = (px[0], px[1], px[2], 0)
        # Reducir si es más grande, pero también agrandar si viene pequeña
        iw, ih = img.size
        scale = min(size / max(iw, ih, 1), 3.0)   # max 3x upscale
        new_w = max(int(iw * scale), 1)
        new_h = max(int(ih * scale), 1)
        if scale != 1.0:
            img = img.resize((new_w, new_h), Image.LANCZOS)
        return img
    except Exception as e:
        print(f"   sin imagen: {e}")
        return None


def create_post(deal: dict, output_path: str = "post.png") -> str:
    print(f"   Creando imagen para: {deal['title'][:50]}...")

    canvas = Image.new("RGB", (W, H), OFFWHITE)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # ── Fondo degradado suave ─────────────────────────────────────────────────
    for y in range(H):
        t = y / H
        c = int(247 - t * 8)
        draw.line([(0, y), (W, y)], fill=(c, c, c+4))

    # ══════════════════════════════════════════════════════════════════════════
    # HEADER  (0 → 118)
    # ══════════════════════════════════════════════════════════════════════════
    draw.rectangle([0, 0, W, 118], fill=NAVY)
    rr(draw, [28, 16, 238, 102], r=20, fill=ORANGE)
    draw.text((50, 20), "DISCOUNT", font=font(30, bold=True), fill=WHITE)
    draw.text((56, 58), "PARTNER",  font=font(30, bold=True), fill=WHITE)
    draw.text((258, 26), "OFERTA DEL DIA",  font=font(40, bold=True), fill=WHITE)
    draw.text((260, 76), "@discountpartner", font=font(22),            fill=(190, 205, 240))
    draw.rectangle([0, 116, W, 122], fill=ORANGE)

    # ══════════════════════════════════════════════════════════════════════════
    # IMAGEN PRODUCTO  (128 → 770)
    # ══════════════════════════════════════════════════════════════════════════
    IMG_Y1, IMG_Y2 = 128, 770
    shadow_rect(canvas, [42, IMG_Y1, W-42, IMG_Y2], r=26, alpha=36, blur=18)
    rr(draw, [42, IMG_Y1, W-42, IMG_Y2], r=26, fill=WHITE)

    prod = get_product_img(deal.get("image_url", ""), size=620)
    IMG_H = IMG_Y2 - IMG_Y1
    if prod:
        pw, ph = prod.size
        px_ = (W - pw) // 2
        py_ = IMG_Y1 + (IMG_H - ph) // 2
        # Sombra elíptica bajo producto
        sh = Image.new("RGBA", (pw, 32), (0,0,0,0))
        shd = ImageDraw.Draw(sh)
        shd.ellipse([pw//6, 4, pw*5//6, 28], fill=(0,0,0,38))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        canvas.paste(sh, (px_, py_+ph-10), sh)
        canvas.paste(prod, (px_, py_), prod)
    else:
        f_ph = font(90, bold=True)
        draw.text((cx(draw, "NO IMG", f_ph), IMG_Y1+IMG_H//2-50), "NO IMG", font=f_ph, fill=GRAY)

    # ── BADGE descuento ── centrado en esquina sup-der, totalmente dentro del canvas
    disc = deal.get("discount_pct", 0)
    BR = 80                        # radio — encaja sin salirse
    bx  = W - BR - 18             # centro X: BR desde el borde + margen 18px
    by_ = IMG_Y1 + BR + 18        # centro Y: BR desde top de la tarjeta + margen 18px
    # Sombras concéntricas
    for br2, al in [(BR+16,10),(BR+10,16),(BR+4,24)]:
        draw.ellipse([bx-br2, by_-br2, bx+br2, by_+br2], fill=(0,0,0,al))
    draw.ellipse([bx-BR, by_-BR, bx+BR, by_+BR], fill=RED)
    draw.ellipse([bx-BR+6, by_-BR+6, bx+BR-6, by_+BR-6], outline=WHITE, width=5)

    pct_txt = f"{disc}%"
    f_pct = font(50, bold=True)
    f_off = font(24, bold=True)
    pct_w = int(draw.textlength(pct_txt, font=f_pct))
    off_w = int(draw.textlength("OFF",   font=f_off))
    # Centrar verticalmente el bloque pct+OFF dentro del círculo interior
    block_h = 55 + 28   # aprox altura pct + gap + OFF
    start_y = by_ - block_h // 2
    draw.text((bx - pct_w//2, start_y),      pct_txt, font=f_pct, fill=WHITE)
    draw.text((bx - off_w//2, start_y + 58), "OFF",   font=f_off, fill=WHITE)

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL PRECIOS  (780 → ~1218)
    # ══════════════════════════════════════════════════════════════════════════
    orig   = deal.get("original_price", 0)
    sale   = deal.get("sale_price", 0)
    savings = round(orig - sale, 2)
    rating  = deal.get("rating", 0)
    r_count = deal.get("rating_count", 0)
    title   = deal.get("title", "Producto Amazon")

    has_rating = bool(rating and rating > 0)
    f_title = font(30, bold=True)
    title_lines = wrap_text(title, f_title, W - 140, draw, max_lines=2)
    n_lines = len(title_lines)
    # Contenido necesario
    CONTENT_H = (n_lines * 40               # título
                + 20 + 16                   # línea naranja
                + 100                       # precios
                + 52                        # pill ahorro
                + (44 if has_rating else 0))# rating
    PANEL_Y1 = 780
    PANEL_Y2 = H - 18                       # panel llega hasta abajo
    # Centrar contenido verticalmente en el panel
    panel_inner = PANEL_Y2 - PANEL_Y1
    ty_offset = (panel_inner - CONTENT_H) // 2  # margen top para centrar

    shadow_rect(canvas, [42, PANEL_Y1, W-42, PANEL_Y2], r=26, alpha=42, blur=14)
    rr(draw, [42, PANEL_Y1, W-42, PANEL_Y2], r=26, fill=NAVY)

    # Título
    ty = PANEL_Y1 + ty_offset
    for line in title_lines:
        draw.text((cx(draw, line, f_title), ty), line, font=f_title, fill=WHITE)
        ty += 40

    # Línea naranja
    ty += 10
    draw.rectangle([90, ty, W-90, ty+3], fill=ORANGE)
    ty += 16

    # Precios: original tachado + nuevo grande
    f_orig = font(36)
    f_sale = font(86, bold=True)
    orig_txt = f"${orig:.2f}"
    sale_txt = f"${sale:.2f}"
    orig_w = int(draw.textlength(orig_txt, font=f_orig))
    sale_w = int(draw.textlength(sale_txt, font=f_sale))
    gap = 36
    total_w = orig_w + gap + sale_w
    start_x = (W - total_w) // 2

    draw.text((start_x, ty + 34), orig_txt, font=f_orig, fill=GRAY)
    draw.line([(start_x, ty+34+22), (start_x+orig_w, ty+34+22)], fill=RED, width=4)
    draw.text((start_x + orig_w + gap, ty), sale_txt, font=f_sale, fill=ORANGE)
    ty += 100

    # Pill ahorro
    f_save = font(28, bold=True)
    save_txt = f"  Ahorras ${savings:.2f}  "
    sw = int(draw.textlength(save_txt, font=f_save))
    px2 = (W - sw - 48) // 2
    rr(draw, [px2, ty, px2+sw+48, ty+46], r=23, fill=GREEN)
    draw.text((px2+24, ty+9), save_txt, font=f_save, fill=WHITE)
    ty += 58

    # Rating (si existe)
    if has_rating:
        stars = "★" * int(round(rating)) + "☆" * (5 - int(round(rating)))
        f_stars = font(26, bold=True)
        star_txt = f"{stars}  {rating}/5"
        if r_count: star_txt += f"  ({r_count:,} resenas)"
        draw.text((cx(draw, star_txt, f_stars), ty), star_txt, font=f_stars, fill=GOLD)

    # (Footer CTA eliminado — el link va en la historia)

    fmt = "JPEG" if output_path.lower().endswith((".jpg", ".jpeg")) else "PNG"
    save_opts = {"quality": 95, "optimize": True} if fmt == "JPEG" else {"optimize": True}
    canvas.save(output_path, fmt, **save_opts)
    kb = Path(output_path).stat().st_size // 1024
    print(f"   Imagen guardada: {output_path} ({kb} KB)")
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal",   required=True)
    parser.add_argument("--index",  type=int, default=0)
    parser.add_argument("--output", default="post.png")
    args = parser.parse_args()
    with open(args.deal, encoding="utf-8") as f:
        data = json.load(f)
    deals = data.get("deals", [])
    if not deals: sys.exit("Sin deals")
    if args.index >= len(deals): sys.exit("Indice fuera de rango")
    create_post(deals[args.index], args.output)

if __name__ == "__main__":
    main()
