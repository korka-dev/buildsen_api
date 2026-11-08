"""Mots de passe, sessions, jetons à usage unique et double authentification.

Compatible avec la version Next.js : même cookie « bs_session », même table Session (empreinte SHA-256 du jeton),
mêmes empreintes bcrypt (coût 12) et mêmes secrets TOTP. Les utilisateurs restent connectés lors du changement de version.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

import bcrypt
import pyotp
from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as Db

from .config import settings
from .models import Session, User
from .utils import now

COOKIE = "bs_session"
# Sessions glissantes : prolongées à chaque visite, elles n'expirent qu'après cette durée sans utilisation.
MEMBER_DAYS = 90
ADMIN_DAYS = 7
# Le cookie dure plus longtemps que la session : c'est la date enregistrée en base qui fait foi
COOKIE_DAYS = 400


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(pw: str) -> str:
    # bcrypt ne lit que les 72 premiers octets (comme bcryptjs côté Node)
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(12)).decode()


def check_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


# Longueur minimale : 8 caractères pour les particuliers et les pros, 12 pour l'équipe (admin)
MEMBER_PASSWORD_MIN = 8
ADMIN_PASSWORD_MIN = 12


# Mots de passe les plus courants : refusés quelle que soit leur longueur
_COMMON = {
    "password", "password1", "password123", "motdepasse", "motdepasse1", "motdepasse123", "azertyuiop", "azerty123", "azerty1234", "qwertyuiop", "qwerty123", "123456789",
    "1234567890", "12345678", "123123123", "000000000", "111111111", "0123456789", "iloveyou", "buildsen", "buildsen123", "senegal", "senegal123", "dakar123", "bonjour123",
    "admin1234", "administrateur", "letmein123", "welcome123", "abc123456", "passw0rd", "p@ssw0rd", "changeme", "changeme123",
}


def password_problem(pw: str, minimum: int = MEMBER_PASSWORD_MIN) -> str:
    if len(pw) < minimum:
        return f"Le mot de passe doit contenir au moins {minimum} caractères."
    if len(pw) > 200:
        return "Mot de passe trop long."
    if "\x00" in pw:
        return "Le mot de passe contient un caractère non accepté."
    plain = pw.lower().replace(" ", "")
    if plain in _COMMON or len(set(plain)) <= 2 or plain.isdigit() and len(set(plain)) <= 3:
        return "Ce mot de passe est trop courant. Choisissez-en un moins facile à deviner."
    return ""


def _session_length(admin: bool) -> timedelta:
    return timedelta(days=ADMIN_DAYS if admin else MEMBER_DAYS)


def create_session(db: Db, response: Response, user_id: str, admin: bool = False) -> None:
    token = new_token()
    db.add(Session(token_hash=sha256(token), user_id=user_id, mfa=admin, expires_at=now() + _session_length(admin)))
    response.set_cookie(
        COOKIE, token, max_age=COOKIE_DAYS * 86400, httponly=True, secure=settings.production, samesite="lax", path="/",
    )


def destroy_session(db: Db, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(Session).where(Session.token_hash == sha256(token)))
    response.delete_cookie(COOKIE, path="/")


def load_session(db: Db, request: Request) -> Session | None:
    """Session valide du visiteur, prolongée au plus une fois par jour."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    s = db.scalar(select(Session).where(Session.token_hash == sha256(token)))
    if not s or s.expires_at < now() or s.user.status != "ACTIVE":
        return None
    renewed = now() + _session_length(s.mfa)
    if renewed - s.expires_at > timedelta(days=1):
        s.expires_at = renewed
        db.commit()
    return s


# ---------- Jetons à usage unique (activation administrateur, mot de passe oublié, confirmation de l'e-mail) ----------

def issue_user_token(db: Db, user: User, purpose: str, hours: int) -> str:
    token = new_token()
    user.token_hash, user.token_purpose, user.token_expires_at = sha256(token), purpose, now() + timedelta(hours=hours)
    db.commit()
    return token


def user_for_token(db: Db, token: str, purpose: str) -> User | None:
    if not token:
        return None
    u = db.scalar(select(User).where(User.token_hash == sha256(token)))
    if not u or u.token_purpose != purpose or not u.token_expires_at or u.token_expires_at < now():
        return None
    return u


def clear_token(user: User) -> None:
    user.token_hash = user.token_purpose = user.token_expires_at = None


# ---------- Double authentification (TOTP, SHA-1, 6 chiffres, 30 secondes) ----------

def new_totp_secret() -> str:
    return pyotp.random_base32(32)


def totp_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="BUILDSEN")


def check_totp(secret: str | None, code: str) -> bool:
    if not secret or not code.isdigit() or len(code) != 6:
        return False
    if settings.admin_dev_code_ok and hmac.compare_digest(code, settings.admin_dev_code):
        return True  # développement local seulement
    return pyotp.TOTP(secret).verify(code, valid_window=1)
