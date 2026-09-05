"""
run_pipeline.py — Discount Partner Automation
Orquestador principal: busca deals → crea posts → publica en Instagram.

Configura tus credenciales en config.json antes de ejecutar.

Uso:
    python run_pipeline.py
    python run_pipeline.py --dry-run      # Solo busca y crea imágenes, no publica
    python run_pipeline.py --count 1      # Publicar solo 1 post
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Importar módulos del sistema
sys.path.insert(0, str(Path(__file__).parent))
from deal_finder import AmazonDealFinder
from post_creator import create_post
from instagram_publisher import InstagramPublisher, upload_to_cloudinary, update_github_redirect


# ─── Configuración ──────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "amazon": {
        "affiliate_tag": "TU-TAG-20",          # ← CAMBIAR
        "min_discount": 30,
        "deals_per_run": 2
    },
    "instagram": {
        "account_id": "TU_ACCOUNT_ID",             # ← CAMBIAR
        "access_token": "TU_ACCESS_TOKEN",         # ← CAMBIAR
        "cloudinary_cloud_name": "TU_CLOUD_NAME",  # ← CAMBIAR (cloudinary.com)
        "cloudinary_api_key": "TU_API_KEY",        # ← CAMBIAR
        "cloudinary_api_secret": "TU_API_SECRET",  # ← CAMBIAR
    },
    "schedule": {
        "post_times": ["09:00", "18:00"],
        "timezone": "America/Bogota"
    }
}


def load_config() -> dict:
    """Carga configuración desde config.json y/o variables de entorno (GitHub Actions)."""
    import copy
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    # Leer config.json si existe (local / Windows)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            file_cfg = json.load(f)
        for section, values in file_cfg.items():
            cfg.setdefault(section, {}).update(values)

    # Variables de entorno tienen prioridad — para GitHub Actions Secrets
    ig  = cfg["instagram"]
    amz = cfg["amazon"]
    env_map = {
        "INSTAGRAM_ACCESS_TOKEN":   (ig,  "access_token"),
        "INSTAGRAM_ACCOUNT_ID":     (ig,  "account_id"),
        "CLOUDINARY_CLOUD_NAME":    (ig,  "cloudinary_cloud_name"),
        "CLOUDINARY_API_KEY":       (ig,  "cloudinary_api_key"),
        "CLOUDINARY_API_SECRET":    (ig,  "cloudinary_api_secret"),
        "AMAZON_AFFILIATE_TAG":     (amz, "affiliate_tag"),
        "INSTAGRAM_APP_ID":         (ig,  "app_id"),
        "INSTAGRAM_APP_SECRET":     (ig,  "app_secret"),
    }
    for env_var, (section, key) in env_map.items():
        val = os.getenv(env_var)
        if val:
            section[key] = val

    return cfg


def check_config(cfg: dict) -> bool:
    """Verifica que la configuración tenga los valores necesarios."""
    issues = []
    tag = cfg["amazon"]["affiliate_tag"]
    if tag in ("TU-TAG-20", "", None):
        issues.append("  • amazon.affiliate_tag: Pon tu Amazon Associate Tag (ej: discountpartner-20)")

    token = cfg["instagram"]["access_token"]
    if token in ("TU_ACCESS_TOKEN", "", None):
        issues.append("  • instagram.access_token: Necesitas el token de Instagram Graph API")

    acct = cfg["instagram"]["account_id"]
    if acct in ("TU_ACCOUNT_ID", "", None):
        issues.append("  • instagram.account_id: Necesitas tu Instagram Business Account ID")

    cloud = cfg["instagram"].get("cloudinary_cloud_name")
    if cloud in ("TU_CLOUD_NAME", "", None):
        issues.append("  • instagram.cloudinary_cloud_name: Cloud name de Cloudinary (cloudinary.com)")

    if issues:
        print("❌ Configuración incompleta. Edita config.json:\n")
        for i in issues:
            print(i)
        return False
    return True


# ─── Pipeline ───────────────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False, count: int = None) -> dict:
    """
    Ejecuta el pipeline completo.
    dry_run=True: busca y crea imágenes pero no publica.
    """
    cfg = load_config()
    if not dry_run and not check_config(cfg):
        sys.exit(1)

    run_time = datetime.now()
    log = {
        "run_at": run_time.isoformat(),
        "dry_run": dry_run,
        "results": []
    }

    amazon_cfg = cfg["amazon"]
    ig_cfg     = cfg["instagram"]
    n_deals    = count or amazon_cfg.get("deals_per_run", 2)

    print(f"\n{'='*60}")
    print(f"  🛒 DISCOUNT PARTNER — Pipeline Automático")
    print(f"  {run_time.strftime('%A %d de %B %Y, %H:%M')}")
    print(f"  Modo: {'DRY RUN (sin publicar)' if dry_run else '🚀 PRODUCCIÓN'}")
    print(f"{'='*60}\n")

    # ── PASO 1: Buscar deals ──────────────────────────────────────────────────
    print("🔍 PASO 1: Buscando deals en Amazon...\n")
    finder = AmazonDealFinder(
        affiliate_tag=amazon_cfg["affiliate_tag"],
        min_discount=amazon_cfg.get("min_discount", 30)
    )
    deals = finder.find_deals(count=n_deals)

    if not deals:
        print("⚠️  No se encontraron deals. Abortando pipeline.")
        log["error"] = "No deals found"
        return log

    # Guardar deals al disco
    deals_path = Path(f"deals_{run_time.strftime('%Y%m%d_%H%M')}.json")
    with open(deals_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_at": run_time.isoformat(),
            "affiliate_tag": amazon_cfg["affiliate_tag"],
            "deals": [d.to_dict() for d in deals]
        }, f, ensure_ascii=False, indent=2)

    # ── PASO 2 y 3: Para cada deal → crear post → publicar ───────────────────
    publisher = None
    if not dry_run:
        publisher = InstagramPublisher(ig_cfg["account_id"], ig_cfg["access_token"])
        print("\n🔐 Verificando credenciales de Instagram...")
        publisher.verify_account()

    for i, deal in enumerate(deals):
        print(f"\n{'─'*50}")
        print(f"📦 Deal {i+1}/{len(deals)}: {deal.title[:55]}...")
        print(f"   💰 ${deal.sale_price:.2f} (antes ${deal.original_price:.2f}) — {deal.discount_pct}% OFF")

        deal_dict = deal.to_dict()
        result_entry = {"deal_title": deal.title, "discount_pct": deal.discount_pct}

        # Crear imagen
        print(f"\n🎨 PASO 2: Creando imagen del post...")
        img_dir = Path(__file__).parent / "Imagenes"
        img_dir.mkdir(exist_ok=True)
        img_path = str(img_dir / f"post_{run_time.strftime('%Y%m%d_%H%M')}_{i}.png")
        try:
            create_post(deal_dict, img_path)
            result_entry["image_path"] = img_path
        except Exception as e:
            print(f"   ❌ Error creando imagen: {e}")
            result_entry["error"] = f"Image creation failed: {e}"
            log["results"].append(result_entry)
            continue

        # Publicar
        if dry_run:
            print(f"\n⏭️  DRY RUN: se omitiría la publicación en Instagram.")
            result_entry["published"] = False
        else:
            print(f"\n📲 PASO 3: Subiendo imagen a Cloudinary...")
            try:
                public_url = upload_to_cloudinary(
                    img_path,
                    ig_cfg["cloudinary_cloud_name"],
                    ig_cfg["cloudinary_api_key"],
                    ig_cfg["cloudinary_api_secret"]
                )
                affiliate_url = deal_dict.get("affiliate_url") or deal_dict.get("url", "")

                # Actualizar redirect en GitHub Pages y publicar historia
                print(f"\n📖 PASO 4: Publicando historia en Instagram...")
                try:
                    gh_token = os.getenv("GITHUB_TOKEN", "")
                    if gh_token and affiliate_url:
                        pages_url = update_github_redirect(affiliate_url, i, gh_token)
                    else:
                        pages_url = affiliate_url  # fallback local
                        print(f"   ⚠️  Sin GITHUB_TOKEN — usando URL directa (no funcionará el link sticker)")

                    story_result = publisher.publish_story_with_link(public_url, pages_url)
                    result_entry.update({
                        "published": True,
                        "story_id": story_result.get("story_id"),
                        "story_link": pages_url,
                    })
                    print(f"   ✅ Historia publicada.")
                except Exception as e:
                    print(f"   ❌ Error publicando historia: {e}")
                    result_entry["error"] = f"Story publish failed: {e}"
                    result_entry["published"] = False
            except Exception as e:
                print(f"   ❌ Error subiendo imagen: {e}")
                result_entry["error"] = f"Upload failed: {e}"
                result_entry["published"] = False

        log["results"].append(result_entry)

        # Pausa entre posts para no saturar la API
        if i < len(deals) - 1 and not dry_run:
            print(f"\n⏳ Esperando 30 segundos antes del siguiente post...")
            time.sleep(30)

    # ── Resumen ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    published = sum(1 for r in log["results"] if r.get("published"))
    print(f"  ✅ Pipeline completado: {published}/{len(deals)} posts publicados")
    for r in log["results"]:
        emoji = "✅" if r.get("published") else ("⏭️" if dry_run else "❌")
        print(f"  {emoji} {r['deal_title'][:50]}...")
        if r.get("post_url"):
            print(f"     → {r['post_url']}")
    print(f"{'='*60}\n")

    # Guardar log
    log_path = f"run_log_{run_time.strftime('%Y%m%d_%H%M')}.json"
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"📄 Log guardado en: {log_path}")

    return log


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discount Partner — Pipeline Completo")
    parser.add_argument("--dry-run", action="store_true",
                        help="Ejecutar sin publicar en Instagram")
    parser.add_argument("--count", type=int, help="Número de posts a publicar")
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run, count=args.count)


if __name__ == "__main__":
    main()
