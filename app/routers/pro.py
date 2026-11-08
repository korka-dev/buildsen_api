"""Espace professionnel : dossier de vérification, profil, demandes des particuliers et premier message."""


from fastapi import APIRouter, BackgroundTasks, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..audit import audit
from ..db import get_db
from ..deps import require_pro
from ..emails import dossier_submitted_mail
from ..errors import ApiError, not_found
from ..mailer import app_url, mail_ctx, notify_team, send_mail
from ..models import Conversation, ProProfile, ServiceRequest, User
from ..rules import (
    CATEGORIES,
    COMMUNES,
    MAX_PROS_PER_REQUEST,
    PHONE_RE,
    REVIEW_DELAY,
    SPECIALTIES,
    STATUTS,
    all_docs,
    big_locked,
    can_do_studies,
    dossier_editable,
    dossier_next,
    missing_items,
    pro_can_see,
    pro_sees,
)
from ..serializers import conversation_summary, pro_private_out, request_out, user_out
from ..services.actions import notify_chat, verified_pro
from ..services.actions import start_conversation as start_chat
from ..services.pay.service import month_start
from ..services.queries import conversation_counts, last_messages, my_conversations
from ..storage import remove_object
from ..tasks import with_db
from ..utils import now
from ..validators import checked, experience_years, text

router = APIRouter(prefix="/api/pro", tags=["Espace professionnel"])


def _pro(db: Db, user: User) -> ProProfile:
    p = db.scalar(select(ProProfile).where(ProProfile.user_id == user.id))
    if not p:
        raise not_found("Profil introuvable.")
    return p


@router.get("/profile")
def profile(user: User = Depends(require_pro), db: Db = Depends(get_db)):
    p = _pro(db, user)
    return {"user": user_out(user), "pro": pro_private_out(p, with_files=True), "next": dossier_next(p)}


@router.put("/dossier")
def save_dossier(body: dict = Body(...), user: User = Depends(require_pro), db: Db = Depends(get_db)):
    """Partie 1 du dossier. Enregistrement partiel accepté : chaque champ rempli est gardé, les champs vides ne bloquent rien."""
    p = _pro(db, user)
    if not dossier_editable(p.state):
        raise ApiError("Votre dossier est en cours d'examen ou déjà validé.", 409)
    statut = text(body, "statut") or None
    if statut is not None and statut not in STATUTS:
        raise ApiError("Statut invalide.")
    years = experience_years(body.get("experienceYears"))
    # Changement de statut : les justificatifs qui ne le concernent plus sont retirés
    if p.statut and statut and p.statut != statut:
        keep = all_docs(statut)
        for c in [c for c in p.credentials if c.type not in keep]:
            remove_object("justificatifs", c.path)
            db.delete(c)
    if statut:
        p.statut = statut
    for field, key, max_len in (("school", "school", 120), ("diploma", "diploma", 160)):
        v = text(body, key)[:max_len]
        if v:
            setattr(p, field, v)
    if years is not None:
        p.experience_years = years
    company = text(body, "companyName")[:120]
    if (statut or p.statut) == "ENT":
        if company:
            p.company_name = company
    elif statut:
        p.company_name = None
    audit(db, user.id, "Informations du dossier enregistrées")
    db.commit()
    return {"ok": "Informations enregistrées. Envoyez maintenant vos justificatifs juste en dessous (2. Vos justificatifs)."}


@router.delete("/credentials/{credential_id}")
def delete_credential(credential_id: str, user: User = Depends(require_pro), db: Db = Depends(get_db)):
    p = _pro(db, user)
    c = next((x for x in p.credentials if x.id == credential_id), None)
    if not c or not dossier_editable(p.state):
        raise not_found("Justificatif introuvable.")
    remove_object("justificatifs", c.path)
    db.delete(c)
    db.commit()
    return {"ok": True}


def _after_submit(db: Db, pro_id: str, missing: list[str]) -> None:
    p = db.get(ProProfile, pro_id)
    u = p.user
    who = f"{p.company_name}, représentée par " if p.company_name else ""
    statut = STATUTS[p.statut]["name"] if p.statut else "statut non choisi"
    detail = f"\nIl manque : {', '.join(missing)}." if missing else " Dossier complet."
    notify_team(db, "Nouveau dossier professionnel à vérifier",
                f"{who}{u.full_name} ({statut}) a soumis son dossier.{detail}\n\n{app_url(f'/admin/dossiers/{p.id}')}", ("VERIF",))
    send_mail(u.email, dossier_submitted_mail(mail_ctx(), u.first_name))


@router.post("/dossier/submit")
def submit_dossier(background: BackgroundTasks, user: User = Depends(require_pro), db: Db = Depends(get_db)):
    """Soumission possible même incomplète : l'équipe examine et demande un complément si nécessaire."""
    p = _pro(db, user)
    if not dossier_editable(p.state):
        raise ApiError("Dossier déjà transmis.", 409)
    missing = missing_items(p)
    p.state, p.submitted_at, p.decision_reason = "PENDING", now(), None
    audit(db, user.id, "Dossier soumis pour vérification", user.full_name)
    db.commit()
    background.add_task(with_db, _after_submit, p.id, missing)
    extra = f" Il manque encore : {', '.join(missing)} ; l'équipe vous dira si un complément est nécessaire." if missing else ""
    return {"ok": f"Dossier transmis. Il sera examiné sous {REVIEW_DELAY} et vous serez prévenu par e-mail de la décision.{extra}"}


