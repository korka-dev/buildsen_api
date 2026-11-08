"""Fournisseurs d'envoi : le simulateur local (développement) et l'API WhatsApp Cloud de Meta.

Les deux offrent les mêmes opérations ; le reste du code ne sait pas lequel est actif.
Numéros : BUILDSEN garde 9 chiffres (77 123 45 67) ; Meta attend l'identifiant complet « 221771234567 ».
"""

import logging

import httpx

from ...config import settings
from ...utils import cuid

log = logging.getLogger("buildsen.wa")
MAX_TEXT = 3900  # Meta accepte 4096 caractères


class ProviderError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def wa_id(phone: str) -> str:
    return f"221{phone}"


class Provider:
    name = "base"

    def send_text(self, phone: str, body: str, buttons: list[tuple[str, str]] | None = None) -> str:
        raise NotImplementedError

    def send_audio(self, phone: str, mp3: bytes) -> str:
        raise NotImplementedError

    def send_template(
        self, phone: str, name: str, params: list[str], payloads: list[str] | None = None, code: str | None = None, lang: str = "fr",
    ) -> str:
        raise NotImplementedError

    def download(self, media_id: str) -> bytes:
        raise NotImplementedError


class SimProvider(Provider):
    """Simulateur : rien ne part chez Meta, tout est gardé dans le journal et affiché par la page /simulateur."""

    name = "sim"

    def send_text(self, phone, body, buttons=None):
        return "sim_" + cuid()

    def send_audio(self, phone, mp3):
        return "sim_" + cuid()

    def send_template(self, phone, name, params, payloads=None, code=None, lang="fr"):
        return "sim_" + cuid()

    def download(self, media_id):
        raise ProviderError("Le simulateur ne télécharge rien.")


class MetaProvider(Provider):
    """API WhatsApp Cloud : https://developers.facebook.com/docs/whatsapp/cloud-api"""

    name = "meta"

    def _base(self) -> str:
        return f"https://graph.facebook.com/{settings.whatsapp_api_version}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.whatsapp_access_token}"}

    def _post_message(self, payload: dict) -> str:
        try:
            r = httpx.post(f"{self._base()}/{settings.whatsapp_phone_number_id}/messages", headers=self._headers(),
                           json={"messaging_product": "whatsapp", **payload}, timeout=20)
        except httpx.HTTPError as e:
            raise ProviderError(f"Réseau : {type(e).__name__}") from e
        if r.status_code >= 300:
            err = (r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else None) or {}
            raise ProviderError(str(err.get("message", r.status_code))[:200], err.get("code"))
        return (r.json().get("messages") or [{}])[0].get("id") or "meta_" + cuid()

    def send_text(self, phone, body, buttons=None):
        body = body[:MAX_TEXT]
        if not buttons:
            return self._post_message({"to": wa_id(phone), "type": "text", "text": {"body": body, "preview_url": False}})
        return self._post_message({
            "to": wa_id(phone), "type": "interactive",
            "interactive": {
                "type": "button", "body": {"text": body},
                "action": {"buttons": [{"type": "reply", "reply": {"id": i[:256], "title": t[:20]}} for i, t in buttons[:3]]},
            },
        })

    def send_audio(self, phone, mp3):
        try:
            up = httpx.post(f"{self._base()}/{settings.whatsapp_phone_number_id}/media", headers=self._headers(),
                            data={"messaging_product": "whatsapp", "type": "audio/mpeg"}, files={"file": ("message.mp3", mp3, "audio/mpeg")}, timeout=30)
        except httpx.HTTPError as e:
            raise ProviderError(f"Réseau : {type(e).__name__}") from e
        if up.status_code >= 300:
            raise ProviderError(f"Envoi du fichier audio refusé ({up.status_code})")
        return self._post_message({"to": wa_id(phone), "type": "audio", "audio": {"id": up.json()["id"]}})

    def send_template(self, phone, name, params, payloads=None, code=None, lang="fr"):
        components: list[dict] = []
        if params:
            components.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in params]})
        if code:  # modèle d'authentification : le bouton « Copier le code » reprend le code
            components.append({"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": code}]})
        for i, payload in enumerate(payloads or []):
            components.append({"type": "button", "sub_type": "quick_reply", "index": str(i), "parameters": [{"type": "payload", "payload": payload}]})
        return self._post_message({"to": wa_id(phone), "type": "template", "template": {"name": name, "language": {"code": lang}, "components": components}})

    def download(self, media_id):
        try:
            meta = httpx.get(f"{self._base()}/{media_id}", headers=self._headers(), timeout=20)
            if meta.status_code >= 300:
                raise ProviderError(f"Fichier introuvable ({meta.status_code})")
            data = httpx.get(meta.json()["url"], headers=self._headers(), timeout=40)
        except httpx.HTTPError as e:
            raise ProviderError(f"Réseau : {type(e).__name__}") from e
        if data.status_code >= 300:
            raise ProviderError(f"Téléchargement refusé ({data.status_code})")
        return data.content


def get_provider() -> Provider:
    if settings.wa_meta:
        return MetaProvider()
    return SimProvider()
