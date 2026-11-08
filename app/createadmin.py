"""Crée le super-administrateur d'une base neuve (production comprise) : python -m app.createadmin email prénom nom

N'écrit rien si un compte avec cet e-mail existe. Affiche un lien d'activation valable 72 h (choix du mot de passe + double authentification).
"""

import sys

from sqlalchemy import select

from .db import SessionLocal
from .mailer import app_url
from .models import User
from .security import issue_user_token, new_totp_secret


def main(argv: list[str]) -> int:
    if len(argv) != 3 or "@" not in argv[0]:
        print("Usage : python -m app.createadmin email prénom nom", file=sys.stderr)
        return 2
    email, first, last = argv[0].strip().lower(), argv[1], argv[2]
    db = SessionLocal()
    try:
        if db.scalar(select(User.id).where(User.email == email)):
            print("Un compte existe déjà avec cet e-mail : rien à créer.", file=sys.stderr)
            return 1
        u = User(email=email, first_name=first, last_name=last, role="ADMIN", admin_role="SUPER", totp_secret=new_totp_secret())
        db.add(u)
        db.flush()
        link = app_url(f"/admin/activation?jeton={issue_user_token(db, u, 'setup', 72)}")
        db.commit()
    finally:
        db.close()
    print(f"Administrateur à activer (72 h) : {link}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
