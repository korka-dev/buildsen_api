"""Comptes de test pour le développement local : python -m app.devseed

Crée (sans toucher à ceux qui existent) un particulier, deux professionnels vérifiés (un en formule Pro, un en formule Gratuite),
un professionnel au dossier à compléter et un administrateur à activer. Affiche les mots de passe créés.
Refuse de s'exécuter en production ou sur une base qui n'est pas locale : ne JAMAIS l'utiliser sur les vraies données.
"""

import secrets
import sys
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .mailer import app_url
from .models import ProProfile, User
from .security import hash_password, issue_user_token, new_totp_secret
from .utils import now

ACCOUNTS = [
    # téléphone, prénom, nom, rôle, dossier, formule, statut, entreprise
    ("771000001", "Awa", "Diop", "CLIENT", None, "FREE", None, None),
    ("771000002", "Moussa", "Ndiaye", "PRO", "DRAFT", "FREE", None, None),
    ("771000003", "Fatou", "Sarr", "PRO", "VERIFIED", "PRO", "ING", None),
    ("771000004", "Ibrahima", "Fall", "PRO", "VERIFIED", "FREE", "ENT", "Fall BTP Études SARL"),
]
EMAILS = {"771000001": "particulier@buildsen.test", "771000002": "pro.independant@buildsen.test", "771000003": "ingenieur@buildsen.test", "771000004": "entreprise@buildsen.test"}


def local_database() -> bool:
    host = urlsplit(settings.database_url).hostname or ""
    return host in ("localhost", "127.0.0.1", "db")


def main() -> int:
    if settings.production or not local_database():
        print("Refusé : ce script ne s'exécute qu'en développement, sur la base locale.", file=sys.stderr)
        return 1
    db = SessionLocal()
    created = []
    try:
        for phone, first, last, role, dossier, plan, statut, company in ACCOUNTS:
            if db.scalar(select(User.id).where(User.email == EMAILS[phone])):
                continue
            pw = "Test-" + secrets.token_urlsafe(6)
            u = User(email=EMAILS[phone], phone=phone, first_name=first, last_name=last, role=role, password_hash=hash_password(pw), email_verified_at=now())
            db.add(u)
            db.flush()
            if role == "PRO":
                db.add(ProProfile(
                    user_id=u.id, state=dossier, statut=statut, company_name=company, plan=plan,
                    plan_until=now() + timedelta(days=365) if plan != "FREE" else None,
                    school="École supérieure polytechnique de Dakar" if dossier == "VERIFIED" else None,
                    diploma="Ingénieur de conception en génie civil" if dossier == "VERIFIED" else None,
                    specialties=[], commune="Dakar-Plateau" if dossier == "VERIFIED" else None, decided_at=now() if dossier == "VERIFIED" else None,
                ))
            created.append((EMAILS[phone], phone, pw))
        if not db.scalar(select(User.id).where(User.email == "admin@buildsen.test")):
            a = User(email="admin@buildsen.test", first_name="Admin", last_name="BUILDSEN", role="ADMIN", admin_role="SUPER", totp_secret=new_totp_secret(), totp_enabled=False)
            db.add(a)
            db.flush()
            if settings.admin_dev_password:  # administrateur de test déjà activé (mot de passe fixe + code fixe de développement)
                a.password_hash, a.totp_enabled = hash_password(settings.admin_dev_password), True
                link = None
            else:
                link = app_url(f"/admin/activation?jeton={issue_user_token(db, a, 'setup', 72)}")
        else:
            link = None
        db.commit()
    finally:
        db.close()
    if not created and not link:
        print("Les comptes de test existent déjà : rien à créer.")
    for email, phone, pw in created:
        print(f"{phone}  {email}  mot de passe : {pw}")
    if link:
        print(f"Administrateur à activer (72 h, QR code de double authentification) : {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
