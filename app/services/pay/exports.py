"""Import de l'export « Transactions » de Wave Business ou d'Orange Money : confirme d'un coup les paiements en attente retrouvés."""

import csv
import io
import re

from sqlalchemy.orm import Session as Db

from ...errors import ApiError
from ...models import User
from ...utils import phone_local
from .manual import confirm_external
from .service import MANUAL

_PHONE = re.compile(r"(?:\+?221[\s.-]?)?(7[0-8])[\s.-]?(\d{3})[\s.-]?(\d{2})[\s.-]?(\d{2})")
MAX_IMPORT_ROWS = 5000


def _amount(raw: str) -> int | None:
    v = re.sub(r"[^\d,.\-]", "", raw or "")
    if not v or v.startswith("-"):
        return None  # les sorties d'argent ne sont pas des paiements reçus
    v = v.split(",")[0] if "," in v and "." not in v else v
    try:
        n = int(float(v.replace(",", "")))
    except ValueError:
        return None
    return n if n > 0 else None


def import_export(db: Db, text: str, provider: str = "wave") -> dict:
    """Confirme d'un coup les paiements Wave en attente retrouvés dans l'export des transactions (CSV téléchargé depuis Wave Business).

    Une ligne règle un paiement seulement si le montant correspond et que l'identifiant de transaction donné par le professionnel, ou son numéro Wave,
    la désigne sans ambiguïté. Une transaction déjà utilisée n'est jamais comptée deux fois.
    """
    if provider not in MANUAL:
        raise ApiError("Moyen de paiement inconnu.")
    text = text.lstrip("\ufeff")[:2_000_000]
    first = text.splitlines()[0] if text.strip() else ""
    delim = max([",", ";", "\t"], key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))[: MAX_IMPORT_ROWS + 1]
    if len(rows) < 2:
        raise ApiError("Fichier vide ou illisible. Utilisez le CSV téléchargé depuis Wave Business > Transactions.")
    head = [h.strip().lower() for h in rows[0]]

    def col(*names: str) -> int | None:
        for i, h in enumerate(head):
            if any(n in h for n in names):
                return i
        return None

    c_amount, c_id = col("montant", "amount"), col("identifiant", "transaction id", "id de transaction", "transaction_id")
    if c_amount is None:
        raise ApiError("Colonne « Montant » introuvable dans le fichier.")
    done: list[dict] = []
    unmatched = ignored = 0
    for r in rows[1:]:
        if not any(r):
            continue
        amount = _amount(r[c_amount]) if c_amount < len(r) else None
        if not amount:
            ignored += 1
            continue
        ref = (r[c_id].strip() if c_id is not None and c_id < len(r) else "") or None
        pay = None
        phones = {phone_local("".join(m.groups())) for m in _PHONE.finditer(" ".join(r))}
        for ph in [*phones, None]:
            pay = confirm_external(db, amount, ph, ref, window_h=24 * 7, provider=provider)
            if pay:
                break
        if pay:
            u = db.get(User, pay.user_id)
            done.append({"id": pay.id, "who": u.full_name, "amount": pay.amount, "plan": pay.plan})
        else:
            unmatched += 1
    return {"confirmed": done, "unmatched": unmatched, "ignored": ignored}
