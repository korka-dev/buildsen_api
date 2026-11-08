"""Administration : connexion avec double authentification, dossiers, utilisateurs, demandes, signalements, vitrine, équipe, journal."""

import re

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload, selectinload

from ..audit import audit
from ..db import get_db
from ..deps import require_admin
from ..emails import more_info_mail, refused_mail, request_removed_mail, verified_mail
from ..errors import ApiError, not_found
from ..mailer import app_url, mail_ctx, send_email, send_mail
from ..models import Ad, AuditLog, Conversation, Credential, Payment, ProProfile, Report, Review, ServiceRequest, Session, User
from ..rules import ADMIN_ROLES, CLIENTS_OPEN, DOC_TYPES, LAUNCH_OFFER, PLANS, STATUTS, can_decide
from ..security import (
    ADMIN_PASSWORD_MIN,
    check_password,
    check_totp,
    clear_token,
    create_session,
    destroy_session,
    hash_password,
    issue_user_token,
    new_totp_secret,
    password_problem,
    totp_uri,
    user_for_token,
)
from ..serializers import ad_out, message_out, pro_private_out, request_out, user_out
from ..services.market import pro_name, request_title
from ..services.queries import conversation_counts
from ..storage import remove_object
from ..throttle import clear_throttle, client_ip, too_many
from ..utils import add_months, iso, now
from ..validators import email, text

router = APIRouter(prefix="/api/admin", tags=["Administration"])


def _pending_dossiers(db: Db) -> list[ProProfile]:
    """Dossiers à examiner : soumis, ou complets mais pas encore soumis."""
    ps = db.scalars(select(ProProfile).where(ProProfile.state.in_(["PENDING", "DRAFT", "MORE"])).options(selectinload(ProProfile.credentials))).all()
    return [p for p in ps if can_decide(p)]


# ---------- Connexion ----------

@router.post("/login")
def admin_login(request: Request, response: Response, body: dict = Body(...), db: Db = Depends(get_db)):
    mail = text(body, "email").lower()
    pw = str(body.get("password") or "")
    code = re.sub(r"\s", "", text(body, "code"))
    key = f"admin:{mail}"
    if too_many(db, key, 5, 15) or too_many(db, f"admin-ip:{client_ip(request)}", 20, 15):
        raise ApiError("Trop de tentatives. Réessayez dans 15 minutes.", 429)
    u = db.scalar(select(User).where(User.email == mail))
    ok = (u and u.role == "ADMIN" and u.status == "ACTIVE" and u.totp_enabled
          and check_password(pw, u.password_hash) and check_totp(u.totp_secret, code))
    if not ok:
        raise ApiError("Identifiants ou code de double authentification incorrects.", 401)
    clear_throttle(db, key)
    create_session(db, response, u.id, admin=True)
    audit(db, u.id, "Connexion à l'administration")
    db.commit()
    return {"redirect": "/admin"}


@router.post("/logout")
def admin_logout(request: Request, response: Response, db: Db = Depends(get_db)):
    destroy_session(db, request, response)
    db.commit()
    return {"redirect": "/admin/connexion"}


@router.get("/activation")
def activation_info(token: str, db: Db = Depends(get_db)):
    """Page d'activation d'un administrateur invité : secret de double authentification à scanner."""
    u = user_for_token(db, token, "setup")
    if not u or u.role != "ADMIN" or not u.totp_secret:
        return {"valid": False}
    return {"valid": True, "firstName": u.first_name, "email": u.email, "role": ADMIN_ROLES.get(u.admin_role or "VERIF"),
            "secret": u.totp_secret, "uri": totp_uri(u.email, u.totp_secret)}


