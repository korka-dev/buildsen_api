"""Inscription, connexion, déconnexion, mot de passe oublié et confirmation de l'e-mail."""

import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as Db

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..emails import reset_mail, verify_email_mail, welcome_mail
from ..errors import ApiError
from ..mailer import app_url, mail_ctx, notify_team, send_mail
from ..models import ProProfile, Session, User, WaCode
from ..rules import CLIENTS_OPEN
from ..security import (
    ADMIN_PASSWORD_MIN,
    MEMBER_PASSWORD_MIN,
    check_password,
    clear_token,
    create_session,
    destroy_session,
    hash_password,
    issue_user_token,
    password_problem,
    sha256,
    user_for_token,
)
from ..services.queries import users_with_phone
from ..services.wa import notify as wa_notify
from ..services.wa import store as wa_store
from ..tasks import with_db
from ..throttle import clear_throttle, client_ip, too_many
from ..utils import now, phone_local
from ..validators import checked, email, name, phone, text

router = APIRouter(prefix="/api/auth", tags=["Authentification"])

EXISTS = "Un compte existe déjà avec cette adresse. Connectez-vous ou réinitialisez votre mot de passe."
PHONE_EXISTS = "Un compte existe déjà avec ce numéro. Connectez-vous ou réinitialisez votre mot de passe."


def _common_signup(body: dict) -> tuple[str, str, str, str, str]:
    first = name(body, "firstName", "Indiquez votre prénom.")
    last = name(body, "lastName", "Indiquez votre nom.")
    mail = email(body)
    ph = phone(body)
    pw = str(body.get("password") or "")
    if not checked(body, "cgu"):
        raise ApiError("Acceptez les conditions pour créer votre compte.")
    return first, last, mail, ph, pw


def _check_signup(db: Db, request: Request, mail: str, ph: str, pw: str) -> None:
    if problem := password_problem(pw):
        raise ApiError(problem)
    if too_many(db, f"signup:{client_ip(request)}", 10, 60):
        raise ApiError("Trop de tentatives. Réessayez dans une heure.", 429)
    if db.scalar(select(User.id).where(User.email == mail)):
        raise ApiError(EXISTS, 409)
    # Le numéro sert à se connecter : un seul compte par numéro
    if users_with_phone(db, ph):
        raise ApiError(PHONE_EXISTS, 409)


def _after_pro_signup(db: Db, user_id: str) -> None:
    u = db.get(User, user_id)
    p = u.pro
    label = p.company_name if p and p.statut == "ENT" and p.company_name else u.full_name
    who = f"Entreprise, gérant {u.full_name}" if p and p.statut == "ENT" else u.full_name
    notify_team(db, f"Nouveau professionnel inscrit : {label}",
                f"{who}\n{u.email}\n\nSon dossier de vérification est à compléter.\n{app_url('/admin/utilisateurs')}")
    send_mail(u.email, welcome_mail(mail_ctx(), u.first_name))


def _after_client_signup(db: Db, user_id: str) -> None:
    u = db.get(User, user_id)
    notify_team(db, f"Nouveau particulier inscrit : {u.first_name} {u.last_name[:1]}.", f"{u.full_name}\n{u.email}\n\n{app_url('/admin/utilisateurs')}")
    token = issue_user_token(db, u, "verify", 48)
    send_mail(u.email, verify_email_mail(mail_ctx(), u.first_name, app_url(f"/verifier-email?jeton={token}")))


@router.post("/signup-pro")
def signup_pro(request: Request, response: Response, background: BackgroundTasks, body: dict = Body(...), db: Db = Depends(get_db)):
    first, last, mail, ph, pw = _common_signup(body)
    kind = text(body, "kind") if text(body, "kind") in ("IND", "ENT") else "IND"
    company = text(body, "companyName")[:120]
    if kind == "ENT" and len(company) < 2:
        raise ApiError("Indiquez le nom de votre entreprise.")
    _check_signup(db, request, mail, ph, pw)
    user = User(email=mail, phone=ph, first_name=first, last_name=last, password_hash=hash_password(pw), role="PRO")
    user.pro = ProProfile(statut="ENT", company_name=company) if kind == "ENT" else ProProfile()
    db.add(user)
    db.flush()
    audit(db, user.id, "Création d'un compte professionnel", user.email)
    create_session(db, response, user.id)
    db.commit()
    background.add_task(with_db, _after_pro_signup, user.id)
    return {"redirect": "/pro/verification"}


