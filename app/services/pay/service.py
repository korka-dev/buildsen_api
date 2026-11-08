"""Formules : limites de contacts, prix, état d'un abonnement, création des paiements et règlement par un fournisseur (PayDunya, simulateur).

Les paiements par lien marchand (Wave, Orange Money) se règlent dans `manual.py` ; l'import d'un export de transactions dans `exports.py`."""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from ...audit import audit
from ...config import settings
from ...errors import ApiError
from ...models import AppSetting, Conversation, Payment, ProProfile, User
from ...rules import PLAN_CONTACTS, PLAN_MONTHS, PLANS, PLANS_ACTIVE, YEAR_DISCOUNT, active_plan
from ...throttle import too_many
from ...utils import add_months, now
from .provider import get_provider

log = logging.getLogger("buildsen.pay")
ORDER = {"FREE": 0, "PRO": 1, "PREMIUM": 2}
EXPIRES_AFTER_H = 24


def month_start() -> datetime:
    t = now()
    return datetime(t.year, t.month, 1)


def contacts_used(db: Db, user_id: str) -> int:
    """Conversations ouvertes par ce professionnel depuis le début du mois."""
    return db.scalar(select(func.count()).select_from(Conversation).where(Conversation.pro_id == user_id, Conversation.created_at >= month_start())) or 0


def quota(db: Db, p: ProProfile) -> dict:
    """Formule active, contacts utilisés ce mois-ci et limite (None = illimité). Sans formules actives, aucune limite."""
    plan = active_plan(p.plan, p.plan_until)
    limit = PLAN_CONTACTS[plan] if PLANS_ACTIVE else None
    used = contacts_used(db, p.user_id)
    return {"plan": plan, "limit": limit, "used": used, "reached": limit is not None and used >= limit}


def limit_message(q: dict, for_bot: bool = False) -> str:
    base = f"Vous avez utilisé vos {q['limit']} contacts du mois (formule {PLANS[q['plan']]['name']})."
    where = "" if for_bot else " dans « Mon abonnement »"
    return base + f" Passez à une formule supérieure{where} pour continuer à écrire aux clients."


PRICES_KEY = "plan_prices"
MIN_PRICE, MAX_PRICE = 500, 1_000_000


def prices(db: Db) -> dict[str, int]:
    """Prix mensuels en vigueur : ceux de rules.py, sauf si l'équipe les a modifiés (réglage « plan_prices »)."""
    out = {k: v["price"] for k, v in PLANS.items()}
    row = db.get(AppSetting, PRICES_KEY)
    if row:
        try:
            for k, v in json.loads(row.value).items():
                if k in out and k != "FREE" and isinstance(v, int) and MIN_PRICE <= v <= MAX_PRICE:
                    out[k] = v
        except (ValueError, AttributeError):
            pass
    return out


def set_prices(db: Db, new: dict[str, int], admin_id: str) -> dict[str, int]:
    cur = prices(db)
    for k, v in new.items():
        if k not in cur or k == "FREE":
            raise ApiError("Formule inconnue.")
        if not isinstance(v, int) or isinstance(v, bool) or not MIN_PRICE <= v <= MAX_PRICE:
            raise ApiError(f"Prix : entre {MIN_PRICE} et {MAX_PRICE:,} FCFA.".replace(",", " "))
        cur[k] = v
    row = db.get(AppSetting, PRICES_KEY)
    payload = json.dumps({k: v for k, v in cur.items() if k != "FREE"})
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=PRICES_KEY, value=payload))
    audit(db, admin_id, "Tarifs modifiés", ", ".join(f"{k} {v}" for k, v in cur.items() if k != "FREE"))
    db.commit()
    return cur


def price(db: Db, plan: str, months: int) -> int:
    total = prices(db)[plan] * months
    return total * (100 - YEAR_DISCOUNT) // 100 if months == 12 else total


