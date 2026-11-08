"""Journal d'audit en écriture seule. L'écriture est validée avec la transaction de l'appelant."""

from sqlalchemy.orm import Session as Db

from .models import AuditLog


def audit(db: Db, actor_id: str | None, action: str, target: str | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, target=target))
