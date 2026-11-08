"""Messages modèles : seuls messages que BUILDSEN peut envoyer à quelqu'un qui ne lui a pas écrit depuis 24 heures.

Ils doivent être déposés et approuvés par Meta, une fois par langue (même nom, langues « fr » et « en » ; voir WHATSAPP.md). Meta ne propose pas
le wolof : les personnes qui ont choisi le wolof reçoivent le modèle en français hors de la fenêtre de 24 heures. Le simulateur affiche les
modèles à l'identique pour que les tests montrent ce que les gens recevront.
"""

from ...config import settings
from .i18n import TEMPLATE_LANG

TEMPLATES: dict[str, dict] = {
    # Catégorie « Utilitaire ». Variables : {1} commune, {2} prestation, {3} budget. Boutons de réponse rapide.
    "alert": {
        "body": {
            "fr": "Nouvelle demande à {1} : {2}. Budget : {3}. Touchez Répondre pour écrire au client.",
            "en": "New request in {1}: {2}. Budget: {3}. Tap Reply to write to the client.",
        },
        "buttons": {"fr": ["Voir", "Répondre", "Ignorer"], "en": ["View", "Reply", "Skip"]},
    },
    # Catégorie « Utilitaire ». Variables : {1} expéditeur, {2} référence de la demande, {3} début du message.
    "message": {
        "body": {
            "fr": "{1} vous a écrit au sujet de la demande {2} : « {3} »",
            "en": "{1} wrote to you about request {2}: \"{3}\"",
        },
        "buttons": {"fr": ["Répondre"], "en": ["Reply"]},
    },
    # Catégorie « Authentification » (bouton « Copier le code » imposé par Meta). Variable : {1} le code.
    "code": {
        "body": {
            "fr": "{1} est votre code de connexion BUILDSEN. Il expire dans 10 minutes.",
            "en": "{1} is your BUILDSEN login code. It expires in 10 minutes.",
        },
        "buttons": {"fr": [], "en": []},
        "auth": True,
    },
}


def template_lang(lang: str | None) -> str:
    return TEMPLATE_LANG.get(lang or "fr", "fr")


def template_name(key: str) -> str:
    return {"alert": settings.whatsapp_tpl_alert, "message": settings.whatsapp_tpl_message, "code": settings.whatsapp_tpl_code}[key]


def template_buttons(key: str, lang: str | None) -> list[str]:
    return TEMPLATES[key]["buttons"][template_lang(lang)]


def clean_param(value: str, limit: int = 120) -> str:
    """Meta refuse les retours à la ligne, les tabulations et les longues suites d'espaces dans une variable."""
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render(key: str, params: list[str], lang: str | None = "fr") -> str:
    body = TEMPLATES[key]["body"][template_lang(lang)]
    for i, p in enumerate(params, 1):
        body = body.replace(f"{{{i}}}", p)
    return body
