"""Avis des clients sur les professionnels : un seul par conversation, seulement si le professionnel a répondu."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..audit import audit
from ..errors import ApiError
from ..models import Conversation, Message, Review, User
from ..throttle import too_many
from ..utils import iso

MAX_COMMENT = 600


def ratings(db: Db, pro_user_ids: list[str]) -> dict[str, dict]:
    """Note moyenne et nombre d'avis visibles, par professionnel (identifiant d'utilisateur)."""
    if not pro_user_ids:
        return {}
    rows = db.execute(
        select(Review.pro_id, func.avg(Review.rating), func.count()).where(Review.pro_id.in_(pro_user_ids), Review.state == "VISIBLE").group_by(Review.pro_id)
    ).all()
    return {pid: {"avg": round(float(avg), 1), "count": n} for pid, avg, n in rows}


def public_reviews(db: Db, pro_user_id: str, limit: int = 20) -> list[dict]:
    rows = db.scalars(
        select(Review).where(Review.pro_id == pro_user_id, Review.state == "VISIBLE").order_by(Review.created_at.desc()).limit(limit).options(joinedload(Review.client))
    ).all()
    return [{"id": r.id, "rating": r.rating, "comment": r.comment, "author": f"{r.client.first_name} {r.client.last_name[:1]}.", "createdAt": iso(r.created_at)} for r in rows]


def my_review(db: Db, conversation_id: str) -> dict | None:
    r = db.scalar(select(Review).where(Review.conversation_id == conversation_id))
    return {"rating": r.rating, "comment": r.comment} if r else None


def can_review(db: Db, c: Conversation) -> bool:
    """Le professionnel a répondu au client dans cette conversation."""
    return bool(db.scalar(select(Message.id).where(Message.conversation_id == c.id, Message.sender_id == c.pro_id).limit(1)))


def create_review(db: Db, client: User, c: Conversation, rating, comment: str) -> Review:
    if client.id != c.request.client_id:
        raise ApiError("Conversation introuvable.", 404)
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ApiError("Note : de 1 à 5 étoiles.")
    if len(comment) > MAX_COMMENT:
        raise ApiError(f"Commentaire : {MAX_COMMENT} caractères maximum.")
    if not can_review(db, c):
        raise ApiError("Vous pourrez laisser un avis dès que le professionnel vous aura répondu.", 409)
    if db.scalar(select(Review.id).where(Review.conversation_id == c.id)):
        raise ApiError("Vous avez déjà laissé un avis pour cette conversation.", 409)
    if too_many(db, f"review:{client.id}", 10, 24 * 60):
        raise ApiError("Trop d'avis aujourd'hui. Réessayez demain.", 429)
    r = Review(conversation_id=c.id, pro_id=c.pro_id, client_id=client.id, rating=rating, comment=comment or None)
    db.add(r)
    audit(db, client.id, "Avis déposé", c.id)
    db.commit()
    return r
