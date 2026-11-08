"""Paiement des formules.

- GET  /api/pro/plan                      formule, contacts utilisés ce mois-ci, offres
- POST /api/pro/checkout                  crée un lien de paiement (Wave / Orange Money)
- GET  /api/payments/{token}              état d'un paiement (page de retour) : le lien est secret
- POST /api/payments/webhook/paydunya     confirmation de PayDunya (jamais crue : le serveur interroge PayDunya)
- POST /api/payments/sim/{token}/{result} paiement simulé (développement uniquement)
- GET  /api/admin/payments                liste des paiements (équipe)
"""

import hmac
import json
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import require_admin, require_pro
from ..errors import ApiError, not_found
from ..models import Payment, User
from ..rules import PLANS
from ..services.pay import exports, manual, notify, service
from ..tasks import with_db
from ..throttle import client_ip, too_many
from ..utils import iso

router = APIRouter(prefix="/api", tags=["Paiements"])


@router.get("/pro/plan")
def my_plan(user: User = Depends(require_pro), db: Db = Depends(get_db)):
    return service.status(db, user)


@router.post("/pro/checkout")
def checkout(body: dict = Body(...), user: User = Depends(require_pro), db: Db = Depends(get_db)):
    raw = body.get("months")
    try:
        months = 1 if raw in (None, "") else int(raw)
    except (TypeError, ValueError):
        raise ApiError("Durée : 1, 3 ou 12 mois.") from None
    pay = service.create_checkout(db, user, str(body.get("plan") or ""), months, method=str(body.get("method") or "") or None)
    return {"url": pay.checkout_url, "ref": pay.token}


def _settle_and_notify(db: Db, token: str) -> None:
    pay, done = service.settle(db, token)
    if pay and done:
        notify.paid(db, pay)


@router.get("/payments/{token}")
def payment_state(token: str, background: BackgroundTasks, db: Db = Depends(get_db)):
    pay = db.scalar(select(Payment).where(Payment.token == token))
    if not pay:
        raise not_found("Paiement introuvable.")
    if pay.state == "PENDING" and pay.provider not in service.MANUAL:
        # La page de retour peut arriver avant la notification du fournisseur : on vérifie nous-mêmes
        pay, done = service.settle(db, token)
        if done:
            background.add_task(with_db, _notify_paid, pay.id)
    return {
        "state": pay.state, "plan": pay.plan, "planName": PLANS[pay.plan]["name"], "amount": pay.amount, "months": pay.months,
        "sandbox": pay.provider == "sim",
        "provider": pay.provider, "payUrl": pay.checkout_url if pay.provider in service.MANUAL else None,
        "until": (pay.data or {}).get("provisional_until") if pay.state == "PROVISIONAL" else None,
        "claimed": bool((pay.data or {}).get("claimed_at")),
    }


@router.post("/payments/{token}/claim")
def payment_claim(token: str, body: dict = Body(default={}), user: User = Depends(require_pro), db: Db = Depends(get_db)):
    """« J'ai payé » (Wave) : la formule s'active tout de suite, le temps que le paiement soit confirmé."""
    pay = manual.claim(db, user, token, str(body.get("phone") or "") or None, str(body.get("reference") or "") or None)
    return {"state": pay.state, "until": (pay.data or {}).get("provisional_until")}


@router.post("/payments/webhook/wave")
async def webhook_wave(request: Request, background: BackgroundTasks, db: Db = Depends(get_db)):
    """Avis de paiement Wave (transfert automatique ou outil de redirection des SMS). Protégé par un secret partagé."""
    return await _webhook(request, background, db, "wave")


@router.post("/payments/webhook/om")
async def webhook_om(request: Request, background: BackgroundTasks, db: Db = Depends(get_db)):
    """Avis de paiement Orange Money, même principe que Wave (secret : OM_WEBHOOK_SECRET, à défaut WAVE_WEBHOOK_SECRET)."""
    return await _webhook(request, background, db, "om")


async def _webhook(request: Request, background: BackgroundTasks, db: Db, method: str):
    if too_many(db, f"hook:{client_ip(request)}", 60, 1):  # un avis légitime arrive au compte-gouttes : on freine les essais de secret
        raise ApiError("Trop de requêtes.", 429)
    secret = (settings.om_webhook_secret or settings.wave_webhook_secret) if method == "om" else settings.wave_webhook_secret
    got = request.headers.get("x-webhook-secret", "")
    if method not in settings.pay_methods or not secret or not hmac.compare_digest(got.encode(), secret.encode()):
        raise not_found()
    try:
        body = json.loads((await request.body())[:20_000] or b"{}")
        amount = int(body.get("amount"))
    except (ValueError, TypeError, AttributeError):
        raise ApiError("Avis illisible.") from None
    pay = manual.confirm_external(db, amount, str(body.get("phone") or "") or None, str(body.get("reference") or "")[:80] or None, provider=method)
    if pay:
        background.add_task(with_db, _notify_paid, pay.id)
    return {"ok": True, "matched": bool(pay)}