@router.post("/activate")
def activate(response: Response, body: dict = Body(...), db: Db = Depends(get_db)):
    u = user_for_token(db, text(body, "token"), "setup")
    if not u or u.role != "ADMIN":
        raise ApiError("Ce lien d'activation n'est plus valable. Demandez une nouvelle invitation.", 410)
    pw = str(body.get("password") or "")
    if problem := password_problem(pw, ADMIN_PASSWORD_MIN):
        raise ApiError(problem)
    if pw != str(body.get("password2") or ""):
        raise ApiError("Les deux mots de passe ne correspondent pas.")
    if not check_totp(u.totp_secret, re.sub(r"\s", "", text(body, "code"))):
        raise ApiError("Code incorrect. Vérifiez l'heure de votre téléphone et saisissez le code affiché par l'application.")
    u.password_hash, u.totp_enabled = hash_password(pw), True
    clear_token(u)
    audit(db, u.id, "Compte administrateur activé", u.email)
    create_session(db, response, u.id, admin=True)
    db.commit()
    return {"redirect": "/admin"}


# ---------- Navigation et vue d'ensemble ----------

@router.get("/me")
def admin_me(admin: User = Depends(require_admin()), db: Db = Depends(get_db)):
    """Administrateur connecté et compteurs du menu."""
    to_review = db.scalar(select(func.count(func.distinct(ServiceRequest.id))).select_from(ServiceRequest).join(Report, Report.request_id == ServiceRequest.id)
                          .where(ServiceRequest.state == "PUBLISHED", Report.state == "OPEN"))
    reports = db.scalar(select(func.count()).select_from(Report).where(Report.state == "OPEN"))
    return {"admin": user_out(admin), "roleLabel": ADMIN_ROLES[admin.admin_role],
            "counts": {"dossiers": len(_pending_dossiers(db)), "requests": to_review, "reports": reports}, "clientsOpen": CLIENTS_OPEN}


@router.get("/overview")
def overview(admin: User = Depends(require_admin()), db: Db = Depends(get_db)):
    count = lambda stmt: db.scalar(stmt)  # noqa: E731
    flagged = count(select(func.count(func.distinct(ServiceRequest.id))).select_from(ServiceRequest).join(Report, Report.request_id == ServiceRequest.id)
                    .where(ServiceRequest.state == "PUBLISHED", Report.state == "OPEN"))
    return {
        "pros": count(select(func.count()).select_from(User).where(User.role == "PRO")),
        "verified": count(select(func.count()).select_from(ProProfile).where(ProProfile.state == "VERIFIED")),
        "pending": len(_pending_dossiers(db)),
        "more": count(select(func.count()).select_from(ProProfile).where(ProProfile.state == "MORE")),
        "clients": count(select(func.count()).select_from(User).where(User.role == "CLIENT")),
        "flagged": flagged,
        "published": count(select(func.count()).select_from(ServiceRequest).where(ServiceRequest.state == "PUBLISHED")),
        "conversations": count(select(func.count()).select_from(Conversation)),
        "reports": count(select(func.count()).select_from(Report).where(Report.state == "OPEN")),
        "clientsOpen": CLIENTS_OPEN,
    }


# ---------- Dossiers des professionnels ----------

@router.get("/dossiers")
def dossiers(etat: str = "PENDING", admin: User = Depends(require_admin("VERIF")), db: Db = Depends(get_db)):
    state = etat if etat in ("DRAFT", "PENDING", "MORE", "VERIFIED", "REFUSED") else "PENDING"
    q = select(ProProfile).options(joinedload(ProProfile.user), selectinload(ProProfile.credentials), selectinload(ProProfile.portfolio))
    q = q.where(ProProfile.state.in_(["PENDING", "DRAFT", "MORE"]) if state == "PENDING" else ProProfile.state == state)
    ps = db.scalars(q.order_by(ProProfile.submitted_at.asc().nulls_first(), ProProfile.created_at.asc())).all()
    # L'onglet « En attente » inclut les dossiers complets que le professionnel a oublié de soumettre
    listed = [p for p in ps if can_decide(p)] if state == "PENDING" else ps
    return {"state": state, "dossiers": [{
        **pro_private_out(p), "user": user_out(p.user), "credentialsCount": len(p.credentials), "portfolioCount": len(p.portfolio),
        "notSubmitted": state == "PENDING" and p.state != "PENDING",
    } for p in listed]}


