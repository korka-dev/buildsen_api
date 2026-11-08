"""Petits utilitaires : identifiants, dates, formats."""

import itertools
import os
import secrets
import time
from datetime import UTC, datetime

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_counter = itertools.count(secrets.randbelow(36**4))


def _b36(n: int) -> str:
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = _ALPHABET[r] + out
        if not n:
            return out


def cuid() -> str:
    """Identifiant au format cuid (« c » + 24 caractères), comme ceux créés par Prisma."""
    ts = _b36(int(time.time() * 1000)).rjust(8, "0")[-8:]
    count = _b36(next(_counter) % 36**4).rjust(4, "0")
    fingerprint = _b36(os.getpid() % 36**4).rjust(4, "0")
    rnd = _b36(secrets.randbits(48)).rjust(8, "0")[-8:]
    return f"c{ts}{count}{fingerprint}{rnd}"


def now() -> datetime:
    """Heure UTC sans fuseau : les colonnes Prisma sont des « timestamp(3) without time zone » en UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso(d: datetime | None) -> str | None:
    """Date au format ISO 8601 en UTC (« …Z »), pour le frontend."""
    if d is None:
        return None
    return d.replace(tzinfo=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fcfa(n: int) -> str:
    """Montant au format « 1 500 000 FCFA » (espaces insécables, pour ne pas couper le montant)."""
    return f"{n:,}".replace(",", " ") + " FCFA"


def add_months(d: datetime, months: int) -> datetime:
    """Ajoute des mois en gardant le jour (ramené au dernier jour du mois si besoin)."""
    import calendar

    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def phone_local(raw: str | None) -> str | None:
    """Numéro sénégalais sur 9 chiffres, sans espaces ni indicatif (« 771234567 »), ou None s'il est invalide."""
    d = "".join(c for c in raw or "" if c.isdigit())
    local = d[3:] if len(d) == 12 and d.startswith("221") else d
    return local if len(local) == 9 else None


def format_phone(raw: str | None) -> str:
    """Numéro sénégalais au format « 77 123 45 67 » (avec « +221 » s'il a été saisi)."""
    if not raw:
        return ""
    d = "".join(c for c in raw if c.isdigit())
    local = d[3:] if len(d) == 12 and d.startswith("221") else d
    if len(local) != 9:
        return raw
    f = f"{local[:2]} {local[2:5]} {local[5:7]} {local[7:]}"
    return f if local == d else f"+221 {f}"
