"""Dépendances FastAPI : base de données, session du visiteur et contrôles d'accès par rôle."""

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session as Db

from .db import get_db
from .errors import ApiError
from .models import Session, User
from .security import load_session


def current_session(request: Request, db: Db = Depends(get_db)) -> Session | None:
    # Une seule lecture par requête, même si plusieurs dépendances la demandent
    if not hasattr(request.state, "bs_session"):
        request.state.bs_session = load_session(db, request)
    return request.state.bs_session


def require_member(s: Session | None = Depends(current_session)) -> User:
    """Particulier ou professionnel connecté."""
    if not s or s.user.role not in ("CLIENT", "PRO"):
        raise ApiError("Connectez-vous.", 401)
    return s.user


def require_client(s: Session | None = Depends(current_session)) -> User:
    if not s or s.user.role != "CLIENT":
        raise ApiError("Connectez-vous à votre espace particulier.", 401)
    return s.user


def require_pro(s: Session | None = Depends(current_session)) -> User:
    if not s or s.user.role != "PRO":
        raise ApiError("Connectez-vous à votre espace professionnel.", 401)
    return s.user


def require_admin(*roles: str) -> Callable[..., User]:
    """Administrateur ayant passé la double authentification, avec l'un des rôles donnés (le super-admin a tous les droits)."""

    def dep(s: Session | None = Depends(current_session)) -> User:
        if not s or s.user.role != "ADMIN" or not s.mfa or not s.user.admin_role:
            raise ApiError("Connectez-vous à l'administration.", 401)
        if roles and s.user.admin_role not in roles and s.user.admin_role != "SUPER":
            raise ApiError("Accès réservé.", 403)
        return s.user

    return dep