@router.get("/dossiers/{pro_id}")
def dossier(pro_id: str, admin: User = Depends(require_admin("VERIF")), db: Db = Depends(get_db)):
    p = db.get(ProProfile, pro_id)
    if not p:
        raise not_found("Dossier introuvable.")
    decidable = can_decide(p)
    return {"pro": pro_private_out(p, with_files=True), "user": user_out(p.user), "canDecide": decidable, "notSubmitted": decidable and p.state != "PENDING"}


@router.post("/credentials/{credential_id}/decide")
def decide_document(credential_id: str, body: dict = Body(...), admin: User = Depends(require_admin("VERIF")), db: Db = Depends(get_db)):
    c = db.get(Credential, credential_id)
    if not c:
        raise not_found()
    accept = bool(body.get("accept"))
    reason = text(body, "reason")[:300]
    if not accept and len(reason) < 5:
        raise ApiError("Indiquez le motif du refus.")
    c.state, c.reason = ("ACCEPTED", None) if accept else ("REFUSED", reason)
    audit(db, admin.id, f"{DOC_TYPES.get(c.type, c.type)} {'accepté' if accept else 'refusé — ' + reason}", c.pro.user.full_name)
    db.commit()
    return {"ok": True}


@router.post("/dossiers/{pro_id}/decide")
def decide_dossier(pro_id: str, background: BackgroundTasks, body: dict = Body(...), admin: User = Depends(require_admin("VERIF")), db: Db = Depends(get_db)):
    decision, reason = text(body, "decision"), text(body, "reason")[:600]
    if decision not in ("VERIFIED", "MORE", "REFUSED"):
        raise ApiError("Choisissez une décision.")
    if decision != "VERIFIED" and len(reason) < 5:
        raise ApiError("Indiquez un motif : il sera transmis au professionnel.")
    p = db.get(ProProfile, pro_id)
    if not p:
        raise not_found("Dossier introuvable.")
    if not can_decide(p):
        raise ApiError("Ce dossier n'est pas complet : statut, formation, pièce d'identité et diplôme sont nécessaires.", 409)
    if decision == "VERIFIED" and any(c.state == "REFUSED" for c in p.credentials):
        raise ApiError("Un justificatif est refusé : demandez un complément plutôt que de valider.", 409)
    p.state, p.decided_at, p.decision_reason = decision, now(), reason or None
    p.welcome_granted = p.welcome_granted or decision == "VERIFIED"
    if decision == "VERIFIED":
        for c in p.credentials:
            if c.state == "RECEIVED":
                c.state = "ACCEPTED"
        # Offre de lancement : formule Pro offerte quelques mois aux premiers professionnels vérifiés, une seule fois
        offered = db.scalar(select(func.count()).select_from(ProProfile).where(ProProfile.launch_offer.is_(True)))
        if not p.launch_offer and offered < LAUNCH_OFFER["first_pros"]:
            p.launch_offer, p.plan, p.plan_until = True, LAUNCH_OFFER["plan"], add_months(now(), LAUNCH_OFFER["months"])
    label = {"VERIFIED": "accepté", "MORE": "complément demandé", "REFUSED": "refusé"}[decision]
    audit(db, admin.id, f"Dossier {label}{' — ' + reason if reason else ''}", p.user.full_name)
    db.commit()
    ctx, first = mail_ctx(), p.user.first_name
    mail = verified_mail(ctx, first) if decision == "VERIFIED" else more_info_mail(ctx, first, reason) if decision == "MORE" else refused_mail(ctx, first, reason)
    background.add_task(send_mail, p.user.email, mail)
    return {"ok": "Décision enregistrée. Le professionnel est prévenu par e-mail."}


# ---------- Utilisateurs ----------

