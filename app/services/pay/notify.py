"""Confirmation d'un paiement : message WhatsApp (si la fenêtre de 24 h est ouverte) et e-mail. Ne lève jamais d'erreur."""

import logging

from sqlalchemy.orm import Session as Db

from ...emails import plan_paid_mail
from ...mailer import app_url, mail_ctx, send_mail
from ...models import Payment, User
from ...rules import PLAN_CONTACTS, PLANS
from ...utils import fcfa, phone_local
from ..wa import store
from ..wa.i18n import tr
from ..wa.sender import send_text

log = logging.getLogger("buildsen.pay")


def paid(db: Db, pay: Payment) -> None:
    try:
        user = db.get(User, pay.user_id)
        name, until = PLANS[pay.plan]["name"], user.pro.plan_until
        n = PLAN_CONTACTS[pay.plan]
        send_mail(user.email, plan_paid_mail(mail_ctx(), user.first_name, name, fcfa(pay.amount), f"{until:%d/%m/%Y}", app_url("/pro/abonnement")))
        phone = phone_local(user.phone)
        if phone and not store.get_session(db, phone).opted_out and store.in_window(store.get_session(db, phone)):
            lang = (store.get_session(db, phone).data or {}).get("lang")
            contacts = tr(lang, "contacts_all") if n is None else tr(lang, "contacts_month", n=n)
            send_text(db, phone, tr(lang, "paid", amount=fcfa(pay.amount), plan=name, date=f"{until:%d/%m/%Y}", contacts=contacts))
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Paiement : confirmation non envoyée")
        db.rollback()
