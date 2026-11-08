"""Données de démonstration pour parcourir toute l'administration : python -m app.demodata

Crée (sans doublon : on peut le relancer) des professionnels à tous les stades du dossier, des particuliers, des demandes publiées, clôturées,
retirées et signalées, des conversations, des signalements, des paiements, des annonces partenaires, des messages WhatsApp et une équipe d'administration.
Les adresses finissent par « @demo.buildsen.test » : connexion en un clic possible depuis /simulateur. Refuse de s'exécuter en production ou sur une base distante.
"""

import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from .audit import audit
from .config import settings
from .db import SessionLocal
from .models import Ad, AuditLog, Conversation, Credential, Message, Payment, ProProfile, Report, ServiceRequest, User, WaMessage, WaSession
from .security import hash_password, new_totp_secret
from .utils import cuid, now

D = "@demo.buildsen.test"
# (téléphone, prénom, nom, rôle, état du dossier, statut, entreprise, formule, mois de formule, vedette, mise en avant, motif)
PROS = [
    ("771010001", "Cheikh", "Gaye", "DRAFT", "TECH", None, "FREE", 0, False, False, None),
    ("771010002", "Mame Diarra", "Sow", "PENDING", "ING", None, "FREE", 0, False, False, None),
    ("771010003", "Ousmane", "Ba", "MORE", "TECH", None, "FREE", 0, False, False, "La pièce d'identité est illisible : merci d'envoyer une photo plus nette."),
    ("771010004", "Khadija", "Thiam", "VERIFIED", "ING", None, "PREMIUM", 3, True, True, None),
    ("771010005", "Seydou", "Mbaye", "VERIFIED", "ENT", "Sahel Construction SARL", "PRO", 2, True, False, None),
    ("771010006", "Pape", "Ndoye", "REFUSED", "TECH", None, "FREE", 0, False, False, "Le diplôme fourni ne correspond pas au statut demandé."),
    ("771010007", "Aïssatou", "Cissé", "VERIFIED", "TECH", None, "FREE", 0, False, False, None),
]
CLIENTS = [("771010101", "Mariama", "Ba", "ACTIVE"), ("771010102", "Modou", "Sarr", "ACTIVE"), ("771010103", "Fatima", "Kane", "ACTIVE"), ("771010104", "Binta", "Faye", "SUSPENDED")]
# (réf. courte, client, catégorie, bâtiment, commune, budget, délai, état, motif, description)
REQUESTS = [
    ("D1", 0, "plans", "Maison individuelle (R+1 ou plus)", "Ngor", 800_000, "Sous 1 mois", "PUBLISHED", None, "Villa R+1, 4 chambres, salon, cuisine fermée et terrasse à l'étage."),
    ("D2", 1, "metres", "Immeuble d'habitation", "Parcelles Assainies", 450_000, "Sous 2 semaines", "PUBLISHED", None, "Devis quantitatif pour un immeuble de 3 niveaux, 6 appartements."),
    ("D3", 2, "etudes", "Maison individuelle (R+1 ou plus)", "Ouakam", 1_200_000, "Dès que possible", "PUBLISHED", None, "Étude de structure et plans de ferraillage d'une villa R+2."),
    ("D4", 0, "dao", "Local commercial ou bureaux", "Dakar-Plateau", 250_000, "Sous 1 mois", "PUBLISHED", None, "Mise au propre numérique des plans d'un local commercial de 120 m²."),
    ("D5", 1, "plans", "Extension ou surélévation", "Pikine", 600_000, "Sous 2 mois", "PUBLISHED", None, "Surélévation d'un étage sur une maison existante, plans et autorisation."),
    ("D6", 2, "plans", "Aménagement intérieur", "Médina", 350_000, "Pas de contrainte", "PUBLISHED", None, "Plans d'aménagement intérieur d'un appartement de 4 pièces."),
    ("D7", 0, "metres", "Maison individuelle (plain-pied)", "Rufisque", 300_000, "Sous 1 mois", "CLOSED", None, "Métré et estimation du coût d'une maison plain-pied, 5 pièces."),
    ("D8", 1, "plans", "Maison individuelle (plain-pied)", "Guédiawaye", 500_000, "Sous 1 mois", "CLOSED", None, "Plans d'une maison plain-pied de 150 m² sur un terrain de 10 m par 20."),
    ("D9", 2, "plans", "Autre", "Yoff", 90_000, "Dès que possible", "REJECTED", "Coordonnées personnelles dans la description : contraire aux règles.", "Appelez-moi au 77 000 00 00 pour faire mes plans, je paie cash."),
    ("D10", 0, "plans", "Maison individuelle (R+1 ou plus)", "Keur Massar", 700_000, "Sous 1 mois", "PENDING", None, "Plans d'une villa R+1 sur un terrain acheté récemment."),
]


def _user(db: Db, phone: str, first: str, last: str, role: str, status: str = "ACTIVE") -> User:
    u = db.scalar(select(User).where(User.email == f"{phone}{D}"))
    if not u:
        u = User(email=f"{phone}{D}", phone=phone, first_name=first, last_name=last, role=role, status=status, email_verified_at=now() - timedelta(days=30))
        db.add(u)
        db.flush()
    return u


