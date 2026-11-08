"""Lecture des notifications que Meta envoie à notre adresse (webhook) : messages reçus et états d'envoi."""

import hashlib
import hmac

from ...config import settings
from ...utils import phone_local
from ...validators import clean
from .types import Inbound


def valid_signature(body: bytes, header: str | None) -> bool:
    """Meta signe chaque envoi avec le secret de l'application : en-tête X-Hub-Signature-256 = « sha256= » + HMAC-SHA256 du corps."""
    secret = settings.whatsapp_app_secret
    if not secret or not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def parse(payload: dict) -> tuple[list[Inbound], list[dict]]:
    """Renvoie (messages reçus, états d'envoi). Ce qui n'est pas reconnu est ignoré, jamais une erreur."""
    inbound: list[Inbound] = []
    statuses: list[dict] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                phone = phone_local(m.get("from"))
                if not phone or not m.get("id"):
                    continue
                kind, text, button, media = "other", "", "", ""
                match m.get("type"):
                    case "text":
                        kind, text = "text", (m.get("text") or {}).get("body", "")
                    case "audio":
                        kind, media = "audio", (m.get("audio") or {}).get("id", "")
                    case "interactive":
                        reply = (m.get("interactive") or {}).get("button_reply") or (m.get("interactive") or {}).get("list_reply") or {}
                        kind, button, text = "button", reply.get("id", ""), reply.get("title", "")
                    case "button":  # bouton de réponse rapide d'un message modèle
                        b = m.get("button") or {}
                        kind, button, text = "button", b.get("payload") or "", b.get("text", "")
                inbound.append(Inbound(
                    id=m["id"], phone=phone, kind=kind, text=clean(text), button_id=clean(button), media_id=media,
                    context_id=(m.get("context") or {}).get("id", ""), name=names.get(m.get("from"), ""),
                ))
            for st in value.get("statuses") or []:
                if st.get("id"):
                    statuses.append({"id": st["id"], "status": st.get("status", ""), "errors": st.get("errors") or []})
    return inbound, statuses
