"""Mise en relation : références des demandes, noms affichés, e-mails de nouveaux messages."""

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from ..emails import new_message_mail
from ..mailer import app_url, mail_ctx, send_mail
from ..models import Conversation, ProProfile, ServiceRequest, User
from ..utils import now


def new_request_ref(db: Db) -> str:
    """Référence lisible d'une demande, par exemple BS-2026-04172."""
    while True:
        ref = f"BS-{now().year}-{10000 + secrets.randbelow(90000)}"
        if not db.scalar(select(ServiceRequest.id).where(ServiceRequest.ref == ref)):
            return ref


def request_title(r: ServiceRequest) -> str:
    return f"{r.service} · {r.building_type}"


def pro_name(u: User, p: ProProfile | None = None) -> str:
    """Nom du professionnel affiché au client : l'entreprise s'il y en a une."""
    return (p.company_name if p and p.company_name else None) or f"{u.first_name} {u.last_name}"


def client_name(u: User, shared: bool) -> str:
    """Prénom et initiale tant que le client n'a pas partagé son WhatsApp."""
    return f"{u.first_name} {u.last_name}" if shared else f"{u.first_name} {u.last_name[:1]}."


def wa_link(phone: str | None) -> str | None:
    d = "".join(c for c in (phone or "") if c.isdigit())
    if not d:
        return None
    return f"https://wa.me/{'221' + d if len(d) == 9 else d}"


def initials(first: str, last: str) -> str:
    return f"{first[:1]}{last[:1]}".upper()


def company_initials(name: str) -> str:
    import re
    import unicodedata

    plain = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode()
    words = [w for w in re.split(r"[^A-Za-z0-9]+", plain) if w]
    return "".join(w[0] for w in words[:2]).upper()


def badge(u: User, p: ProProfile | None) -> str:
    return company_initials(p.company_name) if p and p.company_name else initials(u.first_name, u.last_name)


def notify_message(db: Db, conv: Conversation, to_client: bool, first: bool) -> None:
    """E-mail au destinataire d'un nouveau message, au plus une fois tant qu'il n'a pas relu la conversation (12 h)."""
    # WhatsApp : chaque message est transmis (la conversation doit rester fluide) ; l'e-mail, lui, est espacé
    from .wa import notify as wa_notify  # import tardif : wa.notify utilise lui-même ce module

    wa_notify.chat_message(db, conv, to_client)
    mailed_at = conv.client_mailed_at if to_client else conv.pro_mailed_at
    read_at = conv.client_read_at if to_client else conv.pro_read_at
    if mailed_at and (not read_at or read_at < mailed_at) and now() - mailed_at < timedelta(hours=12):
        return
    client = conv.request.client
    to = client if to_client else conv.pro
    sender = pro_name(conv.pro, conv.pro.pro) if to_client else client_name(client, conv.shared)
    link = app_url(f"/espace/messages/{conv.id}" if to_client else f"/pro/messages/{conv.id}")
    send_mail(to.email, new_message_mail(mail_ctx(), to.first_name, sender, request_title(conv.request), link, first))
    if to_client:
        conv.client_mailed_at = now()
    else:
        conv.pro_mailed_at = now()
    db.commit()