@router.post("/signup-client")
def signup_client(request: Request, response: Response, background: BackgroundTasks, body: dict = Body(...), db: Db = Depends(get_db)):
    if not CLIENTS_OPEN:
        raise ApiError("Les inscriptions des particuliers ne sont pas encore ouvertes.", 403)
    first, last, mail, ph, pw = _common_signup(body)
    _check_signup(db, request, mail, ph, pw)
    user = User(email=mail, phone=ph, first_name=first, last_name=last, password_hash=hash_password(pw), role="CLIENT")
    db.add(user)
    db.flush()
    audit(db, user.id, "Création d'un compte particulier", user.email)
    create_session(db, response, user.id)
    db.commit()
    background.add_task(with_db, _after_client_signup, user.id)
    return {"redirect": "/espace"}


@router.post("/login")
def login(request: Request, response: Response, body: dict = Body(...), db: Db = Depends(get_db)):
    # Connexion par numéro de téléphone (l'e-mail sert au mot de passe oublié et aux notifications)
    raw = text(body, "phone")
    ph = phone_local(raw)
    pw = str(body.get("password") or "")
    if not raw or not pw:
        raise ApiError("Renseignez votre numéro de téléphone et votre mot de passe.")
    if not ph:
        raise ApiError("Numéro sénégalais attendu, par exemple 77 123 45 67.")
    key = f"login:{ph}"
    if too_many(db, key, 5, 15) or too_many(db, f"login-ip:{client_ip(request)}", 30, 15):
        raise ApiError("Trop de tentatives. Réessayez dans 15 minutes.", 429)
    # Anciens comptes : un même numéro peut exister deux fois, le mot de passe départage
    u = next((x for x in users_with_phone(db, ph) if check_password(pw, x.password_hash)), None)
    # Même message que le compte existe ou non, pour ne pas révéler les numéros inscrits
    if not u:
        raise ApiError("Numéro ou mot de passe incorrect.", 401)
    if u.status != "ACTIVE":
        raise ApiError("Ce compte est suspendu. Contactez l'équipe BUILDSEN.", 403)
    clear_throttle(db, key)
    create_session(db, response, u.id)
    db.commit()
    return {"redirect": _landing(u)}


def _landing(u: User) -> str:
    """Page d'arrivée après connexion : directement dans l'espace de la personne (plus de page d'accueil intermédiaire)."""
    if u.role == "PRO":
        return "/pro/verification" if u.pro and u.pro.state in ("DRAFT", "MORE") else "/pro"
    return "/espace" if u.role == "CLIENT" else "/accueil"


CODE_MINUTES = 10
CODE_TRIES = 5


@router.post("/code/request")
def code_request(request: Request, background: BackgroundTasks, body: dict = Body(...), db: Db = Depends(get_db)):
    """Connexion sans mot de passe : un code à 6 chiffres est envoyé sur le WhatsApp du compte."""
    ph = phone_local(text(body, "phone"))
    if not ph:
        raise ApiError("Numéro sénégalais attendu, par exemple 77 123 45 67.")
    if not settings.wa_on:
        raise ApiError("La connexion par WhatsApp n'est pas disponible pour le moment. Utilisez votre mot de passe.", 503)
    if too_many(db, f"wacode:{ph}", 3, 15) or too_many(db, f"wacode-ip:{client_ip(request)}", 20, 15):
        raise ApiError("Trop de demandes de code. Réessayez dans 15 minutes.", 429)
    u = wa_store.find_user(db, ph)
    if u and u.status == "ACTIVE":
        code = f"{secrets.randbelow(10**6):06d}"
        row = db.get(WaCode, ph)
        values = dict(code_hash=sha256(f"{code}:{ph}"), expires_at=now() + timedelta(minutes=CODE_MINUTES), attempts=0)
        if row:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(WaCode(phone=ph, **values))
        db.commit()
        background.add_task(with_db, wa_notify.login_code, ph, code)
    # Même réponse que le compte existe ou non, pour ne pas révéler les numéros inscrits
    return {"ok": "Si ce numéro correspond à un compte, un code vient d'être envoyé sur WhatsApp. Il est valable 10 minutes."}