def status(db: Db, user: User) -> dict:
    p = user.pro
    q = quota(db, p)
    pending = db.scalar(select(Payment).where(Payment.user_id == user.id, Payment.state.in_(["PENDING", "PROVISIONAL"])).order_by(Payment.created_at.desc()).limit(1))
    cur = prices(db)
    options = [{"plan": k, "name": v["name"], "price": cur[k], "contacts": PLAN_CONTACTS[k]} for k, v in PLANS.items() if v["price"]]
    return {
        **q, "plansActive": PLANS_ACTIVE, "paymentsOpen": settings.pay_on, "sandbox": settings.pay_sim, "provider": settings.payment_provider, "methods": [{"id": m, "name": METHOD_NAMES[m]} for m in settings.pay_methods],
        "until": p.plan_until.isoformat() if p.plan_until else None, "pending": bool(pending), "options": options, "yearDiscount": YEAR_DISCOUNT,
    }


MANUAL = ("wave", "om")  # paiements par lien marchand : confirmés par avis reçu, export importé ou équipe
METHOD_NAMES = {"wave": "Wave", "om": "Orange Money"}


def create_checkout(db: Db, user: User, plan: str, months: int = 1, source: str = "web", method: str | None = None) -> Payment:
    """Crée un lien de paiement. La formule n'est activée qu'à la confirmation du paiement par le fournisseur."""
    if not PLANS_ACTIVE or not settings.pay_on:
        raise ApiError("Les formules payantes ne sont pas encore ouvertes.", 403)
    p = user.pro
    if not p or p.state != "VERIFIED":
        raise ApiError("Votre profil doit être vérifié avant de choisir une formule.", 403)
    if plan not in PLANS or not PLANS[plan]["price"]:
        raise ApiError("Choisissez une formule payante.")
    if months not in PLAN_MONTHS:
        raise ApiError("Durée : 1, 3 ou 12 mois.")
    if ORDER[plan] < ORDER[active_plan(p.plan, p.plan_until)]:
        raise ApiError("Vous avez déjà une formule supérieure.", 409)
    if too_many(db, f"pay:{user.id}", 10, 60):
        raise ApiError("Trop de tentatives. Réessayez dans un moment.", 429)
    amount = price(db, plan, months)
    methods = settings.pay_methods
    if methods:  # paiement par liens : le client choisit Wave ou Orange Money
        if method not in (None, *methods):
            raise ApiError("Moyen de paiement indisponible.")
        method = method or methods[0]
    prov = get_provider(method if methods else None)
    c = prov.create(amount, f"BUILDSEN · formule {PLANS[plan]['name']} · {months} mois")
    pay = Payment(user_id=user.id, plan=plan, months=months, amount=amount, provider=prov.name, token=c.token, checkout_url=c.url, source=source)
    db.add(pay)
    audit(db, user.id, "Paiement de formule démarré", f"{plan} {months} mois")
    db.commit()
    return pay


def _apply(p: ProProfile, pay: Payment) -> datetime:
    """Prolonge la formule en cours si c'est la même ; une formule supérieure démarre aujourd'hui."""
    start = now()
    if p.plan == pay.plan and p.plan_until and p.plan_until > start:
        start = p.plan_until
    until = add_months(start, pay.months)
    p.plan, p.plan_until = pay.plan, until
    return until


def settle(db: Db, token: str) -> tuple[Payment | None, bool]:
    """Interroge le fournisseur et règle le paiement. Renvoie (paiement, réglé à l'instant ?) : une confirmation en double ne fait rien."""
    pay = db.scalar(select(Payment).where(Payment.token == token).with_for_update())
    if not pay:
        return None, False
    if pay.state != "PENDING":
        db.rollback()
        return pay, False
    v = get_provider(pay.provider).verify(pay)
    if v.state == "paid":
        if v.amount is not None and v.amount != pay.amount:
            log.error("Paiement %s : montant reçu différent du montant attendu", pay.id)
            pay.state = "FAILED"
            pay.data = {**pay.data, "error": "montant"}
        else:
            user = db.get(User, pay.user_id)
            until = _apply(user.pro, pay)
            pay.state, pay.paid_at = "PAID", now()
            audit(db, user.id, "Formule payée", f"{pay.plan} jusqu'au {until:%d/%m/%Y}")
    elif v.state == "failed":
        pay.state = "FAILED"
    elif now() - pay.created_at > timedelta(hours=EXPIRES_AFTER_H):
        pay.state = "EXPIRED"
    db.commit()
    return pay, pay.state == "PAID"
