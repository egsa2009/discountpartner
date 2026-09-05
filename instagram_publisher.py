"""
instagram_publisher.py — Discount Partner Automation
Publica una imagen en Instagram via Graph API.

Uso:
    python instagram_publisher.py \
        --image post.png \
        --caption "caption del post" \
        --token TU_ACCESS_TOKEN \
        --account-id TU_IG_ACCOUNT_ID \
        --image-url https://tu-servidor.com/post.png   # opcional, ver nota abajo

NOTA sobre la imagen:
  La Instagram Graph API requiere que la imagen sea accesible públicamente
  por URL. Opciones:
  1. Imgbb (gratis): subir la imagen y usar la URL que da.
  2. Un bucket S3/R2 público.
  3. Cualquier hosting de imágenes.
  El script sube automáticamente a Imgbb si provees --imgbb-key.
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "requests", "--break-system-packages", "-q"])
    import requests


# ─── Configuración ──────────────────────────────────────────────────────────────

GRAPH_API = "https://graph.instagram.com/v20.0"


# ─── Upload a Imgbb (host de imágenes gratuito) ─────────────────────────────────

def upload_to_cloudinary(image_path: str, cloud_name: str, api_key: str, api_secret: str) -> str:
    """
    Sube la imagen a Cloudinary y retorna la URL pública HTTPS.
    Cloudinary es compatible con Instagram Graph API.
    Cuenta gratuita en: https://cloudinary.com
    """
    import hashlib
    print("   📤 Subiendo imagen a Cloudinary...")

    timestamp = int(time.time())
    params_to_sign = f"timestamp={timestamp}"
    signature = hashlib.sha1(f"{params_to_sign}{api_secret}".encode()).hexdigest()

    with open(image_path, "rb") as f:
        resp = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            files={"file": f},
            data={"api_key": api_key, "timestamp": timestamp, "signature": signature}
        )

    result = resp.json()
    if "secure_url" not in result:
        raise RuntimeError(f"Error subiendo a Cloudinary: {result}")

    url = result["secure_url"]
    print(f"   ✅ Imagen disponible en: {url[:70]}...")
    return url


# ─── Instagram Graph API ────────────────────────────────────────────────────────

class InstagramPublisher:
    def __init__(self, account_id: str, access_token: str):
        self.account_id = account_id
        self.token = access_token

    def _api(self, method: str, endpoint: str, **kwargs) -> dict:
        """Hace una llamada a la Graph API."""
        url = f"{GRAPH_API}/{endpoint}"
        params = {"access_token": self.token}
        if method == "GET":
            resp = requests.get(url, params={**params, **kwargs.get("params", {})})
        else:
            resp = requests.post(url, params=params, data=kwargs.get("data", {}))
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Graph API error: {data['error']}")
        return data

    def verify_account(self) -> dict:
        """Verifica que el token y account ID son válidos."""
        data = self._api("GET", self.account_id,
                         params={"fields": "id,username,followers_count,media_count"})
        print(f"   ✅ Cuenta verificada: {data.get('username')} "
              f"({data.get('followers_count', 0):,} seguidores)")
        return data

    def create_media_container(self, image_url: str, caption: str) -> str:
        """
        Paso 1: Crea el contenedor de media.
        Retorna el container_id.
        """
        print("   📦 Creando contenedor de media...")
        data = self._api("POST", f"{self.account_id}/media",
                         data={
                             "image_url": image_url,
                             "caption": caption,
                             "media_type": "IMAGE"
                         })
        container_id = data["id"]
        print(f"   ✅ Container ID: {container_id}")
        return container_id

    def wait_for_container(self, container_id: str, max_wait: int = 60) -> bool:
        """
        Espera a que el container esté listo para publicar.
        """
        print("   ⏳ Esperando que el container esté listo...")
        for attempt in range(max_wait // 5):
            time.sleep(5)
            data = self._api("GET", container_id,
                             params={"fields": "status_code"})
            status = data.get("status_code", "")
            if status == "FINISHED":
                print("   ✅ Container listo.")
                return True
            elif status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Container falló con status: {status}")
            print(f"      Status: {status} (intento {attempt + 1})")
        raise RuntimeError("Tiempo de espera agotado para el container.")

    def publish_container(self, container_id: str) -> str:
        """
        Paso 2: Publica el container.
        Retorna el media_id del post publicado.
        """
        print("   🚀 Publicando post en Instagram...")
        data = self._api("POST", f"{self.account_id}/media_publish",
                         data={"creation_id": container_id})
        media_id = data["id"]
        print(f"   🎉 Post publicado! Media ID: {media_id}")
        return media_id

    def get_post_url(self, media_id: str) -> str:
        """Obtiene la URL del post publicado."""
        data = self._api("GET", media_id, params={"fields": "permalink"})
        return data.get("permalink", "")

    def update_bio_link(self, affiliate_url: str) -> bool:
        """
        Actualiza el link en la bio del perfil de Instagram.
        Se llama después de publicar para que los seguidores puedan comprar.
        """
        print(f"   🔗 Actualizando link en bio → {affiliate_url[:60]}...")
        try:
            self._api("POST", self.account_id, data={"website": affiliate_url})
            print("   ✅ Bio actualizada con el link del deal.")
            return True
        except Exception as e:
            print(f"   ⚠️  No se pudo actualizar la bio: {e}")
            return False

    def publish(self, image_url: str, caption: str) -> dict:
        """
        Pipeline completo: crear container → esperar → publicar.
        """
        container_id = self.create_media_container(image_url, caption)
        self.wait_for_container(container_id)
        media_id = self.publish_container(container_id)
        post_url = self.get_post_url(media_id)
        return {
            "media_id": media_id,
            "post_url": post_url,
            "container_id": container_id
        }


    def publish_story_with_link(self, image_url: str, affiliate_url: str) -> dict:
        """
        Publica una historia de Instagram con link sticker clickeable.
        Las historias permiten links directos — mejor conversión que 'link en bio'.
        """
        print("   📖 Creando historia con link de afiliado...")
        # Paso 1: Crear container de historia con link sticker
        container_data = self._api("POST", f"{self.account_id}/media", data={
            "image_url": image_url,
            "media_type": "STORIES",
            "link_sticker": '{"link_url": "' + affiliate_url + '"}',
        })
        container_id = container_data["id"]
        print(f"   ✅ Container de historia creado: {container_id}")

        # Paso 2: Esperar que esté listo
        self.wait_for_container(container_id)

        # Paso 3: Publicar la historia
        print("   🚀 Publicando historia en Instagram...")
        pub_data = self._api("POST", f"{self.account_id}/media_publish",
                             data={"creation_id": container_id})
        story_id = pub_data["id"]
        print(f"   🎉 ¡Historia publicada con link! Story ID: {story_id}")
        return {"story_id": story_id, "affiliate_url": affiliate_url}


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discount Partner — Instagram Publisher")
    parser.add_argument("--image",      required=True, help="Ruta del PNG a publicar")
    parser.add_argument("--caption",    required=True, help="Texto del post (caption)")
    parser.add_argument("--token",      required=True, help="Instagram Graph API Access Token")
    parser.add_argument("--account-id", required=True, help="Instagram Business Account ID")
    parser.add_argument("--image-url",  help="URL pública de la imagen (si ya está alojada)")
    parser.add_argument("--imgbb-key",  help="API Key de Imgbb para subir la imagen (gratis)")
    parser.add_argument("--output",     default="publish_result.json", help="Resultado en JSON")
    args = parser.parse_args()

    publisher = InstagramPublisher(args.account_id, args.token)

    # Verificar credenciales
    print("\n🔐 Verificando credenciales de Instagram...")
    publisher.verify_account()

    # Obtener URL pública de la imagen
    if args.image_url:
        public_url = args.image_url
        print(f"   ℹ️  Usando URL de imagen provista: {public_url[:70]}")
    elif args.imgbb_key:
        public_url = upload_to_imgbb(args.image, args.imgbb_key)
    else:
        print("❌ Necesitas proveer --image-url O --imgbb-key para subir la imagen.")
        print("   Obtén una API key gratis en: https://api.imgbb.com/")
        sys.exit(1)

    # Publicar
    print(f"\n📲 Publicando en Instagram (@discountpartner)...")
    result = publisher.publish(public_url, args.caption)

    # Guardar resultado
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n✨ ¡Publicación exitosa!")
    print(f"   📎 Ver post: {result.get('post_url', 'N/A')}")
    print(f"   💾 Resultado guardado en: {args.output}")


if __name__ == "__main__":
    main()
