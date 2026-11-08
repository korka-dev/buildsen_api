"""Rappel de fin de formule : e-mail (et message WhatsApp avec bouton « Renouveler » si la discussion est ouverte) à 7 jours, 3 jours et à l'échéance, une fois par étape."""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ...emails import plan_expiring_mail
from ...mailer import app_url, mail_ctx, send_mail
from ...models import ProProfile, Throttle, User
from ...rules import GRACE_DAYS, PLANS, PLANS_ACTIVE
from ...utils import now, phone_local
from ..wa import store
from ..wa.i18n import tr
from ..wa.sender import send_text

log = logging.getLogger("buildsen.pay")
STAGES = (("7", timedelta(days=7)), ("3", timedelta(days=3)))  # puis « end » : formule terminée, 2 jours de grâce


def _stage(p: ProProfile, t) -> str | None:
    """Le rappel qui s'applique à cette échéance (le plus urgent) ; None si rien à envoyer."""
    if p.plan_until <= t:
        return "end"
    for name, delta in reversed(STAGES):
        if p.plan_until <= t + delta:
            return name
    return None


def send_reminders(db: Db) -> int:
    """Prévient les professionnels à 7 jours, 3 jours et à l'échéance (formule terminée, 2 jours de grâce), une fois par étape. Inactif sans formules."""
    if not PLANS_ACTIVE:
        return 0
    t = now()
    rows = db.scalars(
        select(ProProfile).join(User).where(
            ProProfile.state == "VERIFIED", ProProfile.plan != "FREE", ProProfile.plan_until.is_not(None),
            ProProfile.plan_until > t - timedelta(days=GRACE_DAYS), ProProfile.plan_until <= t + timedelta(days=7), User.status == "ACTIVE",
        ).options(joinedload(ProProfile.user))
    ).all()
    sent = 0
    for p in rows:
        stage = _stage(p, t)
        if not stage:
            continue
        key = f"renew:{p.id}:{p.plan_until:%Y%m%d}:{stage}"
        if db.get(Throttle, key):  # déjà prévenu à cette étape
            continue
        db.add(Throttle(key=key, count=1, reset_at=t + timedelta(days=60)))
        db.commit()
        try:
            u, name, date = p.user, PLANS[p.plan]["name"], f"{p.plan_until:%d/%m/%Y}"
            link = app_url("/pro/abonnement")
            send_mail(u.email, plan_expiring_mail(mail_ctx(), u.first_name, name, date, link, stage))
            phone = phone_local(u.phone)
            if phone:
                s = store.get_session(db, phone)
                if not s.opted_out and store.in_window(s):
                    lang = (s.data or {}).get("lang")
                    key_txt = "renew_ended" if stage == "end" else "renew_soon"
                    send_text(db, phone, tr(lang, key_txt, plan=name, date=date, url=link), [(f"plan:{p.plan}", tr(lang, "btn_renew"))])
            db.commit()
            sent += 1
        except Exception:  # noqa: BLE001
            log.exception("Rappel de fin de formule non envoyé")
            db.rollback()
    return sent
