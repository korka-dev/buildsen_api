"""Messagerie commune au particulier et au professionnel, et signalements."""

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..audit import audit
from ..db import get_db
from ..deps import require_client, require_member, require_pro
from ..errors import ApiError, not_found
from ..emails import review_received_mail
from ..mailer import app_url, mail_ctx, notify_team, send_mail
from ..models import Conversation, ProProfile, Report, ServiceRequest, User
from ..serializers import conversation_full
from ..services import reviews
from ..services.actions import notify_chat, post_chat_message
from ..tasks import with_db
from ..throttle import too_many
from ..utils import now
from ..validators import report_reason, text

router = APIRouter(prefix="/api", tags=["Messagerie"])


def _load(db: Db, conversation_id: str) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).options(
            joinedload(Conversation.request).joinedload(ServiceRequest.client),
            joinedload(Conversation.pro).joinedload(User.pro).selectinload(ProProfile.portfolio),
        )
    )


def _mine(db: Db, conversation_id: str, user: User) -> tuple[Conversation, bool]:
    """Conversation accessible : le client de la demande ou le professionnel de la conversation."""
    c = _load(db, conversation_id)
    if c:
        if user.role == "CLIENT" and c.request.client_id == user.id:
            return c, True
        if user.role == "PRO" and c.pro_id == user.id:
            return c, False
    raise not_found("Conversation introuvable.")


@router.get("/conversations/{conversation_id}")
def open_conversation(conversation_id: str, user: User = Depends(require_member), db: Db = Depends(get_db)):
    c, is_client = _mine(db, conversation_id, user)
    # Ouverture : la conversation est lue
    if is_client:
        c.client_read_at = now()
    else:
        c.pro_read_at = now()
    db.commit()
    out = conversation_full(c, "client" if is_client else "pro")
    if is_client:
        out["other"]["rating"] = reviews.ratings(db, [c.pro_id]).get(c.pro_id, {"avg": None, "count": 0})
        out["review"] = reviews.my_review(db, c.id)
        out["canReview"] = reviews.can_review(db, c)
    return {"conversation": out, "meId": user.id}


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, background: BackgroundTasks, body: dict = Body(...), user: User = Depends(require_member), db: Db = Depends(get_db)):
    c, is_client = _mine(db, conversation_id, user)
    m = post_chat_message(db, c, user, is_client, text(body, "body"))
    background.add_task(with_db, notify_chat, c.id, not is_client)
    return {"ok": True, "message": {"id": m.id, "senderId": m.sender_id, "body": m.body, "createdAt": m.created_at.isoformat() + "Z"}}


@router.post("/conversations/{conversation_id}/review")
def review_conversation(conversation_id: str, background: BackgroundTasks, body: dict = Body(...), user: User = Depends(require_client), db: Db = Depends(get_db)):
    """Avis du particulier sur le professionnel (une fois, après sa réponse)."""
    c, _ = _mine(db, conversation_id, user)
    r = reviews.create_review(db, user, c, body.get("rating"), text(body, "comment"))
    background.add_task(send_mail, c.pro.email, review_received_mail(mail_ctx(), c.pro.first_name, r.rating, app_url(f"/pro/messages/{c.id}")))
    return {"ok": True, "review": {"rating": r.rating, "comment": r.comment}}


@router.post("/conversations/{conversation_id}/report")
def report_conversation(
    conversation_id: str, background: BackgroundTasks, body: dict = Body(...), user: User = Depends(require_member), db: Db = Depends(get_db)
):
    c, _ = _mine(db, conversation_id, user)
    reason = report_reason(body)
    if too_many(db, f"report:{user.id}", 5, 24 * 60):
        raise ApiError("Trop de signalements aujourd'hui. Contactez-nous sur WhatsApp.", 429)
    db.add(Report(reporter_id=user.id, conversation_id=c.id, reason=reason))
    audit(db, user.id, "Conversation signalée", c.id)
    db.commit()
    background.add_task(with_db, notify_team, "Nouveau signalement d'une conversation", f"{reason}\n\n{app_url('/admin/signalements')}", ("MODER", "SUPPORT"))
    return {"ok": "Merci. Notre équipe va examiner ce signalement."}


@router.post("/requests/{request_id}/report")
def report_request(request_id: str, background: BackgroundTasks, body: dict = Body(...), user: User = Depends(require_pro), db: Db = Depends(get_db)):
    """Signalement d'une demande par un professionnel (contenu contraire aux règles, fausse demande…)."""
    r = db.get(ServiceRequest, request_id)
    if not r or r.state != "PUBLISHED":
        raise ApiError("Cette demande n'est plus en ligne.", 404)
    reason = report_reason(body)
    if too_many(db, f"report:{user.id}", 5, 24 * 60):
        raise ApiError("Trop de signalements aujourd'hui. Contactez-nous sur WhatsApp.", 429)
    if db.scalar(select(Report.id).where(Report.request_id == request_id, Report.reporter_id == user.id, Report.state == "OPEN")):
        return {"ok": "Vous avez déjà signalé cette demande. Notre équipe l'examine."}
    db.add(Report(reporter_id=user.id, request_id=request_id, reason=reason))
    audit(db, user.id, "Demande signalée", r.ref)
    db.commit()
    background.add_task(with_db, notify_team, f"Demande signalée : {r.ref}", f"{reason}\n\n{app_url('/admin/signalements')}", ("MODER", "SUPPORT"))
    return {"ok": "Merci. Notre équipe va examiner cette demande."}
