"""
run_pipeline.py — Discount Partner (Telegram Edition con imágenes)
Busca deals → genera imagen Instagram → envía foto a Telegram.
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "requests", "--break-system-packages", "-q"])
    import requests

from deal_finder import AmazonDealFinder
from post_creator import create_post


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram_text(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
        data = r.json()
        if not data.get("ok"):
            print(f"   ⚠️  Telegram text error: {data.get('description', data)}")
            return False
        return True
    except Exception as e:
        print(f"   ⚠️  Error enviando texto Telegram: {e}")
        return False


def send_telegram_photo(token: str, chat_id: str, img_bytes: bytes,
                        caption: str = "") -> bool:
    """Envía una foto a Telegram con caption en HTML."""
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        r = requests.post(url, data={
            "chat_id": chat_id,
            "caption": caption[:1024],   # límite de Telegram
            "parse_mode": "HTML",
        }, files={
            "photo": ("deal.jpg", img_bytes, "image/jpeg"),
        }, timeout=30)
        data = r.json()
        if not data.get("ok"):
            print(f"   ⚠️  Telegram photo error: {data.get('description', data)}")
            return False
        return True
    except Exception as e:
        print(f"   ⚠️  Error enviando foto Telegram: {e}")
        return False


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = {}
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))

    env_map = {
        "AMAZON_AFFILIATE_TAG": ("amazon",   "affiliate_tag"),
        "TELEGRAM_TOKEN":       ("telegram", "token"),
        "TELEGRAM_CHAT_ID":     ("telegram", "chat_id"),
    }
    for env_key, (section, field) in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg.setdefault(section, {})[field] = val
    return cfg


def check_config(cfg: dict) -> bool:
    required = [
        ("amazon",   "affiliate_tag", "AMAZON_AFFILIATE_TAG"),
        ("telegram", "token",         "TELEGRAM_TOKEN"),
        ("telegram", "chat_id",       "TELEGRAM_CHAT_ID"),
    ]
    ok = True
    for section, field, env_name in required:
        if not cfg.get(section, {}).get(field):
            print(f"❌ Falta {section}.{field} (env: {env_name})")
            ok = False
    return ok


# ─── Pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline(count: int = 10, dry_run: bool = False):
    print("\n" + "═" * 55)
    print("   🛍️  Discount Partner — Pipeline Telegram + Instagram")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} COT")
    print("═" * 55 + "\n")

    cfg = load_config()
    if not check_config(cfg):
        sys.exit(1)

    tg_token      = cfg["telegram"]["token"]
    tg_chat_id    = cfg["telegram"]["chat_id"]
    affiliate_tag = cfg["amazon"]["affiliate_tag"]
    min_discount  = cfg["amazon"].get("min_discount", 30)

    # Directorio para imágenes Instagram
    img_dir = Path(__file__).parent / "instagram_posts"
    img_dir.mkdir(exist_ok=True)

    # Buscar deals
    finder = AmazonDealFinder(affiliate_tag=affiliate_tag, min_discount=min_discount)
    deals  = finder.find_deals(count=count)

    if not deals:
        msg = "⚠️ <b>Discount Partner</b>\nNo se encontraron ofertas en este momento."
        print("\n" + msg)
        if not dry_run:
            send_telegram_text(tg_token, tg_chat_id, msg)
        return

    # Encabezado
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    header  = (
        f"🛍️ <b>Discount Partner</b> — {len(deals)} ofertas\n"
        f"🕐 {now_str} COT\n"
        f"─────────────────────────\n"
        f"Categorías: Ropa · Zapatos · Tecnología\n"
        f"Filtro: ≥{min_discount}% descuento"
    )

    print(f"\n📲 Enviando {len(deals)} deals por Telegram (foto + caption)…\n")

    if dry_run:
        print("--- DRY RUN (no se envía nada) ---")
        print(header)
        for i, deal in enumerate(deals, 1):
            print(f"\n{'─'*40}")
            print(deal.telegram_caption(i))
            # Igual genera imagen para probar
            out_path = img_dir / f"post_{datetime.now().strftime('%Y%m%d_%H%M')}_{i}.jpg"
            create_post(deal, str(out_path))
        return

    # Encabezado de texto
    if send_telegram_text(tg_token, tg_chat_id, header):
        print("   ✅ Encabezado enviado")
    time.sleep(1)

    # Enviar cada deal como foto + caption
    sent = 0
    for i, deal in enumerate(deals, 1):
        print(f"\n   Deal #{i}: {deal.title[:60]}…")

        # Generar imagen Instagram
        out_path = img_dir / f"post_{datetime.now().strftime('%Y%m%d_%H%M')}_{i:02d}.jpg"
        try:
            img_canvas = create_post(deal, str(out_path))
            buf = io.BytesIO()
            img_canvas.save(buf, "JPEG", quality=92)
            img_bytes = buf.getvalue()
        except Exception as e:
            print(f"      ⚠️  Error generando imagen: {e}")
            img_bytes = b""

        caption = deal.telegram_caption(i)

        if img_bytes:
            ok = send_telegram_photo(tg_token, tg_chat_id, img_bytes, caption)
        else:
            # Fallback a texto si no hay imagen
            ok = send_telegram_text(tg_token, tg_chat_id, caption)

        if ok:
            print(f"   ✅ Deal #{i} enviado — {deal.category} [{deal.discount_pct}% OFF]")
            sent += 1
        else:
            print(f"   ❌ Deal #{i} falló")

        time.sleep(0.8)  # evitar rate-limit

    print(f"\n✨ Pipeline completado: {sent}/{len(deals)} deals enviados.")
    print(f"📁 Imágenes Instagram guardadas en: {img_dir}/")

    # Guardar JSON de resultados
    out = {
        "generated_at": datetime.now().isoformat(),
        "sent": sent, "total": len(deals),
        "deals": [d.to_dict() for d in deals],
    }
    out_path_json = Path(__file__).parent / "deals_output.json"
    out_path_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 Resultados guardados en: deals_output.json")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count",   type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_pipeline(count=args.count, dry_run=args.dry_run)
