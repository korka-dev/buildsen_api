"""Scénarios de démonstration du simulateur : remettent le local dans un état précis pour tester une partie à la fois.

Réservé au développement (le routeur refuse tout hors simulateur). Ne touche qu'aux données locales de test.
"""

from datetime import timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session as Db

from ...models import Conversation, Message, Payment, ProProfile, ServiceRequest, Session, Throttle, User, WaMessage, WaSession
from ...rules import PLANS_ACTIVE
from ...utils import now, phone_local
from .. import actions
from ..alerts import process_due_alerts
from . import notify

CLIENT_NEW, PRO_FREE, PRO_PAID, CLIENT_DEMO = "771000099", "771000004", "771000003", "771000001"
PROJECT = {
    "category": "plans", "buildingType": "Maison individuelle (R+1 ou plus)", "commune": "Ngor", "budget": "600000", "deadline": "Sous 1 mois",
    "confirm": "on",
}
OFFER = "Bonjour, je peux faire vos plans sous trois semaines, voici ma proposition."
SCENARIOS = {
    "depart": "Tout remettre à zéro",
    "nouveau-client": "Un nouveau client arrive sur WhatsApp",
    "limite": "Un professionnel gratuit atteint sa limite",
    "alertes-48h": "Avancer le temps de 48 heures",
    "expirer": "Faire expirer les formules payantes",
    "demo-admin": "Remplir l'administration de données de démonstration",
}


def _user(db: Db, phone: str) -> User | None:
    return next((u for u in db.scalars(select(User).where(User.phone.is_not(None))).all() if phone_local(u.phone) == phone), None)


def _reset_phone(db: Db, phone: str, delete_account: bool = False, lang: str | None = "fr") -> None:
    """Efface la conversation d'un numéro. Les comptes existants gardent le français ; un nouveau client redécouvre le choix de la langue."""
    db.execute(delete(WaMessage).where(WaMessage.phone == phone))
    db.execute(delete(WaSession).where(WaSession.phone == phone))
    db.flush()
    if lang:
        db.add(WaSession(phone=phone, state="NEW", data={"lang": lang}))
    db.execute(delete(Throttle).where(Throttle.key == f"wa-in:{phone}"))
    if delete_account:
        for u in db.scalars(select(User).where(User.email == f"{phone}@wa.buildsen.invalid")).all():
            db.delete(u)


def _clear_limits(db: Db) -> None:
    """Limites de débit remises à zéro (demandes par jour, messages, premiers contacts, paiements)."""
    keys = ("request:%", "msg:%", "start:%", "pay:%")
    db.execute(delete(Throttle).where(or_(*[Throttle.key.like(k) for k in keys])))


def _reset_pros(db: Db) -> None:
    """Formules d'origine : Fatou en Pro pour un an, l'entreprise en Gratuit ; plus aucun paiement ni conversation."""
    # Les données de démonstration de l'administration (app/demodata.py) sont conservées
    db.execute(delete(Payment).where(Payment.token.not_like("demo_%")))
    demo_requests = select(ServiceRequest.id).where(ServiceRequest.ref.like("BS-DEMO-%"))
    db.execute(delete(Conversation).where(Conversation.request_id.not_in(demo_requests)))
    for phone, plan in ((PRO_PAID, "PRO"), (PRO_FREE, "FREE")):
        u = _user(db, phone)
        if u and u.pro:
            u.pro.plan = plan
            u.pro.plan_until = now() + timedelta(days=365) if plan != "FREE" else None


def _publish(db: Db, client: User, n: int) -> list[ServiceRequest]:
    out = []
    for i in range(n):
        desc = f"Je veux une villa avec quatre chambres, un salon et une cuisine fermée, projet numéro {i + 1}."
        r = actions.create_service_request(db, client, {**PROJECT, "description": desc})
        out.append(r)
    return out


