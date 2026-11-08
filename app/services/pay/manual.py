"""Paiements par lien marchand (Wave, Orange Money) : « J'ai payé », confirmation par avis reçu ou par l'équipe, activation provisoire facultative."""

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from ...audit import audit
from ...config import settings
from ...errors import ApiError
from ...models import Payment, User
from ...throttle import too_many
from ...utils import now, phone_local
from .service import MANUAL, _apply

log = logging.getLogger("buildsen.pay")

PROVISIONAL_DAYS = 30  # une activation provisoire par professionnel et par période de 30 jours


def finalize(db: Db, pay: Payment, how: str) -> datetime:
    """Règle le paiement : la formule est active pour toute la durée achetée (en repartant de la situation d'avant s'il y a eu activation provisoire)."""
    user = db.get(User, pay.user_id)
    p = user.pro
    _revert(db, pay)
    until = _apply(p, pay)
    pay.state, pay.paid_at = "PAID", now()
    pay.data = {**(pay.data or {}), "confirmed_by": how}
    audit(db, user.id, "Formule payée", f"{pay.plan} jusqu'au {until:%d/%m/%Y} ({how})")
    return until


def _locked(db: Db, token: str) -> Payment | None:
    return db.scalar(select(Payment).where(Payment.token == token).with_for_update())


def claim(db: Db, user: User, token: str, payer_phone: str | None = None, tx_ref: str | None = None) -> Payment:
    """« J'ai payé » : signale le paiement et note le numéro Wave utilisé. Rien n'est activé sans la réception du vrai paiement (sauf WAVE_GRACE_HOURS > 0)."""
    pay = _locked(db, token)
    if not pay or pay.user_id != user.id:
        db.rollback()
        raise ApiError("Paiement introuvable.", 404)
    if pay.provider not in MANUAL:
        db.rollback()
        raise ApiError("Ce paiement se confirme automatiquement.", 400)
    if pay.state in ("PAID", "PROVISIONAL"):
        db.rollback()
        return pay
    if pay.state != "PENDING":
        db.rollback()
        raise ApiError("Ce paiement n'est plus en attente. Recommencez depuis « Abonnement ».", 409)
    if too_many(db, f"claim:{user.id}", 10, 60):
        raise ApiError("Trop de tentatives. Réessayez dans un moment.", 429)
    pay = _locked(db, token)
    p = user.pro
    payer = None
    if payer_phone:
        payer = phone_local(payer_phone)
        if not payer:
            db.rollback()
            raise ApiError("Numéro Wave : 9 chiffres, par exemple 77 123 45 67.")
    ref = clean_ref(tx_ref)
    if tx_ref and not ref:
        db.rollback()
        raise ApiError("Identifiant de transaction : lettres et chiffres, par exemple T_ABC123XYZ.")
    pay.data = {**(pay.data or {}), "claimed_at": now().isoformat(), **({"payer_phone": payer} if payer else {}), **({"claimed_ref": ref} if ref else {})}
    recent = db.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.user_id == user.id, Payment.id != pay.id, Payment.created_at > now() - timedelta(days=PROVISIONAL_DAYS),
            Payment.data["provisional_at"].astext.is_not(None),
        )
    )
    grace = settings.wave_grace_hours
    if grace <= 0 or recent:
        # Pas d'activation avant la confirmation : l'avis de paiement ou l'équipe confirmera
        db.commit()
        return pay
    t = now()
    pay.data = {**pay.data, "prev": {"plan": p.plan, "until": p.plan_until.isoformat() if p.plan_until else None}, "provisional_at": t.isoformat(),
                "provisional_until": (t + timedelta(hours=grace)).isoformat()}
    pay.state = "PROVISIONAL"
    keep = p.plan == pay.plan and p.plan_until and p.plan_until > t + timedelta(hours=grace)
    if not keep:
        p.plan, p.plan_until = pay.plan, t + timedelta(hours=grace)
    audit(db, user.id, "Formule activée provisoirement (paiement Wave à confirmer)", f"{pay.plan} {grace} h")
    db.commit()
    return pay


def clean_ref(raw: str | None) -> str | None:
    """Identifiant de transaction Wave saisi par le professionnel : 4 à 40 caractères (lettres, chiffres, _ et -), sinon None."""
    v = (raw or "").strip()
    return v if re.fullmatch(r"[A-Za-z0-9_-]{4,40}", v) else None


