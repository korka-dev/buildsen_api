"""Tâches planifiées appelées de l'extérieur (cron de l'hébergeur, GitHub Actions…)."""

import hmac

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session as Db

from ..config import settings
from ..db import get_db
from ..errors import ApiError
from ..services.alerts import process_due_alerts, remind_priority_pros
from ..services.pay.manual import expire_provisional
from ..services.pay.renewal import send_reminders
from ..services.wa import store as wa_store

router = APIRouter(prefix="/api", tags=["Tâches"])


@router.get("/cron/alertes")
def cron_alerts(request: Request, db: Db = Depends(get_db)):
    """Alertes « nouvelle demande » différées des formules gratuites. Sans le secret, limitée à une exécution toutes les 10 minutes."""
    auth = request.headers.get("authorization", "")
    forced = bool(settings.cron_secret) and hmac.compare_digest(auth, f"Bearer {settings.cron_secret}")
    out = {"ok": True, "sent": process_due_alerts(db, force=forced), "expired": expire_provisional(db)}
    if forced:  # rappels de fin de formule : seulement sur appel autorisé
        out["reminders"] = send_reminders(db)
        out["priority_reminders"] = remind_priority_pros(db)
    return out


@router.get("/cron/nettoyage")
def cron_cleanup(request: Request, db: Db = Depends(get_db)):
    """Purge du journal WhatsApp (90 jours). Réservée à l'appel planifié, avec le secret."""
    auth = request.headers.get("authorization", "")
    if not (settings.cron_secret and hmac.compare_digest(auth, f"Bearer {settings.cron_secret}")):
        raise ApiError("Non autorisé.", 401)
    n = wa_store.purge_old(db)
    db.commit()
    return {"ok": True, "deleted": n}


@router.get("/health")
def health(db: Db = Depends(get_db)):
    db.execute(text("select 1"))
    return {"ok": True}
