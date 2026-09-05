#!/usr/bin/env python3
"""
Convierte el token corto a Long-Lived Token (60 días)
y lo guarda automáticamente en config.json
"""
import json, requests, sys
from pathlib import Path
from datetime import datetime, timedelta

APP_ID     = "1065767132713743"
APP_SECRET = "5a96f3bb7782be3ebe055a41c617bbc2"
SHORT_TOKEN = "IGAAVOpTvxtFxBZAGFxN3hPSmI4aFBKbnBfZAVRTbXJDNEdDUjBlZAVZAIci04UjlmdUlBdU5iUmU3dmV5aWVPS1l2MnIzRWVzaWtESUhSc3FqVDBPaklwejNfSzcyUk9tYlJTeTYxLWpwYjY2ZAjM0RlZAHa011eEUzVmJ3bVEwcE4zZAwZDZD"

CONFIG_PATH = Path(__file__).parent / "config.json"

print("🔄 Convirtiendo token a Long-Lived Token (60 días)...")

resp = requests.get(
    "https://graph.facebook.com/v18.0/oauth/access_token",
    params={
        "grant_type": "fb_exchange_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "fb_exchange_token": SHORT_TOKEN,
    }
)
data = resp.json()

if "access_token" not in data:
    print(f"❌ Error: {data}")
    sys.exit(1)

long_token = data["access_token"]
expires_in = data.get("expires_in", 5183944)  # ~60 días en segundos
expiry = datetime.now() + timedelta(seconds=expires_in)

print(f"✅ Token generado. Válido hasta: {expiry.strftime('%Y-%m-%d')}")

# Guardar en config.json
config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
config["instagram"]["access_token"] = long_token
config["instagram"]["token_expiry"] = expiry.isoformat()
config["instagram"]["app_id"] = APP_ID
config["instagram"]["app_secret"] = APP_SECRET
CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"💾 Token guardado en config.json")
print(f"📅 Expira: {expiry.strftime('%d/%m/%Y')}")
print(f"\n🚀 Ahora puedes correr: python run_pipeline.py --count 1")