def confirm_external(db: Db, amount: int, phone: str | None = None, reference: str | None = None, window_h: int = 72, provider: str | None = None) -> Payment | None:
    """Avis de paiement Wave reçu (montant, numéro de l'expéditeur, référence) : règle le paiement en attente qui correspond, sans intervention.

    Prudence : un seul paiement doit correspondre (montant et, si connu, numéro) ; sinon rien n'est réglé et l'équipe tranche. Une même référence ne compte qu'une fois.
    """
    if reference and db.scalar(select(Payment.id).where(Payment.data["wave_ref"].astext == reference)):
        return None
    q = (
        select(Payment).where(
            Payment.provider == provider if provider else Payment.provider.in_(MANUAL), Payment.amount == amount, Payment.state.in_(["PENDING", "PROVISIONAL", "EXPIRED"]),
            Payment.created_at > now() - timedelta(hours=window_h),
        ).order_by(Payment.created_at.desc())
    )
    cands = list(db.scalars(q).all())
    # 1) L'identifiant de transaction donné par le professionnel est la preuve la plus sûre (il n'existe que sur un vrai paiement)
    by_ref = [c for c in cands if reference and ((c.data or {}).get("claimed_ref") or "").lower() == reference.lower()]
    want = phone_local(phone)
    if by_ref:
        cands = by_ref
    elif want:  # 2) sinon montant + numéro de l'expéditeur
        cands = [c for c in cands if want in {phone_local(db.get(User, c.user_id).phone), (c.data or {}).get("payer_phone")}]
    else:  # sans numéro ni identifiant connu, on ne sait pas qui a payé : l'équipe tranche
        log.warning("Avis Wave de %s FCFA sans numéro d'expéditeur ni identifiant connu : à traiter par l'équipe", amount)
        return None
    if len(cands) != 1:
        log.warning("Avis Wave de %s FCFA : %d paiement(s) correspondant(s), à traiter par l'équipe", amount, len(cands))
        return None
    pay = _locked(db, cands[0].token)
    if pay.state not in ("PENDING", "PROVISIONAL", "EXPIRED"):
        db.rollback()
        return None
    if pay.state == "EXPIRED":  # activation provisoire déjà retirée : on repart de la situation actuelle
        pay.state = "PENDING"
    pay.data = {**(pay.data or {}), "wave_ref": reference or ""}
    finalize(db, pay, "avis de paiement Wave")
    db.commit()
    return pay


def admin_decide(db: Db, pay_id: str, admin_id: str, accept: bool) -> Payment:
    """L'équipe confirme ou refuse un paiement qui n'a pas pu l'être automatiquement."""
    pay = db.scalar(select(Payment).where(Payment.id == pay_id).with_for_update())
    if not pay:
        db.rollback()
        raise ApiError("Paiement introuvable.", 404)
    if pay.state not in ("PENDING", "PROVISIONAL", "EXPIRED"):
        db.rollback()
        raise ApiError("Ce paiement est déjà réglé ou refusé.", 409)
    if accept:
        if pay.state == "EXPIRED":
            pay.state = "PENDING"
        finalize(db, pay, "équipe")
        audit(db, admin_id, "Paiement confirmé", pay.id)
    else:
        _revert(db, pay)
        pay.state = "FAILED"
        audit(db, admin_id, "Paiement refusé", pay.id)
    db.commit()
    return pay


def _revert(db: Db, pay: Payment) -> None:
    """Retire l'activation provisoire si elle est encore en place (une formule achetée entre-temps n'est pas touchée)."""
    if pay.state != "PROVISIONAL":
        return
    until = datetime.fromisoformat(pay.data["provisional_until"])
    p = db.get(User, pay.user_id).pro
    prev = pay.data.get("prev") or {}
    if p.plan == pay.plan and p.plan_until and p.plan_until <= until + timedelta(seconds=1):  # les colonnes gardent le millième de seconde
        p.plan = prev.get("plan", "FREE")
        p.plan_until = datetime.fromisoformat(prev["until"]) if prev.get("until") else None


def expire_provisional(db: Db) -> int:
    """Activations provisoires dont le délai est passé sans confirmation : la formule est retirée (elle expirait déjà d'elle-même)."""
    due = db.scalars(select(Payment).where(Payment.state == "PROVISIONAL")).all()
    n = 0
    for pay in due:
        if datetime.fromisoformat(pay.data["provisional_until"]) > now():
            continue
        pay = _locked(db, pay.token)
        if pay.state != "PROVISIONAL":
            db.rollback()
            continue
        _revert(db, pay)
        pay.state = "EXPIRED"
        audit(db, pay.user_id, "Activation provisoire expirée (paiement non confirmé)", pay.id)
        db.commit()
        n += 1
    return n
