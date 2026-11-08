"""Envoi des e-mails par Resend (domaine buildsen.com vérifié).

Un échec est journalisé sans bloquer l'action en cours. Les réponses des destinataires arrivent sur la boîte commune.
Sans RESEND_API_KEY (développement), aucun e-mail n'est envoyé.
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from .config import settings
from .emails import Ctx, Mail
from .models import User
from .rules import TEAM_INBOX

log = logging.getLogger("buildsen.mail")
_API = "https://api.resend.com"
# Dernier envoi (mémoire du processus) : montré dans l'administration pour diagnostiquer sans lire les journaux
LAST: dict = {"ok": None, "error": None, "at": None}


def _record(ok: bool, error: str | None = None) -> None:
    LAST.update(ok=ok, error=None if ok else error, at=datetime.now(UTC).isoformat())


def key_problem() -> str | None:
    """Pourquoi aucun e-mail ne peut partir, ou None si la clé a l'air valide."""
    k = settings.resend_api_key
    if not k:
        return "RESEND_API_KEY est vide."
    if not k.startswith("re_") or "REMPLACER" in k.upper():
        return "RESEND_API_KEY n'est pas une vraie clé Resend (elle commence par « re_ »)."
    return None


def mail_ctx() -> Ctx:
    return Ctx()


def app_url(path: str = "") -> str:
    return f"{settings.app_url}{path}"


def _no_mailbox(to: str) -> bool:
    """Comptes créés sur WhatsApp : adresse factice en « .invalid », qui ne reçoit jamais d'e-mail."""
    return to.lower().endswith(".invalid")


def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    return send_email_detail(to, subject, text, html)[0]


def send_email_detail(to: str, subject: str, text: str, html: str | None = None) -> tuple[bool, str | None]:
    """Envoie un e-mail et dit pourquoi il n'est pas parti (jamais d'exception)."""
    if _no_mailbox(to):
        return False, "Adresse sans boîte e-mail."
    if not settings.resend_api_key:
        log.info("E-mail non envoyé (pas de clé Resend) : %s → %s", subject, to)
        _record(False, "RESEND_API_KEY est vide.")
        return False, "RESEND_API_KEY est vide."
    try:
        r = httpx.post(
            f"{_API}/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "reply_to": TEAM_INBOX, "to": to, "subject": subject, "text": text, "html": html},
            timeout=15,
        )
        if r.status_code >= 300:
            log.warning("E-mail non envoyé %s %s", r.status_code, r.text[:200])
            err = f"Resend a refusé l'envoi ({r.status_code}) : {r.text[:200]}"
            _record(False, err)
            return False, err
        _record(True)
        return True, None
    except httpx.HTTPError as e:
        log.warning("E-mail non envoyé : %s", e)
        _record(False, f"Resend injoignable : {e}")
        return False, f"Resend injoignable : {e}"


def send_mail(to: str, m: Mail) -> bool:
    return send_email(to, m.subject, m.text, m.html)


def send_mail_batch(items: list[tuple[str, Mail]]) -> None:
    """Envoi groupé (100 e-mails par appel) : évite la limite de débit quand plusieurs professionnels sont prévenus."""
    items = [(to, m) for to, m in items if not _no_mailbox(to)]
    if not settings.resend_api_key or not items:
        return
    for i in range(0, len(items), 100):
        payload = [
            {"from": settings.email_from, "reply_to": TEAM_INBOX, "to": to, "subject": m.subject, "text": m.text, "html": m.html}
            for to, m in items[i:i + 100]
        ]
        try:
            r = httpx.post(f"{_API}/emails/batch", headers={"Authorization": f"Bearer {settings.resend_api_key}"}, json=payload, timeout=30)
            if r.status_code >= 300:
                log.warning("E-mails groupés non envoyés %s %s", r.status_code, r.text[:200])
                _record(False, f"Resend a refusé l'envoi groupé ({r.status_code}) : {r.text[:200]}")
            else:
                _record(True)
        except httpx.HTTPError as e:
            log.warning("E-mails groupés non envoyés : %s", e)
            _record(False, f"Resend injoignable : {e}")


def notify_team(db: Db, subject: str, text: str, roles: tuple[str, ...] = ()) -> None:
    """Toujours vers la boîte commune, et vers les membres de l'équipe qui ont le rôle concerné."""
    send_email(TEAM_INBOX, f"[BUILDSEN] {subject}", text)
    if not roles:
        return
    team = db.scalars(select(User).where(User.role == "ADMIN", User.status == "ACTIVE", User.admin_role.in_(roles))).all()
    for a in team:
        if a.email != TEAM_INBOX:
            send_email(a.email, subject, text)