@router.post("/code/login")
def code_login(response: Response, body: dict = Body(...), db: Db = Depends(get_db)):
    ph = phone_local(text(body, "phone"))
    code = "".join(c for c in text(body, "code") if c.isdigit())
    if not ph or len(code) != 6:
        raise ApiError("Entrez le code à 6 chiffres reçu sur WhatsApp.")
    if too_many(db, f"wacode-try:{ph}", 10, 15):
        raise ApiError("Trop de tentatives. Réessayez dans 15 minutes.", 429)
    wrong = ApiError("Code incorrect ou expiré. Demandez un nouveau code.", 401)
    row = db.get(WaCode, ph)
    if not row or row.expires_at < now() or row.attempts >= CODE_TRIES:
        raise wrong
    row.attempts += 1
    db.commit()
    if not hmac.compare_digest(row.code_hash, sha256(f"{code}:{ph}")):
        raise wrong
    u = wa_store.find_user(db, ph)
    if not u or u.status != "ACTIVE":
        raise wrong
    db.delete(row)  # un code ne sert qu'une fois
    create_session(db, response, u.id)
    audit(db, u.id, "Connexion par code WhatsApp")
    db.commit()
    return {"redirect": _landing(u)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Db = Depends(get_db)):
    destroy_session(db, request, response)
    db.commit()
    return {"redirect": "/"}


@router.post("/forgot")
def forgot(background: BackgroundTasks, body: dict = Body(...), db: Db = Depends(get_db)):
    mail = email(body, message="Indiquez une adresse e-mail valide.")
    done = {"ok": "Si un compte correspond à cette adresse, un lien de réinitialisation vient d'être envoyé. Il est valable 1 heure."}
    if too_many(db, f"forgot:{mail}", 3, 60):
        return done
    u = db.scalar(select(User).where(User.email == mail))
    if u and u.role != "ADMIN" and u.status == "ACTIVE":
        token = issue_user_token(db, u, "reset", 1)
        background.add_task(send_mail, u.email, reset_mail(mail_ctx(), u.first_name, app_url(f"/reinitialiser?jeton={token}")))
    return done


@router.get("/reset/check")
def reset_check(token: str = "", db: Db = Depends(get_db)):
    """La page « nouveau mot de passe » vérifie le lien avant d'afficher le formulaire."""
    return {"valid": user_for_token(db, token, "reset") is not None}


@router.post("/reset")
def reset_password(body: dict = Body(...), db: Db = Depends(get_db)):
    u = user_for_token(db, text(body, "token"), "reset")
    if not u:
        raise ApiError("Ce lien n'est plus valable. Demandez un nouveau lien.", 410)
    pw = str(body.get("password") or "")
    if problem := password_problem(pw, ADMIN_PASSWORD_MIN if u.role == "ADMIN" else MEMBER_PASSWORD_MIN):
        raise ApiError(problem)
    u.password_hash = hash_password(pw)
    clear_token(u)
    # Toutes les sessions de ce compte sont fermées
    db.execute(delete(Session).where(Session.user_id == u.id))
    audit(db, u.id, "Mot de passe réinitialisé", u.email)
    db.commit()
    return {"redirect": "/connexion?reinitialise=1"}


@router.post("/verify-email")
def verify_email(body: dict = Body(...), db: Db = Depends(get_db)):
    """Lien reçu par e-mail par un particulier. Protégé par le jeton secret à usage unique."""
    u = user_for_token(db, text(body, "token"), "verify")
    if not u:
        return {"ok": False}
    u.email_verified_at = now()
    clear_token(u)
    audit(db, u.id, "Adresse e-mail confirmée", u.email)
    db.commit()
    return {"ok": True, "firstName": u.first_name}
