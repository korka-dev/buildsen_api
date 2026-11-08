"""Envoi de messages WhatsApp : journal, fenêtre de 24 heures, interrupteur d'urgence.

Règle de Meta : on peut écrire librement à quelqu'un qui nous a écrit il y a moins de 24 heures. Au-delà, seul un message
modèle approuvé est accepté. `send_or_template` choisit le bon mode. Un échec est journalisé sans bloquer l'action en cours.
"""

import base64
import logging

from sqlalchemy.orm import Session as Db

from ...config import settings
from ...models import WaMessage, WaSession
from ...utils import cuid
from . import store
from .provider import MAX_TEXT, ProviderError, get_provider
from .templates import clean_param, render, template_buttons, template_lang, template_name

log = logging.getLogger("buildsen.wa")
Buttons = list[tuple[str, str]]  # (identifiant, titre de 20 caractères maximum)


def _log(db: Db, msg_id: str, phone: str, kind: str, body: str, status: str = "sent", payload: dict | None = None) -> None:
    db.merge(WaMessage(id=msg_id, phone=phone, direction="OUT", kind=kind, body=body[:MAX_TEXT], status=status, payload=payload))
    db.flush()


def _blocked(db: Db, force: bool = False) -> bool:
    """force : réponse directe à quelqu'un qui vient d'écrire (message « assistant indisponible »), permise même interrupteur coupé."""
    return not settings.wa_on or (not force and not store.enabled(db))


def send_text(db: Db, phone: str, body: str, buttons: Buttons | None = None, *, conv_id: str | None = None, force: bool = False) -> bool:
    """Texte libre (dans la fenêtre de 24 heures), avec jusqu'à 3 boutons de réponse."""
    if _blocked(db, force):
        return False
    payload = {"buttons": [{"id": i, "title": t[:20]} for i, t in (buttons or [])[:3]], **({"conv": conv_id} if conv_id else {})}
    try:
        mid = get_provider().send_text(phone, body, buttons)
    except ProviderError as e:
        log.warning("WhatsApp : envoi refusé (%s) code=%s", e, e.code)
        _log(db, "fail_" + cuid(), phone, "buttons" if buttons else "text", body, "failed", {**payload, "error": str(e)[:200], "code": e.code})
        return False
    _log(db, mid, phone, "buttons" if buttons else "text", body, payload=payload)
    return True


def send_voice(db: Db, phone: str, mp3: bytes, label: str = "Note vocale") -> bool:
    """Note vocale (MP3) : la question de l'assistant lue en wolof."""
    if _blocked(db):
        return False
    try:
        mid = get_provider().send_audio(phone, mp3)
    except ProviderError as e:
        log.warning("WhatsApp : audio refusé (%s) code=%s", e, e.code)
        _log(db, "fail_" + cuid(), phone, "audio", label, "failed", {"error": str(e)[:200], "code": e.code})
        return False
    # Le simulateur garde le son pour pouvoir le rejouer ; chez Meta, le journal ne garde pas l'audio
    _log(db, mid, phone, "audio", label, payload={"audio": base64.b64encode(mp3).decode()} if settings.wa_sim else None)
    return True


def send_or_template(
    db: Db, phone: str, body: str, buttons: Buttons | None, tpl: str, params: list[str], *, conv_id: str | None = None, code: str | None = None,
) -> bool:
    """Message libre si la personne nous a écrit il y a moins de 24 h, sinon message modèle (alertes, codes, nouveaux messages)."""
    if _blocked(db):
        return False
    session = db.get(WaSession, phone)
    if store.in_window(session):
        return send_text(db, phone, body, buttons, conv_id=conv_id)
    lang = (session.data or {}).get("lang") if session else None
    params = [clean_param(p) for p in params]
    # Un code de connexion n'est jamais gardé dans le journal, sauf avec le simulateur (pour pouvoir le lire en test)
    shown = ["••••••" if code and not settings.wa_sim else p for p in params]
    payloads = [i for i, _ in (buttons or [])][: len(template_buttons(tpl, lang))]
    payload = {
        "template": template_name(tpl),
        "params": shown,
        "buttons": [{"id": i, "title": t} for i, t in zip(payloads, template_buttons(tpl, lang), strict=False)],
        **({"conv": conv_id} if conv_id else {}),
    }
    try:
        mid = get_provider().send_template(phone, template_name(tpl), params, payloads, code=code, lang=template_lang(lang))
    except ProviderError as e:
        log.warning("WhatsApp : modèle refusé (%s) code=%s", e, e.code)
        _log(db, "fail_" + cuid(), phone, "template", render(tpl, shown, lang), "failed", {**payload, "error": str(e)[:200], "code": e.code})
        return False
    _log(db, mid, phone, "template", render(tpl, shown, lang), payload=payload)
    return True