def _notify_paid(db: Db, pay_id: str) -> None:
    pay = db.get(Payment, pay_id)
    if pay:
        notify.paid(db, pay)


@router.post("/payments/webhook/paydunya")
async def webhook_paydunya(request: Request, background: BackgroundTasks):
    """Notification de PayDunya : on n'en retient que la référence, puis on interroge PayDunya pour connaître le vrai état."""
    if not settings.pay_paydunya:
        raise not_found()
    raw = (await request.body())[:20_000].decode("utf-8", "ignore")
    token = (parse_qs(raw).get("data[invoice][token]") or [""])[0]
    if token:
        background.add_task(with_db, _settle_and_notify, token)
    return {"ok": True}


@router.post("/payments/sim/{token}/{result}")
def sim_pay(token: str, result: str, background: BackgroundTasks, db: Db = Depends(get_db)):
    """Page de paiement de test : « paid » ou « failed ». Absente en production."""
    if not settings.pay_sim:
        raise not_found()
    if result not in ("paid", "failed"):
        raise ApiError("Résultat inconnu.")
    pay = db.scalar(select(Payment).where(Payment.token == token, Payment.provider == "sim"))
    if not pay:
        raise not_found("Paiement introuvable.")
    if pay.state == "PENDING":
        pay.data = {**pay.data, "sim": result}
        db.commit()
        pay, done = service.settle(db, token)
        if done:
            background.add_task(with_db, _notify_paid, pay.id)
    return {"state": pay.state}


@router.post("/admin/payments/{pay_id}/{decision}")
def admin_decide(pay_id: str, decision: str, background: BackgroundTasks, admin: User = Depends(require_admin("SUPPORT")), db: Db = Depends(get_db)):
    if decision not in ("confirm", "reject"):
        raise not_found()
    pay = manual.admin_decide(db, pay_id, admin.id, decision == "confirm")
    if pay.state == "PAID":
        background.add_task(with_db, _notify_paid, pay.id)
    return {"state": pay.state}


@router.post("/admin/payments-import")
def admin_import(background: BackgroundTasks, body: dict = Body(...), admin: User = Depends(require_admin("SUPPORT")), db: Db = Depends(get_db)):
    """Export CSV des transactions Wave Business : confirme d'un coup tous les paiements en attente qui y figurent."""
    text = body.get("csv")
    if not isinstance(text, str) or not text.strip():
        raise ApiError("Fichier manquant.")
    if len(text) > 2_000_000:
        raise ApiError("Fichier trop volumineux (2 Mo maximum).")
    out = exports.import_export(db, text, str(body.get("provider") or "wave"))
    for c in out["confirmed"]:
        background.add_task(with_db, _notify_paid, c["id"])
    audit(db, admin.id, "Export Wave importé", f"{len(out['confirmed'])} paiement(s) confirmé(s)")
    db.commit()
    return out


@router.get("/admin/pricing")
def admin_pricing(admin: User = Depends(require_admin("SUPPORT")), db: Db = Depends(get_db)):
    return {"prices": {k: v for k, v in service.prices(db).items() if k != "FREE"}}


@router.post("/admin/pricing")
def admin_set_pricing(body: dict = Body(...), admin: User = Depends(require_admin("SUPER")), db: Db = Depends(get_db)):
    raw = body.get("prices")
    if not isinstance(raw, dict) or not raw:
        raise ApiError("Prix manquants.")
    cur = service.set_prices(db, raw, admin.id)
    return {"prices": {k: v for k, v in cur.items() if k != "FREE"}}


@router.get("/admin/payments")
def admin_payments(admin: User = Depends(require_admin("SUPPORT")), db: Db = Depends(get_db)):
    rows = db.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(100)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r.user_id for r in rows]))).all()} if rows else {}
    return {"payments": [
        {"id": r.id, "who": users[r.user_id].full_name if r.user_id in users else "?", "plan": r.plan, "months": r.months, "amount": r.amount,
         "provider": r.provider, "state": r.state, "claimed": bool((r.data or {}).get("claimed_at")), "payerPhone": (r.data or {}).get("payer_phone"), "source": r.source, "createdAt": iso(r.created_at), "paidAt": iso(r.paid_at)}
        for r in rows
    ]}
