"""Alerte « nouvelle demande » aux professionnels vérifiés.

Formules Pro et Premium tout de suite, formule gratuite 48 h après la publication.
Tant que les formules sont inactives (PLANS_ACTIVE = False), tout le monde est prévenu tout de suite.
"""

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..emails import dossier_reminder_mail, new_request_pro_mail, request_reminder_mail
from ..mailer import mail_ctx, send_mail, send_mail_batch
from ..models import ProProfile, ServiceRequest, Throttle, User
from ..rules import FREE_ALERT_DELAY_H, MAX_PROS_PER_REQUEST, PLANS_ACTIVE, REMIND_AFTER_H, active_plan, missing_items, pro_sees
from ..utils import now
from .market import request_title
from .queries import conversation_counts
from .wa import notify as wa_notify


def alert_pros(db: Db, r: ServiceRequest, audience: str) -> int:
    pros = db.scalars(
        select(ProProfile).join(User).where(ProProfile.state == "VERIFIED", User.status == "ACTIVE").options(joinedload(ProProfile.user))
    ).all()

    def wanted(p: ProProfile) -> bool:
        paid = not PLANS_ACTIVE or active_plan(p.plan, p.plan_until) != "FREE"
        return (paid if audience == "paid" else not paid) and pro_sees(p, r) and (
            not p.specialties or r.category in p.specialties
        )

    targets = [p for p in pros if wanted(p)]
    ctx, title = mail_ctx(), request_title(r)
    send_mail_batch([
        (p.user.email, new_request_pro_mail(ctx, p.user.first_name, id=r.id, title=title, commune=r.commune, budget=r.budget))
        for p in targets
    ])
    wa_notify.request_alert(db, r, targets)
    return len(targets)


def process_due_alerts(db: Db, force: bool = False) -> int:
    """Envoie les alertes différées arrivées à échéance. Chaque demande n'est traitée qu'une fois (réservation atomique)."""
    t0 = now()
    if not force:
        t = db.get(Throttle, "job:alertes")
        if t and t.reset_at > t0:
            return 0
        if t:
            t.reset_at = t0 + timedelta(minutes=10)
        else:
            db.add(Throttle(key="job:alertes", count=1, reset_at=t0 + timedelta(minutes=10)))
        db.commit()
    due = db.scalars(
        select(ServiceRequest).where(
            ServiceRequest.state == "PUBLISHED", ServiceRequest.free_notified_at.is_(None),
            ServiceRequest.published_at <= t0 - timedelta(hours=FREE_ALERT_DELAY_H),
        ).limit(20)
    ).all()
    sent = 0
    for r in due:
        claimed = db.execute(
            update(ServiceRequest).where(ServiceRequest.id == r.id, ServiceRequest.free_notified_at.is_(None)).values(free_notified_at=now())
        )
        db.commit()
        if claimed.rowcount == 1:
            sent += alert_pros(db, r, "free")
    return sent


def remind_priority_pros(db: Db, limit: int = 200) -> int:
    """Rappel par e-mail aux professionnels Pro et Premium qui n'ont pas encore regardé une demande encore ouverte (3 h après sa publication).

    Une seule fois par demande et par professionnel ; rien si la demande est complète, retirée, ou si le professionnel est déjà en discussion
    avec le client ou a consulté ses demandes depuis. Sans formules actives, aucun rappel (tout le monde est déjà prévenu au même moment).
    """
    if not PLANS_ACTIVE:
        return 0
    t = now()
    reqs = db.scalars(
        select(ServiceRequest).where(
            ServiceRequest.state == "PUBLISHED", ServiceRequest.published_at <= t - timedelta(hours=REMIND_AFTER_H),
            ServiceRequest.published_at > t - timedelta(hours=24),
        ).limit(50)
    ).all()
    if not reqs:
        return 0
    counts = conversation_counts(db, [r.id for r in reqs])
    pros = db.scalars(select(ProProfile).join(User).where(ProProfile.state == "VERIFIED", User.status == "ACTIVE").options(joinedload(ProProfile.user))).all()
    paid = [p for p in pros if active_plan(p.plan, p.plan_until) != "FREE"]
    ctx, sent = mail_ctx(), 0
    for r in reqs:
        taken = counts.get(r.id, 0)
        if taken >= MAX_PROS_PER_REQUEST:
            continue
        mine = {c.pro_id for c in my_conversations_for_request(db, r.id)}
        batch = []
        for p in paid:
            if p.user_id in mine or not pro_sees(p, r) or (p.specialties and r.category not in p.specialties):
                continue
            if p.requests_seen_at and p.requests_seen_at > r.published_at:  # il a déjà consulté ses demandes depuis la publication
                continue
            key = f"remind:{r.id}:{p.user_id}"
            if db.get(Throttle, key):
                continue
            db.add(Throttle(key=key, count=1, reset_at=t + timedelta(days=3)))
            batch.append(p)
        db.commit()
        title, left = request_title(r), MAX_PROS_PER_REQUEST - taken
        send_mail_batch([
            (p.user.email, request_reminder_mail(ctx, p.user.first_name, id=r.id, title=title, commune=r.commune, budget=r.budget, places=left, total=MAX_PROS_PER_REQUEST))
            for p in batch
        ])
        sent += len(batch)
        if sent >= limit:
            break
    return sent


def my_conversations_for_request(db: Db, request_id: str):
    from ..models import Conversation

    return db.scalars(select(Conversation).where(Conversation.request_id == request_id)).all()


def remind_dossiers(db: Db, limit: int = 100) -> int:
    """Professionnels inscrits dont le dossier n'est pas soumis : un rappel après 1 jour, un second après 4 jours."""
    sent = 0
    t = now()
    rows = db.scalars(
        select(ProProfile).join(User).where(ProProfile.state == "DRAFT", User.status == "ACTIVE", User.created_at < t - timedelta(days=1))
        .options(joinedload(ProProfile.user), joinedload(ProProfile.credentials))
    ).unique().all()
    for p in rows:
        stage = "2" if p.user.created_at < t - timedelta(days=4) else "1"
        key = f"dossier:{p.user_id}:{stage}"
        if db.get(Throttle, key) or p.user.created_at < t - timedelta(days=30):
            continue
        db.add(Throttle(key=key, count=1, reset_at=t + timedelta(days=60)))
        db.commit()
        send_mail(p.user.email, dossier_reminder_mail(mail_ctx(), p.user.first_name, missing_items(p), info_done=bool(p.statut)))
        sent += 1
        if sent >= limit:
            break
    return sent
