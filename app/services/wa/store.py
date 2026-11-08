"""Sessions et réglages WhatsApp en base."""

import hashlib
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session as Db

from ...models import AppSetting, User, WaSession
from ...utils import now, phone_local
from ..queries import users_with_phone

WINDOW = timedelta(hours=24)  # au-delà, Meta n'autorise que les messages modèles


def get_session(db: Db, phone: str) -> WaSession:
    s = db.get(WaSession, phone)
    if not s:
        s = WaSession(phone=phone, state="NEW", data={})
        db.add(s)
        db.flush()
    return s


def save_data(s: WaSession, **changes) -> None:
    """Met à jour les données de la session (JSONB : on remplace le dictionnaire pour que SQLAlchemy voie le changement)."""
    data = dict(s.data or {})
    for k, v in changes.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    s.data = data


def in_window(s: WaSession | None) -> bool:
    return bool(s and s.last_inbound_at and now() - s.last_inbound_at < WINDOW)


def lock_phone(db: Db, phone: str) -> None:
    """Un seul traitement à la fois par numéro (verrou valable jusqu'à la fin de la transaction)."""
    key = int(hashlib.sha256(phone.encode()).hexdigest()[:15], 16)
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": key})


def find_user(db: Db, phone: str) -> User | None:
    """Compte particulier ou professionnel de ce numéro (le premier actif)."""
    users = [u for u in users_with_phone(db, phone) if u.role in ("CLIENT", "PRO")]
    users.sort(key=lambda u: (u.status != "ACTIVE", u.created_at))
    return users[0] if users else None


def user_phone(u: User) -> str | None:
    return phone_local(u.phone)


def enabled(db: Db) -> bool:
    """Interrupteur d'urgence de l'administration."""
    row = db.get(AppSetting, "wa_enabled")
    return not row or row.value != "0"


def set_enabled(db: Db, on: bool) -> None:
    row = db.get(AppSetting, "wa_enabled")
    if row:
        row.value = "1" if on else "0"
    else:
        db.add(AppSetting(key="wa_enabled", value="1" if on else "0"))
    db.flush()


def purge_old(db: Db, days: int = 90) -> int:
    """Supprime les messages du journal plus anciens que `days` jours (confidentialité)."""
    from ...models import WaMessage

    old = db.scalars(select(WaMessage).where(WaMessage.created_at < now() - timedelta(days=days))).all()
    for m in old:
        db.delete(m)
    return len(old)
