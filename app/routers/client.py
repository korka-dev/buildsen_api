"""Espace du particulier : ses demandes, sa messagerie et son compte."""

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..audit import audit
from ..db import get_db
from ..deps import require_client
from ..emails import verify_email_mail
from ..errors import ApiError, not_found
from ..mailer import app_url, mail_ctx, send_mail
from ..models import Conversation, ServiceRequest, User
from ..security import check_password, issue_user_token
from ..serializers import conversation_summary, request_out
from ..services.actions import after_publish, close_request, create_service_request, notify_shared, share_whatsapp
from ..services.queries import conversation_counts, last_messages, users_with_phone
from ..tasks import with_db
from ..throttle import too_many
from ..validators import phone

router = APIRouter(prefix="/api/client", tags=["Espace particulier"])


@router.get("/requests")
def my_requests(user: User = Depends(require_client), db: Db = Depends(get_db)):
    reqs = db.scalars(select(ServiceRequest).where(ServiceRequest.client_id == user.id).order_by(ServiceRequest.created_at.desc())).all()
    counts = conversation_counts(db, [r.id for r in reqs])
    return {"requests": [request_out(r, conversations=counts.get(r.id, 0)) for r in reqs]}


@router.post("/requests")
def create_request(background: BackgroundTasks, body: dict = Body(...), user: User = Depends(require_client), db: Db = Depends(get_db)):
    r = create_service_request(db, user, body)
    background.add_task(with_db, after_publish, r.id)
    return {"redirect": f"/espace/demandes/{r.ref}?envoyee=1", "ref": r.ref}


@router.get("/requests/{ref}")
def my_request(ref: str, user: User = Depends(require_client), db: Db = Depends(get_db)):
    r = db.scalar(select(ServiceRequest).where(ServiceRequest.ref == ref))
    if not r or r.client_id != user.id:
        raise not_found("Demande introuvable.")
    convs = db.scalars(
        select(Conversation).where(Conversation.request_id == r.id).order_by(Conversation.last_message_at.desc())
        .options(joinedload(Conversation.pro).joinedload(User.pro), joinedload(Conversation.request))
    ).all()
    last = last_messages(db, [c.id for c in convs])
    return {"request": request_out(r, conversations=len(convs)), "conversations": [conversation_summary(c, "client", last.get(c.id)) for c in convs]}


@router.post("/requests/{ref}/close")
def close_request_route(ref: str, user: User = Depends(require_client), db: Db = Depends(get_db)):
    close_request(db, user, ref)
    return {"ok": True}


@router.get("/conversations")
def my_conversations(user: User = Depends(require_client), db: Db = Depends(get_db)):
    convs = db.scalars(
        select(Conversation).join(ServiceRequest).where(ServiceRequest.client_id == user.id)
        .order_by(Conversation.last_message_at.desc())
        .options(joinedload(Conversation.request), joinedload(Conversation.pro).joinedload(User.pro))
    ).all()
    last = last_messages(db, [c.id for c in convs])
    return {"conversations": [conversation_summary(c, "client", last.get(c.id)) for c in convs]}


@router.post("/conversations/{conversation_id}/share")
def share_whatsapp_route(conversation_id: str, background: BackgroundTasks, user: User = Depends(require_client), db: Db = Depends(get_db)):
    """Le client partage son WhatsApp avec le professionnel de cette conversation."""
    c, new = share_whatsapp(db, user, conversation_id)
    if new:
        background.add_task(with_db, notify_shared, c.id)
    return {"ok": True}


@router.post("/resend-verification")
def resend_verification(background: BackgroundTasks, user: User = Depends(require_client), db: Db = Depends(get_db)):
    if user.email_verified_at:
        return {"ok": "Votre adresse est déjà confirmée."}
    if too_many(db, f"verify:{user.id}", 3, 60):
        raise ApiError("Trop de demandes. Réessayez dans une heure.", 429)
    token = issue_user_token(db, user, "verify", 48)
    background.add_task(send_mail, user.email, verify_email_mail(mail_ctx(), user.first_name, app_url(f"/verifier-email?jeton={token}")))
    return {"ok": f"Un nouveau lien vient d'être envoyé à {user.email}."}


@router.put("/phone")
def update_phone(body: dict = Body(...), user: User = Depends(require_client), db: Db = Depends(get_db)):
    ph = phone(body)
    # Le numéro sert à se connecter : changer de numéro demande le mot de passe (une session volée ne suffit pas à prendre le compte)
    if user.password_hash and not check_password(str(body.get("password") or ""), user.password_hash):
        raise ApiError("Mot de passe incorrect.", 403)
    # Le numéro ne doit pas appartenir à un autre compte
    if users_with_phone(db, ph, exclude_id=user.id):
        raise ApiError("Ce numéro est déjà utilisé par un autre compte.", 409)
    user.phone = ph
    audit(db, user.id, "Téléphone modifié")
    db.commit()
    return {"ok": "Numéro enregistré."}