@router.get("/users")
def users(admin: User = Depends(require_admin("VERIF", "SUPPORT")), db: Db = Depends(get_db)):
    us = db.scalars(select(User).where(User.role.in_(["PRO", "CLIENT"])).order_by(User.created_at.desc()).limit(200).options(joinedload(User.pro))).all()
    return {
        "canPlan": admin.admin_role in ("SUPER", "SUPPORT"), "canDelete": admin.admin_role == "SUPER",
        "users": [{**user_out(u), "pro": pro_private_out(u.pro) if u.pro else None} for u in us],
    }


@router.post("/users/{user_id}/suspend")
def toggle_suspend(user_id: str, admin: User = Depends(require_admin("VERIF", "SUPPORT", "MODER")), db: Db = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or u.role == "ADMIN":
        raise not_found("Compte introuvable.")
    u.status = "SUSPENDED" if u.status == "ACTIVE" else "ACTIVE"
    if u.status == "SUSPENDED":
        db.execute(delete(Session).where(Session.user_id == u.id))
    audit(db, admin.id, "Compte suspendu" if u.status == "SUSPENDED" else "Compte réactivé", u.full_name)
    db.commit()
    return {"ok": True, "status": u.status}


@router.post("/users/{user_id}/plan")
def set_plan(user_id: str, body: dict = Body(...), admin: User = Depends(require_admin("SUPPORT")), db: Db = Depends(get_db)):
    """Formule attribuée par l'équipe (offre, geste commercial, ou paiement Wave / Orange Money reçu)."""
    plan = text(body, "plan")
    if plan not in PLANS:
        raise ApiError("Choisissez une formule.")
    try:
        months = int(body.get("months") or 0)
    except (TypeError, ValueError):
        months = 0
    if plan != "FREE" and not 1 <= months <= 24:
        raise ApiError("Durée : entre 1 et 24 mois.")
    u = db.get(User, user_id)
    if not u or not u.pro:
        raise ApiError("Seuls les professionnels ont une formule.")
    until = None if plan == "FREE" else add_months(now(), months)
    u.pro.plan, u.pro.plan_until = plan, until
    audit(db, admin.id, f"Formule {PLANS[plan]['name']}" + (f" jusqu'au {until:%d/%m/%Y}" if until else ""), u.full_name)
    db.commit()
    return {"ok": f"Formule {PLANS[plan]['name']} enregistrée."}


@router.post("/users/{user_id}/delete")
def delete_user(user_id: str, body: dict = Body(...), admin: User = Depends(require_admin("SUPER")), db: Db = Depends(get_db)):
    """Suppression définitive d'un particulier ou d'un professionnel, de ses fichiers et, en cascade, de ses demandes et conversations."""
    if text(body, "confirm").upper() != "SUPPRIMER":
        raise ApiError("Saisissez SUPPRIMER pour confirmer.")
    u = db.get(User, user_id)
    if not u or u.role == "ADMIN":
        raise not_found("Compte introuvable.")
    # L'historique des paiements doit rester disponible pour la comptabilité : un compte qui a payé se suspend, il ne se supprime pas
    if db.scalar(select(func.count()).select_from(Payment).where(Payment.user_id == u.id, Payment.state == "PAID")):
        raise ApiError("Ce compte a des paiements enregistrés : suspendez-le plutôt que de le supprimer (l'historique doit être conservé).", 409)
    if u.pro:
        for c in u.pro.credentials:
            remove_object("justificatifs", c.path)
        for it in u.pro.portfolio:
            remove_object("portfolios", it.path)
    name, role = u.full_name, u.role
    db.delete(u)
    audit(db, admin.id, f"Compte supprimé définitivement ({'professionnel' if role == 'PRO' else 'particulier'})", name)
    db.commit()
    return {"ok": f"Compte de {name} supprimé."}


# ---------- Équipe d'administration ----------

@router.get("/team")
def team(admin: User = Depends(require_admin("SUPER")), db: Db = Depends(get_db)):
    admins = db.scalars(select(User).where(User.role == "ADMIN").order_by(User.created_at.asc())).all()
    return {"admins": [{**user_out(u), "roleLabel": ADMIN_ROLES.get(u.admin_role or "VERIF"), "active": u.totp_enabled} for u in admins]}


@router.post("/team/invite")
def invite_admin(background: BackgroundTasks, body: dict = Body(...), admin: User = Depends(require_admin("SUPER")), db: Db = Depends(get_db)):
    first, last = text(body, "firstName")[:60], text(body, "lastName")[:60]
    if not first:
        raise ApiError("Prénom requis.")
    if not last:
        raise ApiError("Nom requis.")
    mail = email(body, message="E-mail invalide.")
    role = text(body, "role")
    if role not in ADMIN_ROLES:
        raise ApiError("Choisissez un rôle.")
    if db.scalar(select(User.id).where(User.email == mail)):
        raise ApiError("Un compte existe déjà avec cette adresse.", 409)
    u = User(email=mail, first_name=first, last_name=last, role="ADMIN", admin_role=role, totp_secret=new_totp_secret(), totp_enabled=False)
    db.add(u)
    db.flush()
    token = issue_user_token(db, u, "setup", 72)
    link = app_url(f"/admin/activation?jeton={token}")
    audit(db, admin.id, f"Invitation administrateur ({ADMIN_ROLES[role]})", u.email)
    db.commit()
    background.add_task(send_email, u.email, "Activez votre accès administrateur BUILDSEN",
                        f"Bonjour {u.first_name},\n\nVous êtes invité à l'administration BUILDSEN ({ADMIN_ROLES[role]}). Activez votre accès sous 72 heures :\n{link}\n\n"
                        "Il vous faudra une application d'authentification (Google Authenticator, Microsoft Authenticator…).")
    return {"ok": "Invitation créée. Transmettez ce lien à la personne (valable 72 heures, à usage unique) :", "link": link}


# ---------- Demandes des particuliers ----------

@router.get("/requests")
def requests(etat: str = "PUBLISHED", signalees: bool = False, admin: User = Depends(require_admin("MODER")), db: Db = Depends(get_db)):
    state = etat if etat in ("PUBLISHED", "REJECTED", "CLOSED") else "PUBLISHED"
    q = select(ServiceRequest).where(ServiceRequest.state == state).options(joinedload(ServiceRequest.client))
    if signalees:
        q = q.where(ServiceRequest.reports.any(Report.state == "OPEN"))
    reqs = db.scalars(q.order_by(ServiceRequest.created_at.desc()).limit(100)).all()
    counts = conversation_counts(db, [r.id for r in reqs])
    open_reports = db.scalars(select(Report).where(Report.request_id.in_([r.id for r in reqs]), Report.state == "OPEN").options(joinedload(Report.reporter))).all() if reqs else []
    by_request: dict[str, list] = {}
    for rep in open_reports:
        by_request.setdefault(rep.request_id, []).append({"id": rep.id, "reason": rep.reason, "createdAt": iso(rep.created_at), "reporter": rep.reporter.full_name})
    return {"state": state, "flagged": signalees, "requests": [{
        **request_out(r, conversations=counts.get(r.id, 0)),
        "client": {"name": r.client.full_name, "email": r.client.email, "phone": r.client.phone},
        "reports": by_request.get(r.id, []),
    } for r in reqs]}


@router.post("/requests/{request_id}/remove")
def remove_request(request_id: str, background: BackgroundTasks, body: dict = Body(...), admin: User = Depends(require_admin("MODER")), db: Db = Depends(get_db)):
    """Les demandes sont publiées directement ; l'équipe peut en retirer une, avec un motif transmis au client."""
    reason = text(body, "reason")[:600]
    if len(reason) < 5:
        raise ApiError("Indiquez un motif : il sera transmis au client.")
    r = db.get(ServiceRequest, request_id)
    if not r or r.state not in ("PUBLISHED", "PENDING"):
        raise ApiError("Cette demande n'est plus en ligne.", 409)
    r.state, r.reject_reason, r.closed_at = "REJECTED", reason, now()
    db.execute(update(Report).where(Report.request_id == r.id, Report.state == "OPEN").values(state="CLOSED", handled_at=now()))
    audit(db, admin.id, f"Demande retirée — {reason}", r.ref)
    db.commit()
    background.add_task(send_mail, r.client.email, request_removed_mail(mail_ctx(), r.client.first_name, request_title(r), reason))
    return {"ok": "Demande retirée. Le client est prévenu par e-mail."}


# ---------- Signalements ----------

@router.get("/reports")
def reports(traites: bool = False, voir: str | None = None, admin: User = Depends(require_admin("MODER", "SUPPORT")), db: Db = Depends(get_db)):
    """La conversation n'est lue par l'équipe que dans ce cadre (consultation journalisée, avec ?voir=<id>)."""
    reps = db.scalars(
        select(Report).where(Report.state == ("CLOSED" if traites else "OPEN")).order_by(Report.created_at.desc()).limit(100)
        .options(joinedload(Report.reporter), joinedload(Report.request).joinedload(ServiceRequest.client),
                 joinedload(Report.conversation).joinedload(Conversation.request).joinedload(ServiceRequest.client),
                 joinedload(Report.conversation).joinedload(Conversation.pro).joinedload(User.pro))
    ).unique().all()
    if voir:
        audit(db, admin.id, "Consultation d'une conversation signalée", voir)
        db.commit()
    out = []
    for r in reps:
        c = r.conversation
        reported = (c.request.client if r.reporter_id == c.pro_id else c.pro) if c else (r.request.client if r.request else None)
        item = {
            "id": r.id, "state": r.state, "reason": r.reason, "createdAt": iso(r.created_at),
            "reporter": {"name": r.reporter.full_name, "role": r.reporter.role},
            "reported": {"id": reported.id, "name": reported.full_name, "status": reported.status} if reported and reported.role != "ADMIN" else None,
            "request": {"ref": r.request.ref, "title": request_title(r.request), "client": r.request.client.full_name} if r.request else None,
            "conversation": None,
        }
        if c:
            item["conversation"] = {
                "id": c.id, "client": c.request.client.full_name, "clientFirstName": c.request.client.first_name, "pro": pro_name(c.pro, c.pro.pro),
                "proId": c.pro_id, "title": request_title(c.request),
                "messages": [message_out(m) for m in c.messages] if voir == c.id else None,
            }
        out.append(item)
    return {"reports": out}


@router.post("/reports/{report_id}/close")
def close_report(report_id: str, admin: User = Depends(require_admin("MODER", "SUPPORT")), db: Db = Depends(get_db)):
    r = db.get(Report, report_id)
    if not r:
        raise not_found("Signalement introuvable.")
    if r.state == "OPEN":
        r.state, r.handled_at = "CLOSED", now()
        audit(db, admin.id, "Signalement traité", report_id)
        db.commit()
    return {"ok": True}


# ---------- Vitrine de l'accueil ----------

@router.get("/showcase")
def showcase(admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    pros = db.scalars(select(ProProfile).where(ProProfile.state == "VERIFIED").options(joinedload(ProProfile.user), selectinload(ProProfile.portfolio))
                      .order_by(ProProfile.spotlight.desc(), ProProfile.featured.desc(), ProProfile.decided_at.desc().nulls_last())).all()
    ads = db.scalars(select(Ad).order_by(Ad.created_at.desc())).all()
    return {
        "pros": [{"id": p.id, "name": pro_name(p.user, p), "statut": STATUTS[p.statut]["name"] if p.statut else None, "commune": p.commune,
                  "portfolioCount": len(p.portfolio), "featured": p.featured, "spotlight": p.spotlight, "visible": p.visible} for p in pros],
        "ads": [ad_out(a) for a in ads],
    }


def _verified_pro(db: Db, pro_id: str) -> ProProfile:
    p = db.get(ProProfile, pro_id)
    if not p or p.state != "VERIFIED":
        raise not_found("Profil introuvable.")
    return p


@router.post("/showcase/{pro_id}/featured")
def toggle_featured(pro_id: str, admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    p = _verified_pro(db, pro_id)
    p.featured = not p.featured
    audit(db, admin.id, "Mis en vedette" if p.featured else "Retiré des profils en vedette", pro_name(p.user, p))
    db.commit()
    return {"ok": True}


@router.post("/showcase/{pro_id}/spotlight")
def toggle_spotlight(pro_id: str, admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    """Un seul profil « À la une » à la fois."""
    p = _verified_pro(db, pro_id)
    was = p.spotlight
    db.execute(update(ProProfile).where(ProProfile.spotlight.is_(True)).values(spotlight=False))
    if not was:
        p.spotlight = True
    audit(db, admin.id, "Retiré de « À la une »" if was else "Mis « À la une »", pro_name(p.user, p))
    db.commit()
    return {"ok": True}


@router.post("/ads")
def create_ad(body: dict = Body(...), admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    title, txt = text(body, "title"), text(body, "text")
    label, href = text(body, "linkLabel")[:30], text(body, "href")
    if len(title) < 2:
        raise ApiError("Indiquez un titre.")
    if len(title) > 70:
        raise ApiError("Titre : 70 caractères maximum.")
    if len(txt) < 5:
        raise ApiError("Indiquez un court texte.")
    if len(txt) > 180:
        raise ApiError("Texte : 180 caractères maximum.")
    if href and (len(href) > 300 or not re.fullmatch(r"https://\S+", href)):
        raise ApiError("Le lien doit commencer par https://")
    db.add(Ad(title=title, text=txt, link_label=label or None, href=href or None))
    audit(db, admin.id, "Annonce partenaire créée", title)
    db.commit()
    return {"ok": "Annonce publiée sur l'accueil."}


@router.post("/ads/{ad_id}/toggle")
def toggle_ad(ad_id: str, admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    a = db.get(Ad, ad_id)
    if not a:
        raise not_found()
    a.active = not a.active
    audit(db, admin.id, "Annonce partenaire affichée" if a.active else "Annonce partenaire masquée", a.title)
    db.commit()
    return {"ok": True}


@router.delete("/ads/{ad_id}")
def delete_ad(ad_id: str, admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    a = db.get(Ad, ad_id)
    if not a:
        raise not_found()
    db.delete(a)
    audit(db, admin.id, "Annonce partenaire supprimée", a.title)
    db.commit()
    return {"ok": True}


# ---------- Journal d'audit ----------

@router.get("/journal")
def journal(admin: User = Depends(require_admin()), db: Db = Depends(get_db)):
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).options(joinedload(AuditLog.actor))).all()
    return {"logs": [{"id": l.id, "createdAt": iso(l.created_at), "actor": l.actor.full_name if l.actor else None, "action": l.action, "target": l.target} for l in logs]}  # noqa: E741



@router.get("/reviews")
def admin_reviews(admin: User = Depends(require_admin("MODER", "SUPPORT")), db: Db = Depends(get_db)):
    rows = db.scalars(select(Review).order_by(Review.created_at.desc()).limit(200).options(joinedload(Review.client))).all()
    pros = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.pro_id for r in rows}))).all()} if rows else {}
    return {"reviews": [
        {"id": r.id, "rating": r.rating, "comment": r.comment, "state": r.state, "createdAt": iso(r.created_at),
         "client": r.client.full_name, "pro": pros[r.pro_id].full_name if r.pro_id in pros else "?"}
        for r in rows
    ]}


@router.post("/reviews/{review_id}/{action}")
def admin_review_moderate(review_id: str, action: str, admin: User = Depends(require_admin("MODER", "SUPPORT")), db: Db = Depends(get_db)):
    """Masquer un avis contraire aux règles (injure, faux avis) ou le remettre en ligne."""
    if action not in ("hide", "show"):
        raise not_found()
    r = db.get(Review, review_id)
    if not r:
        raise not_found("Avis introuvable.")
    r.state = "HIDDEN" if action == "hide" else "VISIBLE"
    audit(db, admin.id, "Avis masqué" if action == "hide" else "Avis remis en ligne", r.id)
    db.commit()
    return {"state": r.state}
