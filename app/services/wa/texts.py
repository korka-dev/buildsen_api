"""Textes de l'assistant WhatsApp qui dépendent de la demande (questions, récapitulatif) : toujours dans la langue de la personne."""

from ...config import settings
from ...rules import BUILDING_TYPES, CATEGORIES, DEADLINES, MAX_PROS_PER_REQUEST
from .i18n import tr, value_label

FIELDS = ["category", "buildingType", "description", "commune", "budget", "deadline"]
TIPS = ("description", "budget", "commune")


def title(lang: str, field: str) -> str:
    return tr(lang, f"t_{field}")


def options(lang: str, field: str) -> list[tuple[str, str]]:
    """(valeur du site, libellé dans la langue) proposés par numéro quand la voix ou le texte n'a pas été compris."""
    if field == "category":
        return [(k, value_label(lang, "category", k)) for k in CATEGORIES]
    if field == "buildingType":
        return [(b, value_label(lang, "buildingType", b)) for b in BUILDING_TYPES]
    if field == "deadline":
        return [(d, value_label(lang, "deadline", d)) for d in DEADLINES]
    return []


def question(lang: str, field: str) -> str:
    return tr(lang, f"q_{field}")


def numbered(lang: str, field: str) -> str:
    return "\n".join(f"{i}. {label}" for i, (_, label) in enumerate(options(lang, field), 1))


def shown(lang: str, field: str, value: str) -> str:
    if field == "budget" and value.isdigit():
        return f"{int(value):,}".replace(",", " ") + " FCFA"
    return value_label(lang, field, value)


def recap_text(lang: str, answers: dict) -> str:
    lines = [f"*{title(lang, f)}* : {shown(lang, f, answers.get(f, '—'))}" for f in FIELDS]
    return tr(lang, "recap_head") + "\n\n" + "\n".join(lines) + "\n\n" + tr(lang, "recap_tail")


def published(lang: str, ref: str) -> str:
    return tr(lang, "published", ref=ref, max=MAX_PROS_PER_REQUEST)


def link_site() -> str:
    return settings.app_url
