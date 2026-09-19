"""
run_pipeline.py — Discount Partner (Telegram Edition)
Busca las mejores 10 ofertas y las envía por Telegram.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "requests", "--break-system-packages", "-q"])
    import requests

from deal_finder import AmazonDealFinder


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            print(f"   ⚠️  Telegram error: {data.get('description', data)}")
            return False
        return True
    except Exception as e:
        print(f"   ⚠️  Error enviando Telegram: {e}")
        return False


def load_config() -> dict:
    cfg = {}
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    env_map = {
        "AMAZON_AFFILIATE_TAG": ("amazon", "affiliate_tag"),
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


def run_pipeline(count: int = 10, dry_run: bool = False):
    print("\n" + "═" * 55)
    print("   🛍️  Discount Partner — Pipeline Telegram")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} COT")
    print("═" * 55 + "\n")

    cfg = load_config()
    if not check_config(cfg):
        sys.exit(1)

    affiliate_tag = cfg["amazon"]["affiliate_tag"]
    tg_token      = cfg["telegram"]["token"]
    tg_chat_id    = cfg["telegram"]["chat_id"]
    min_discount  = cfg.get("amazon", {}).get("min_discount", 30)

    finder = AmazonDealFinder(affiliate_tag=affiliate_tag, min_discount=min_discount)
    deals  = finder.find_deals(count=count)

    if not deals:
        msg = "⚠️ <b>Discount Partner</b>\nNo se encontraron ofertas en este momento."
        print("\n" + msg)
        if not dry_run:
            send_telegram(tg_token, tg_chat_id, msg)
        return

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    header  = (
        f"🛍️ <b>Discount Partner</b> — {len(deals)} ofertas\n"
        f"🕐 {now_str} COT\n"
        f"─────────────────────────\n"
        f"Categorías: Ropa de marca · Zapatos · Tecnología\n"
        f"Filtro: ≥{min_discount}% descuento"
    )

    print(f"\n📲 Enviando {len(deals)} deals por Telegram...\n")

    if dry_run:
        print("--- DRY RUN ---")
        print(header)
        for i, deal in enumerate(deals, 1):
            print(f"\n{'─'*40}")
            print(deal.telegram_msg(i))
        return

    if send_telegram(tg_token, tg_chat_id, header):
        print("   ✅ Encabezado enviado")
    time.sleep(1)

    sent = 0
    for i, deal in enumerate(deals, 1):
        if send_telegram(tg_token, tg_chat_id, deal.telegram_msg(i)):
            print(f"   ✅ Deal #{i} — {deal.category} [{deal.discount_pct}% OFF]")
            sent += 1
        else:
            print(f"   ❌ Deal #{i} falló")
        time.sleep(0.5)

    print(f"\n✨ Pipeline completado: {sent}/{len(deals)} deals enviados.")

    out = {
        "generated_at": datetime.now().isoformat(),
        "sent": sent,
        "total": len(deals),
        "deals": [d.to_dict() for d in deals],
    }
    with open("deals_output.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("💾 Resultados guardados en: deals_output.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count",   type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_pipeline(count=args.count, dry_run=args.dry_run)