def seed(db: Db) -> dict:
    t = now()
    out = {"pros": 0, "requests": 0}
    admin = db.scalar(select(User).where(User.role == "ADMIN", User.admin_role == "SUPER"))
    pros: dict[str, User] = {}
    for i, (phone, first, last, state, statut, company, plan, months, featured, spot, reason) in enumerate(PROS):
        u = _user(db, phone, first, last, "PRO")
        if u.pro:
            pros[phone] = u
            continue
        verified = state == "VERIFIED"
        db.add(ProProfile(
            user_id=u.id, state=state, statut=statut, company_name=company, plan=plan, plan_until=t + timedelta(days=30 * months) if months else None,
            featured=featured, spotlight=spot, school="École supérieure polytechnique de Dakar" if state != "DRAFT" else None,
            diploma="Diplôme de génie civil" if state != "DRAFT" else None, experience_years=3 + i * 2, commune="Dakar-Plateau",
            bio="Bureau d'études : plans, métrés, suivi de chantier." if verified else None, software="AutoCAD, Revit" if verified else None,
            specialties=["plans", "metres"] if verified else [], decision_reason=reason, visible=True,
            submitted_at=t - timedelta(days=2 + i) if state in ("PENDING", "MORE", "REFUSED", "VERIFIED") else None,
            decided_at=t - timedelta(days=1 + i) if state in ("MORE", "REFUSED", "VERIFIED") else None,
        ))
        db.flush()
        if state != "DRAFT":
            p = db.scalar(select(ProProfile).where(ProProfile.user_id == u.id))
            for doc in (("ID", "ACCEPTED" if verified else "RECEIVED"), ("DIPLOME", "ACCEPTED" if verified else "REFUSED" if state == "MORE" else "RECEIVED")):
                db.add(Credential(pro_id=p.id, type=doc[0], path=f"demo/{cuid()}.pdf", file_name=f"{doc[0].lower()}-{last.lower()}.pdf", mime="application/pdf", size=120_000, state=doc[1],
                                  reason="Photo floue" if doc[1] == "REFUSED" else None))
        pros[phone] = u
        out["pros"] += 1
    clients = [_user(db, ph, f, n, "CLIENT", st) for ph, f, n, st in CLIENTS]
    awa = db.scalar(select(User).where(User.phone == "771000001"))
    owners = [awa or clients[0], clients[1], clients[2]]
    reqs: dict[str, ServiceRequest] = {}
    for k, (short, ci, cat, bt, commune, budget, deadline, state, reject, desc) in enumerate(REQUESTS):
        ref = f"BS-DEMO-{short}"
        r = db.scalar(select(ServiceRequest).where(ServiceRequest.ref == ref))
        if not r:
            from .rules import CATEGORIES

            r = ServiceRequest(
                ref=ref, client_id=owners[ci].id, category=cat, service=CATEGORIES[cat]["name"], building_type=bt, description=desc, commune=commune,
                land_status="Non précisé", phase="Non précisé", deadline=deadline, budget=budget, state=state, reject_reason=reject,
                published_at=t - timedelta(days=1 + k) if state in ("PUBLISHED", "CLOSED") else None,
                closed_at=t - timedelta(hours=6) if state == "CLOSED" else None, created_at=t - timedelta(days=1 + k, hours=2),
            )
            db.add(r)
            db.flush()
            out["requests"] += 1
        reqs[short] = r
    # Conversations : (demande, pro, messages, partagée)
    talks = [
        ("D1", "771010004", ["Bonjour, je peux réaliser vos plans en 3 semaines pour 450 000 FCFA, voici mes références.", "Merci, pouvez-vous me montrer un exemple de villa R+1 ?"], True),
        ("D1", "771010005", ["Bonjour, notre bureau propose plans et suivi de chantier. Devis gratuit sous 48 h."], False),
        ("D2", "771010004", ["Bonjour, je réalise des devis quantitatifs détaillés. Délai : 10 jours."], False),
        ("D3", "771010004", ["Bonjour, ingénieure en génie civil, je prends en charge l'étude de structure complète."], False),
        ("D7", "771010007", ["Bonjour, je peux établir le métré et l'estimation cette semaine."], True),
        ("D8", "771010005", ["Bonjour, nous pouvons produire les plans sous 15 jours."], False),
    ]
    convs: dict[tuple[str, str], Conversation] = {}
    for short, phone, texts, shared in talks:
        r, pro = reqs[short], pros[phone]
        c = db.scalar(select(Conversation).where(Conversation.request_id == r.id, Conversation.pro_id == pro.id))
        if not c:
            c = Conversation(request_id=r.id, pro_id=pro.id, shared=shared, shared_at=t if shared else None, pro_read_at=t, last_message_at=t - timedelta(hours=3))
            db.add(c)
            db.flush()
            for j, body in enumerate(texts):
                sender = pro.id if j % 2 == 0 else r.client_id
                db.add(Message(conversation_id=c.id, sender_id=sender, body=body, created_at=t - timedelta(hours=5 - j)))
        convs[(short, phone)] = c
    # Signalements (un seul passage : seulement s'il n'y en a pas déjà de démonstration)
    if not db.scalar(select(Report.id).where(Report.reason.like("[démo]%"))):
        db.add(Report(reporter_id=reqs["D1"].client_id, conversation_id=convs[("D1", "771010005")].id, reason="[démo] Le professionnel demande un paiement avant tout devis."))
        db.add(Report(reporter_id=pros["771010004"].id, request_id=reqs["D4"].id, reason="[démo] Demande suspecte : budget irréaliste et contact hors plateforme."))
        db.add(Report(reporter_id=reqs["D2"].client_id, conversation_id=convs[("D2", "771010004")].id, reason="[démo] Message insistant, hors sujet."))
        db.add(Report(reporter_id=reqs["D3"].client_id, conversation_id=convs[("D3", "771010004")].id, reason="[démo] Problème réglé avec le professionnel.", state="CLOSED", handled_at=t))
    # Paiements de formules
    if not db.scalar(select(Payment.id).where(Payment.token.like("demo_%"))):
        for tok, phone, plan, months, amount, state in (
            ("demo_1", "771010004", "PREMIUM", 3, 45_000, "PAID"), ("demo_2", "771010005", "PRO", 2, 10_000, "PAID"),
            ("demo_3", "771010007", "PRO", 1, 5_000, "PENDING"), ("demo_4", "771010001", "PRO", 1, 5_000, "FAILED"),
        ):
            db.add(Payment(user_id=pros[phone].id, plan=plan, months=months, amount=amount, provider="sim", token=tok, state=state, source="wa" if tok == "demo_2" else "web",
                           paid_at=t - timedelta(days=2) if state == "PAID" else None, created_at=t - timedelta(days=3)))
    # Annonces partenaires
    if not db.scalar(select(Ad.id).where(Ad.title.like("[démo]%"))):
        db.add(Ad(title="[démo] Ciment Sococim : -5 % sur vos premières commandes", text="Offre réservée aux membres BUILDSEN, valable jusqu'à la fin du mois.", link_label="Voir l'offre", href="https://example.com/ciment", active=True))
        db.add(Ad(title="[démo] Location de matériel de chantier", text="Bétonnières, échafaudages et engins, livrés sur chantier à Dakar.", link_label="Demander un devis", href="https://example.com/materiel", active=True))
        db.add(Ad(title="[démo] Ancienne annonce (désactivée)", text="Cette annonce n'est plus affichée sur l'accueil.", active=False))
    # Messages WhatsApp (journal de l'administration)
    if not db.scalar(select(WaMessage.id).where(WaMessage.id.like("demo_wa_%"))):
        rows = [("771010101", "IN", "text", "Bonjour"), ("771010101", "OUT", "buttons", "Choisissez votre langue"), ("771010101", "IN", "button", "[bouton] Français"),
                ("771010101", "OUT", "buttons", "Bienvenue sur la plateforme BUILDSEN !"), ("771010102", "IN", "audio", "[note vocale]"),
                ("771010102", "OUT", "text", "Voici votre demande : Plans et conception à Ngor."), ("771010004", "OUT", "template", "Nouvelle demande à Ngor : Plans et conception."),
                ("771010005", "OUT", "template", "Nouvelle demande à Parcelles Assainies : Devis et métrés.")]
        seen: set[str] = set()
        for i, (phone, direction, kind, body) in enumerate(rows):
            db.add(WaMessage(id=f"demo_wa_{i}", phone=phone, direction=direction, kind=kind, body=body, status="delivered", created_at=t - timedelta(minutes=40 - i * 3)))
            if phone not in seen and not db.get(WaSession, phone):
                db.add(WaSession(phone=phone, state="MENU", data={"lang": "fr"}))
            seen.add(phone)
    # Équipe d'administration (un actif, un invité qui n'a pas encore activé son accès)
    for email, first, last, role, active in (("verif@demo.buildsen.test", "Aminata", "Fall", "VERIF", True), ("moder@demo.buildsen.test", "Ibou", "Diop", "MODER", False)):
        if not db.scalar(select(User.id).where(User.email == email)):
            db.add(User(email=email, first_name=first, last_name=last, role="ADMIN", admin_role=role, totp_secret=new_totp_secret(), totp_enabled=active,
                        password_hash=hash_password("Demo-" + cuid()[:10]) if active else None))
    # Journal d'audit
    if admin and not db.scalar(select(AuditLog.id).where(AuditLog.target == "démo")):
        for action in ("Dossier vérifié", "Demande retirée", "Signalement traité", "Formule Premium attribuée", "Compte suspendu"):
            audit(db, admin.id, action, "démo")
    db.commit()
    return out


def main() -> int:
    if settings.production or not settings.local_database:
        print("Refusé : ce script ne s'exécute qu'en développement, sur la base locale.", file=sys.stderr)
        return 1
    db = SessionLocal()
    try:
        out = seed(db)
    finally:
        db.close()
    print(f"Données de démonstration prêtes : {out['pros']} professionnel(s) et {out['requests']} demande(s) ajoutés (rien de dupliqué).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
