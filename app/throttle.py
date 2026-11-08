"""Limitation des tentatives (table Throttle), identique à la version Next.js."""

from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.orm import Session as Db

from .config import settings
from .models import Throttle
from .utils import now


def client_ip(request: Request) -> str:
    """Adresse du visiteur. Derrière nos propres relais, on lit X-Forwarded-For en partant de la FIN : la première valeur peut être inventée
    par le visiteur (et contournerait les limites par adresse) ; chaque relais de confiance ajoute la sienne à la fin."""
    if settings.trust_proxy:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if parts:
            return parts[-settings.proxy_hops] if len(parts) >= settings.proxy_hops else parts[0]
        if request.headers.get("x-real-ip"):
            return request.headers["x-real-ip"]
    return request.client.host if request.client else "local"


def too_many(db: Db, key: str, max_count: int, minutes: int) -> bool:
    """True si la clé a dépassé `max_count` tentatives dans la fenêtre, sinon compte une tentative."""
    t = db.get(Throttle, key)
    if not t or t.reset_at < now():
        if t:
            t.count, t.reset_at = 1, now() + timedelta(minutes=minutes)
        else:
            db.add(Throttle(key=key, count=1, reset_at=now() + timedelta(minutes=minutes)))
        db.commit()
        return False
    if t.count >= max_count:
        return True
    t.count += 1
    db.commit()
    return False


def clear_throttle(db: Db, key: str) -> None:
    db.execute(delete(Throttle).where(Throttle.key == key))
    db.commit()