@router.put("/profile")
def save_profile(body: dict = Body(...), user: User = Depends(require_pro), db: Db = Depends(get_db)):
    import re

    p = _pro(db, user)
    bio = text(body, "bio")
    if len(bio) > 800:
        raise ApiError("Présentation : 800 caractères maximum.")
    commune = text(body, "commune")
    if commune and commune not in COMMUNES:
        raise ApiError("Commune invalide.")
    whatsapp = text(body, "whatsapp")
    if whatsapp and not re.match(PHONE_RE, whatsapp):
        raise ApiError("Numéro WhatsApp sénégalais attendu.")
    raw = body.get("specialties") or []
    specialties = [s for s in (raw if isinstance(raw, list) else [raw]) if isinstance(s, str) and s in SPECIALTIES]
    if "etudes" in specialties and not can_do_studies(p.statut):
        raise ApiError("Les études techniques sont réservées aux ingénieurs en génie civil et aux entreprises.")
    p.bio, p.software = bio or None, text(body, "software")[:200] or None
    p.commune, p.whatsapp = commune or None, re.sub(r"\s", "", whatsapp) or None
    p.specialties, p.visible = specialties, checked(body, "visible")
    audit(db, user.id, "Profil modifié")
    db.commit()
    return {"ok": "Profil enregistré."}


@router.delete("/portfolio/{item_id}")
def delete_portfolio(item_id: str, user: User = Depends(require_pro), db: Db = Depends(get_db)):
    p = _pro(db, user)
    it = next((x for x in p.portfolio if x.id == item_id), None)
    if not it:
        raise not_found("Réalisation introuvable.")
    remove_object("portfolios", it.path)
    db.delete(it)
    db.commit()
    return {"ok": True}


def _verified(db: Db, user: User) -> ProProfile:
    return verified_pro(db, user)


@router.get("/requests")
def requests(tout: bool = False, user: User = Depends(require_pro), db: Db = Depends(get_db)):
    """Demandes en ligne visibles selon le statut ; filtrées par spécialités sauf ?tout=1. Le point vert « Demandes » disparaît."""
    p = _verified(db, user)
    reqs = db.scalars(select(ServiceRequest).where(ServiceRequest.state == "PUBLISHED").order_by(ServiceRequest.published_at.desc())).all()
    seen_by_status = [r for r in reqs if pro_can_see(p.statut, r.category, r.levels)]
    visible = [r for r in seen_by_status if not big_locked(p, r.budget)]
    p.requests_seen_at = now()
    db.commit()
    mine = bool(p.specialties) and not tout
    listed = [r for r in visible if r.category in p.specialties] if mine else visible
    counts = conversation_counts(db, [r.id for r in listed])
    wrote = my_conversations(db, user.id, [r.id for r in listed])
    return {
        "filtered": mine,
        "hasSpecialties": bool(p.specialties),
        "stats": _zone_stats(db, p, seen_by_status),
        "requests": [
            {**request_out(r, conversations=counts.get(r.id, 0)), "categoryName": CATEGORIES[r.category]["name"], "myConversationId": wrote.get(r.id)}
            for r in listed
        ],
    }


def _zone_stats(db: Db, p: ProProfile, reqs: list[ServiceRequest]) -> dict:
    """Chiffres du mois pour tous les professionnels : demandes dans leur zone, déjà complètes, et gros budgets réservés aux abonnés."""
    start = month_start()
    month = [r for r in reqs if r.published_at and r.published_at >= start and (not p.commune or r.commune == p.commune)]
    counts = conversation_counts(db, [r.id for r in month])
    return {
        "commune": p.commune, "monthZone": len(month), "full": sum(1 for r in month if counts.get(r.id, 0) >= MAX_PROS_PER_REQUEST),
        "bigLocked": sum(1 for r in reqs if big_locked(p, r.budget)),
    }


@router.get("/requests/{request_id}")
def request_detail(request_id: str, user: User = Depends(require_pro), db: Db = Depends(get_db)):
    p = _verified(db, user)
    r = db.get(ServiceRequest, request_id)
    mine = my_conversations(db, user.id, [request_id]).get(request_id) if r else None
    # Visible si publiée (ou si le pro y a déjà une conversation) et si son statut le permet
    if not r or not pro_sees(p, r) or (r.state != "PUBLISHED" and not mine):
        raise not_found("Demande introuvable.")
    n = conversation_counts(db, [r.id]).get(r.id, 0)
    return {"request": request_out(r, conversations=n), "myConversationId": mine, "full": n >= MAX_PROS_PER_REQUEST}


@router.post("/requests/{request_id}/start")
def start_conversation(request_id: str, background: BackgroundTasks, body: dict = Body(...), user: User = Depends(require_pro), db: Db = Depends(get_db)):
    """Premier message d'un professionnel vérifié : ouvre la conversation (5 professionnels au maximum par demande)."""
    conv_id, created = start_chat(db, user, request_id, text(body, "body"))
    if created:
        background.add_task(with_db, notify_chat, conv_id, True, True)
    return {"redirect": f"/pro/messages/{conv_id}"}


@router.get("/conversations")
def conversations(user: User = Depends(require_pro), db: Db = Depends(get_db)):
    convs = db.scalars(
        select(Conversation).where(Conversation.pro_id == user.id).order_by(Conversation.last_message_at.desc())
        .options(joinedload(Conversation.request).joinedload(ServiceRequest.client), joinedload(Conversation.pro).joinedload(User.pro))
    ).all()
    last = last_messages(db, [c.id for c in convs])
    return {"conversations": [conversation_summary(c, "pro", last.get(c.id)) for c in convs]}

