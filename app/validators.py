"""Validation des formulaires, avec les mêmes messages que la version Next.js.

Les corps de requête sont reçus comme des dictionnaires souples : chaque règle produit un message clair en français
plutôt qu'une erreur technique.
"""

import re

from .errors import ApiError
from .rules import BUILDING_TYPES, CATEGORIES, COMMUNES, DEADLINES, LEVELS, MAX_BUDGET, MIN_BUDGET, PHASES, PHONE_RE, TERRAIN_STATUS
from .utils import phone_local

NOT_SPECIFIED = "Non précisé"

DIGITS = re.compile(r"[0-9]+")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(value: str) -> str:
    """Retire les caractères de contrôle (dont le caractère nul, que PostgreSQL refuse) en gardant les retours à la ligne et les tabulations."""
    return _CONTROL.sub("", value)


def text(body: dict, key: str) -> str:
    v = body.get(key)
    return "" if v is None else clean(str(v)).strip()


def opt_text(body: dict, key: str, max_len: int) -> str | None:
    v = text(body, key)
    if len(v) > max_len:
        raise ApiError(f"{key} : {max_len} caractères maximum.")
    return v or None


def checked(body: dict, key: str) -> bool:
    return body.get(key) in (True, "on", "true", "1", 1)


def email(body: dict, key: str = "email", message: str = "Adresse e-mail invalide.") -> str:
    v = text(body, key).lower()
    if not EMAIL_RE.match(v) or len(v) > 160:
        raise ApiError(message)
    return v


def phone(body: dict, key: str = "phone", message: str = "Numéro sénégalais attendu, par exemple 77 123 45 67.") -> str:
    v = text(body, key)
    if not re.match(PHONE_RE, v):
        raise ApiError(message)
    # Enregistré toujours au même format : c'est l'identifiant de connexion
    return phone_local(v) or re.sub(r"\s", "", v)


def name(body: dict, key: str, message: str) -> str:
    v = text(body, key)
    if not v:
        raise ApiError(message)
    return v[:60]


def _opt_int(raw: str, lo: int, hi: int, message: str) -> int | None:
    if not raw:
        return None
    s = re.sub(r"\s", "", raw)
    # Chiffres 0-9 seulement et longueur bornée : « ² », « ① » ou une suite de milliers de chiffres feraient échouer int()
    if not DIGITS.fullmatch(s) or len(s) > 12 or not lo <= int(s) <= hi:
        raise ApiError(message)
    return int(s)


def service_request(body: dict) -> dict:
    """Formulaire « Décrire mon projet »."""
    category = text(body, "category")
    if category not in CATEGORIES:
        raise ApiError("Choisissez une prestation.")
    # Prestation précise facultative : à défaut, le nom de la prestation (colonne obligatoire en base)
    service = text(body, "service") or CATEGORIES[category]["name"]
    if service != CATEGORIES[category]["name"] and service not in CATEGORIES[category]["items"]:
        raise ApiError("Précisez la prestation.")
    building_type = text(body, "buildingType")
    if building_type not in BUILDING_TYPES:
        raise ApiError("Choisissez le type de bâtiment.")
    description = text(body, "description")
    if len(description) < 20:
        raise ApiError("Décrivez votre besoin (20 caractères minimum).")
    if len(description) > 2000:
        raise ApiError("Description : 2 000 caractères maximum.")
    commune = text(body, "commune")
    if commune not in COMMUNES:
        raise ApiError("Indiquez la commune.")
    # Statut du terrain et avancement facultatifs (formulaire simplifié et assistant vocal)
    land_status = text(body, "landStatus") or NOT_SPECIFIED
    if land_status != NOT_SPECIFIED and land_status not in TERRAIN_STATUS:
        raise ApiError("Statut du terrain invalide.")
    phase = text(body, "phase") or NOT_SPECIFIED
    if phase != NOT_SPECIFIED and phase not in PHASES:
        raise ApiError("Avancement du projet invalide.")
    levels = text(body, "levels") or None
    if levels is not None and levels not in LEVELS:
        raise ApiError("Nombre de niveaux invalide.")
    deadline = text(body, "deadline")
    if deadline not in DEADLINES:
        raise ApiError("Choisissez un délai.")
    raw_budget = re.sub(r"[\s.  ]", "", text(body, "budget"))
    raw_budget = re.sub(r"fcfa$", "", raw_budget, flags=re.I)
    if not raw_budget:
        raise ApiError("Indiquez votre budget : il aide les professionnels à vous répondre.")
    if not DIGITS.fullmatch(raw_budget) or len(raw_budget) > 15:
        raise ApiError("Budget : montant en chiffres uniquement.")
    budget = int(raw_budget)
    if budget < MIN_BUDGET:
        raise ApiError("Le budget semble trop faible. Vérifiez votre saisie.")
    if budget > MAX_BUDGET:
        raise ApiError("Le budget semble trop élevé. Vérifiez votre saisie.")
    if not checked(body, "confirm"):
        raise ApiError("Cochez la confirmation pour envoyer votre demande.")
    return {
        "category": category, "service": service, "building_type": building_type, "description": description,
        "commune": commune, "district": opt_text(body, "district", 80), "land_status": land_status, "phase": phase,
        "levels": levels, "rooms": _opt_int(text(body, "rooms"), 1, 100, "Nombre de pièces : entre 1 et 100."),
        "surface": _opt_int(text(body, "surface"), 1, 100000, "Surface : nombre entier de m²."),
        "dims": opt_text(body, "dims", 160), "deadline": deadline, "budget": budget,
    }


def report_reason(body: dict) -> str:
    v = text(body, "reason")
    if len(v) < 10:
        raise ApiError("Expliquez le problème en quelques mots (10 caractères minimum).")
    return v[:1000]


def experience_years(raw) -> int | None:
    """« 5 », « 5 ans » ou « 12 années » sont acceptés : seul le nombre est gardé. Vide : non renseigné."""
    m = re.search(r"\d+", str(raw or ""))
    if not m:
        return None
    n = int(m.group())
    if n > 60:
        raise ApiError("Années d'expérience : 60 au maximum.")
    return n
