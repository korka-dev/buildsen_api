"""Utilisateur connecté : identité, profil professionnel, prochaine étape du dossier et notifications."""

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session as Db

from ..db import get_db
from ..deps import current_session
from ..models import Session
from ..rules import CLIENTS_OPEN, dossier_next
from ..serializers import pro_private_out, user_out
from ..services.alerts import process_due_alerts
from ..services.notifications import client_notifs, pro_notifs
from ..tasks import with_db

router = APIRouter(prefix="/api", tags=["Session"])


@router.get("/me")
def me(background: BackgroundTasks, notifs: bool = False, s: Session | None = Depends(current_session), db: Db = Depends(get_db)):
    """{"user": null} pour un visiteur. Avec ?notifs=1, ajoute le centre de notifications (espaces particulier et pro)."""
    if not s:
        return {"user": None}
    u = s.user
    out: dict = {"user": user_out(u), "mfa": s.mfa, "pro": None, "next": None, "notifs": None}
    if u.role == "PRO" and u.pro:
        out["pro"] = pro_private_out(u.pro)
        out["next"] = dossier_next(u.pro)
    if notifs and CLIENTS_OPEN:
        if u.role == "CLIENT":
            out["notifs"] = client_notifs(db, u)
        elif u.role == "PRO" and u.pro:
            out["notifs"] = pro_notifs(db, u, u.pro)
        # Alertes différées des formules gratuites, vérifiées au passage (au plus toutes les 10 minutes)
        background.add_task(with_db, process_due_alerts)
    return out
