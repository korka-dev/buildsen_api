"""Requêtes groupées réutilisées par plusieurs routes (évitent une requête par ligne affichée)."""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from ..models import Conversation, Message, User


def last_messages(db: Db, conversation_ids: Iterable[str]) -> dict[str, Message]:
    """Dernier message de chaque conversation (DISTINCT ON de PostgreSQL)."""
    ids = list(conversation_ids)
    if not ids:
        return {}
    rows = db.scalars(
        select(Message).where(Message.conversation_id.in_(ids))
        .order_by(Message.conversation_id, Message.created_at.desc())
        .distinct(Message.conversation_id)
    ).all()
    return {m.conversation_id: m for m in rows}


def conversation_counts(db: Db, request_ids: Iterable[str]) -> dict[str, int]:
    """Nombre de professionnels qui ont écrit, par demande."""
    ids = list(request_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(Conversation.request_id, func.count()).where(Conversation.request_id.in_(ids)).group_by(Conversation.request_id)
    ).all()
    return {rid: n for rid, n in rows}


def my_conversations(db: Db, pro_user_id: str, request_ids: Iterable[str]) -> dict[str, str]:
    """Conversation du professionnel connecté sur chaque demande, s'il a déjà écrit."""
    ids = list(request_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(Conversation.request_id, Conversation.id).where(Conversation.pro_id == pro_user_id, Conversation.request_id.in_(ids))
    ).all()
    return {rid: cid for rid, cid in rows}


def users_with_phone(db: Db, local: str, exclude_id: str | None = None) -> list[User]:
    """Comptes particuliers et pros dont le numéro (9 derniers chiffres) correspond, administrateurs exclus."""
    digits = func.right(func.regexp_replace(User.phone, r"\D", "", "g"), 9)
    q = select(User).where(digits == local, User.role != "ADMIN")
    if exclude_id:
        q = q.where(User.id != exclude_id)
    return list(db.scalars(q))
