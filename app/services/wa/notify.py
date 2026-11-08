"""Notifications WhatsApp envoyées par le reste de la plateforme : nouvelle demande, nouveau message, numéro partagé, code de connexion.

Chaque fonction ne lève jamais d'erreur : un échec WhatsApp ne doit pas faire échouer la publication d'une demande
ni l'envoi d'un message. Les personnes qui ont écrit STOP ne reçoivent plus d'alertes.
"""

import logging

from sqlalchemy.orm import Session as Db

from ...config import settings
from ...models import Conversation, ProProfile, ServiceRequest, User, WaSession
from ...rules import CATEGORIES
from ...utils import fcfa, format_phone, phone_local
from ..market import client_name, pro_name, request_title
from . import store
from .i18n import tr, value_label
from .sender import send_or_template, send_text
from .templates import clean_param

log = logging.getLogger("buildsen.wa")


def _target(db: Db, u: User):
    """(numéro, session) si cette personne peut être prévenue sur WhatsApp, sinon (None, None)."""
    if not settings.wa_on or u.status != "ACTIVE":
        return None, None
    phone = phone_local(u.phone)
    if not phone:
        return None, None
    s = store.get_session(db, phone)
    if s.opted_out:
        return None, None
    return phone, s


def service_label(lang: str | None, r: ServiceRequest) -> str:
    """Prestation de la demande dans la langue de la personne (les prestations détaillées du site restent en français)."""
    cat = next((k for k, v in CATEGORIES.items() if v["name"] == r.service), None)
    return value_label(lang, "category", cat) if cat else r.service


def request_alert(db: Db, r: ServiceRequest, pros: list[ProProfile]) -> int:
    """Alerte « nouvelle demande » aux professionnels concernés, avec les boutons Voir / Répondre / Ignorer."""
    sent = 0
    for p in pros:
        try:
            phone, s = _target(db, p.user)
            if not phone:
                continue
            lang = (s.data or {}).get("lang")
            service = service_label(lang, r)
            body = tr(lang, "alert_body", commune=r.commune, service=service, budget=fcfa(r.budget))
            buttons = [(f"req:{r.id}:view", tr(lang, "btn_view")), (f"req:{r.id}:reply", tr(lang, "btn_reply")), (f"req:{r.id}:skip", tr(lang, "btn_skip"))]
            if send_or_template(db, phone, body, buttons, "alert", [r.commune, service, fcfa(r.budget)]):
                sent += 1
        except Exception:  # noqa: BLE001
            log.exception("WhatsApp : alerte non envoyée")
            db.rollback()
    db.commit()
    return sent


def chat_message(db: Db, conv: Conversation, to_client: bool) -> None:
    """Le dernier message de la conversation est transmis à l'autre personne sur WhatsApp, avec le bouton Répondre."""
    try:
        client = conv.request.client
        to = client if to_client else conv.pro
        phone, s = _target(db, to)
        if not phone:
            return
        last = conv.messages[-1] if conv.messages else None
        if not last:
            return
        sender = pro_name(conv.pro, conv.pro.pro) if to_client else client_name(client, conv.shared)
        body = f"💬 *{sender}* · {request_title(conv.request)}\n\n{last.body}"
        lang = (s.data or {}).get("lang")
        buttons = [(f"conv:{conv.id}:reply", tr(lang, "btn_reply"))]
        if to_client and not conv.shared:
            buttons.append((f"conv:{conv.id}:share", tr(lang, "btn_share")))
        # La prochaine réponse libre de cette personne sera transmise à cette conversation
        store.save_data(s, conv=conv.id)
        send_or_template(db, phone, body, buttons, "message", [sender, conv.request.ref, last.body], conv_id=conv.id)
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("WhatsApp : message non transmis")
        db.rollback()


def number_shared(db: Db, conv: Conversation) -> None:
    """Le client a partagé son numéro : le professionnel le reçoit."""
    try:
        phone, s = _target(db, conv.pro)
        client = conv.request.client
        if not phone:
            return
        lang = (s.data or {}).get("lang")
        body = tr(lang, "shared_number", who=client_name(client, True), phone=format_phone(client.phone), link=f"https://wa.me/221{phone_local(client.phone)}")
        send_text(db, phone, body) if store.in_window(store.get_session(db, phone)) else send_or_template(
            db,
            phone,
            body,
            None,
            "message",
            [clean_param(client_name(client, True)), conv.request.ref, tr(lang, "shared_tpl", phone=format_phone(client.phone))],
            conv_id=conv.id,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("WhatsApp : numéro partagé non transmis")
        db.rollback()


def login_code(db: Db, phone: str, code: str) -> bool:
    """Code de connexion au site, envoyé sur WhatsApp (message modèle d'authentification hors fenêtre de 24 h)."""
    if not settings.wa_on:
        return False
    try:
        lang = (db.get(WaSession, phone).data or {}).get("lang") if db.get(WaSession, phone) else None
        ok = send_or_template(db, phone, tr(lang, "login_code", code=code), None, "code", [code], code=code)
        db.commit()
        return ok
    except Exception:  # noqa: BLE001
        log.exception("WhatsApp : code non envoyé")
        db.rollback()
        return False