def play(db: Db, name: str) -> dict:
    """Prépare le scénario et dit quels numéros mettre dans les deux téléphones et quoi faire ensuite."""
    if name == "depart":
        for phone in (CLIENT_NEW, "771000098", CLIENT_DEMO, PRO_PAID, PRO_FREE, "771000002"):
            _reset_phone(db, phone, delete_account=phone in (CLIENT_NEW, "771000098"), lang=None if phone in (CLIENT_NEW, "771000098") else "fr")
        _clear_limits(db)
        _reset_pros(db)
        db.execute(delete(ServiceRequest).where(ServiceRequest.ref.not_like("BS-DEMO-%")))
        db.execute(delete(Session))
        db.commit()
        return {"client": CLIENT_NEW, "pro": PRO_PAID, "next": (
            "Tout est remis à zéro. Côté client, écrivez « Bonjour » : l'assistant vous accueille et crée votre compte.")}
    if name == "nouveau-client":
        _reset_phone(db, CLIENT_NEW, delete_account=True, lang=None)
        _reset_phone(db, PRO_PAID)
        db.commit()
        return {"client": CLIENT_NEW, "pro": PRO_PAID, "next": (
            "Côté client (à gauche), écrivez « Bonjour ». Le professionnel (à droite, formule Pro) recevra l'alerte dès la publication.")}
    if name == "limite":
        client = _user(db, CLIENT_DEMO)
        pro = _user(db, PRO_FREE)
        if not client or not pro:
            return {"error": "Les comptes de test sont absents : lancez « python -m app.devseed »."}
        _reset_phone(db, CLIENT_DEMO)
        _reset_phone(db, PRO_FREE)
        _reset_pros(db)
        _clear_limits(db)
        reqs = _publish(db, client, 4)
        t = now()
        for r in reqs[:3]:  # trois contacts déjà utilisés ce mois-ci
            c = Conversation(request_id=r.id, pro_id=pro.id, pro_read_at=t, last_message_at=t)
            db.add(c)
            db.flush()
            db.add(Message(conversation_id=c.id, sender_id=pro.id, body=OFFER, created_at=t))
        db.commit()
        store_pro = db.scalar(select(ProProfile).where(ProProfile.user_id == pro.id))
        notify.request_alert(db, reqs[3], [store_pro])  # alerte de la 4e demande, sans attendre les 48 h
        db.commit()
        return {"client": CLIENT_DEMO, "pro": PRO_FREE, "next": (
            "L'entreprise (à droite, formule Gratuite) a déjà utilisé ses 3 contacts du mois. Touchez « Répondre » sous la nouvelle demande : "
            "l'assistant refuse et propose Pro ou Premium. Touchez « Passer à Pro », ouvrez le lien et payez (test)."
        ) if PLANS_ACTIVE else "Les formules sont désactivées (PLANS_ENABLED=false)."}
    if name == "alertes-48h":
        db.execute(update(ServiceRequest).where(ServiceRequest.state == "PUBLISHED", ServiceRequest.free_notified_at.is_(None))
                   .values(published_at=now() - timedelta(hours=49)))
        db.commit()
        sent = process_due_alerts(db, force=True)
        return {"client": None, "pro": None, "next": f"48 heures ont passé : {sent} alerte(s) différée(s) envoyée(s) aux professionnels en formule Gratuite."}
    if name == "expirer":
        for phone in (PRO_PAID,):
            u = _user(db, phone)
            if u and u.pro:
                u.pro.plan_until = now() - timedelta(days=1)
        db.commit()
        return {"client": None, "pro": None, "next": (
            "La formule Pro de Fatou Sarr a expiré : elle repasse en Gratuit (3 contacts, alertes 48 h plus tard). "
            "Essayez « abonnement » sur son téléphone.")}
    if name == "demo-admin":
        from ...demodata import seed

        out = seed(db)
        return {"client": None, "pro": None, "next": (
            f"Données de démonstration prêtes ({out['pros']} professionnel(s) et {out['requests']} demande(s) ajoutés). "
            "Connectez-vous à l'administration (voir COMPTES-DE-TEST.md) : dossiers à tous les stades, demandes, signalements, paiements, annonces, équipe.")}
    return {"error": "Scénario inconnu."}
