"""Règles métier de BUILDSEN. Source de vérité côté serveur (le frontend en garde une copie pour l'affichage)."""

from datetime import datetime, timedelta

from .config import settings
from .utils import now

STATUTS: dict[str, dict] = {
    # Architecte : l'inscription à l'Ordre des architectes est contrôlée par l'équipe (attestation demandée)
    "ARCH": {
        "name": "Architecte",
        "scope": "Plans architecturaux, permis de construire et tout type de demande, hors études de structure",
        "docs": ["ID", "DIPLOME", "ORDRE"],
        "optional": ["EXPERIENCE"],
    },
    "ING": {
        "name": "Ingénieur en génie civil",
        "scope": "Tout type de demande, y compris les études de structure",
        "docs": ["ID", "DIPLOME"],
        "optional": ["EXPERIENCE"],
    },
    "TECH": {
        "name": "Technicien supérieur ou dessinateur-projeteur",
        "scope": "Plans, métrés et DAO de projets de moins de 5 étages",
        "docs": ["ID", "DIPLOME"],
        "optional": ["EXPERIENCE"],
    },
    # Entreprise du secteur : les études de structure restent sous la responsabilité d'un ingénieur diplômé
    "ENT": {
        "name": "Entreprise",
        "scope": "Tout type de demande, y compris les études de structure, sous la responsabilité d'un ingénieur",
        "docs": ["NINEA", "RCCM", "ID_GERANT", "DIPLOME_RT"],
        "optional": [],
    },
}

DOC_TYPES = {
    "ID": "Pièce d'identité",
    "DIPLOME": "Diplôme ou titre professionnel",
    "EXPERIENCE": "Attestation d'expérience",
    "NINEA": "NINEA",
    "RCCM": "Registre du commerce (RCCM)",
    "ID_GERANT": "Pièce d'identité du gérant",
    "DIPLOME_RT": "Diplôme d'ingénieur du responsable technique",
    "ORDRE": "Attestation d'inscription à l'Ordre des architectes",
}

SPECIALTIES = {
    "plans": "Plans et conception",
    "metres": "Devis et métrés",
    "dao": "DAO et dessin technique",
    "etudes": "Études techniques",
}

COMMUNES = [
    "Dakar-Plateau", "Médina", "Fann-Point E-Amitié", "Mermoz-Sacré-Cœur", "Ouakam", "Ngor", "Yoff",
    "Parcelles Assainies", "Grand Yoff", "HLM", "Pikine", "Guédiawaye", "Keur Massar", "Rufisque",
    "Diamniadio", "Autre commune",
]

CATEGORIES: dict[str, dict] = {
    "plans": {"name": "Plans et conception", "items": ["Plan architectural", "Dossier de permis de construire", "Conception de plans de maisons", "Modification de plans existants", "Réaménagement de plans", "Mise au propre de plans papier ou numériques", "Plans 2D", "Modélisation 3D", "Plans d'aménagement intérieur"]},
    "metres": {"name": "Devis et métrés", "items": ["Métré des ouvrages", "Quantitatifs de matériaux", "Devis quantitatifs et estimatifs (DQE)", "Bordereaux de prix", "Estimation du coût de construction", "Décomposition des coûts par lot"]},
    "dao": {"name": "DAO et dessin technique", "items": ["Dessin sur AutoCAD", "Reproduction numérique de plans", "Mise en page de documents techniques", "Préparation de plans numériques"]},
    "etudes": {"name": "Études techniques", "items": ["Études de structure", "Plans de béton armé", "Notes de calcul", "Plans de coffrage", "Plans de ferraillage"], "regulated": True},
}
BUILDING_TYPES = ["Maison individuelle (plain-pied)", "Maison individuelle (R+1 ou plus)", "Immeuble d'habitation", "Local commercial ou bureaux", "Extension ou surélévation", "Aménagement intérieur", "Autre"]
PHASES = ["Pas encore de terrain", "Terrain acheté, travaux non commencés", "Travaux déjà commencés", "Bâtiment existant à modifier"]
LEVELS = ["Plain-pied", "R+1", "R+2", "R+3", "R+4 ou plus"]
DEADLINES = ["Dès que possible", "Sous 2 semaines", "Sous 1 mois", "Sous 2 mois", "Pas de contrainte"]
TERRAIN_STATUS = ["Titre foncier", "Bail", "Délibération", "Je ne sais pas"]
MIN_BUDGET = 10_000
MAX_BUDGET = 2_000_000_000

# Ouverture aux particuliers (18/10/2026). False referme l'inscription et le dépôt de demandes des particuliers.
CLIENTS_OPEN = True
OPENING = "décembre 2026"
REVIEW_DELAY = "5 jours ouvrés"
TEAM_INBOX = "contact@buildsen.com"
SUPPORT_WHATSAPP = "221761593003"
SUPPORT_WHATSAPP_DISPLAY = "76 159 30 03"

# Formules (abonnements) masquées et inactives tant que PLANS_ENABLED n'est pas activé (variable d'environnement)
SHOW_PRICING = settings.plans_enabled
PLANS_ACTIVE = SHOW_PRICING
# Nouveaux contacts (premiers messages à un client) par mois civil et par formule ; None = illimité
PLAN_CONTACTS: dict[str, int | None] = {"FREE": 3, "PRO": 15, "PREMIUM": None}
PLAN_MONTHS = (1, 3, 12)
YEAR_DISCOUNT = 10  # % de remise sur l'abonnement annuel (12 mois)
PLANS = {
    "FREE": {"name": "Gratuit", "price": 0},
    "PRO": {"name": "Pro", "price": 5000},
    "PREMIUM": {"name": "Premium", "price": 15000},
}
LAUNCH_OFFER = {"first_pros": 50, "months": 3, "plan": "PRO"}
FREE_ALERT_DELAY_H = 48
REMIND_AFTER_H = 3  # rappel aux professionnels Pro et Premium qui n'ont pas encore regardé une demande ouverte

MAX_PROS_PER_REQUEST = 5
MAX_PORTFOLIO = 12

UPLOADS = {
    "credential": {"bucket": "justificatifs", "max_bytes": 10 * 1024 * 1024, "mimes": ["application/pdf", "image/jpeg", "image/png"]},
    "request": {"bucket": "demandes", "max_bytes": 8 * 1024 * 1024, "mimes": ["image/jpeg", "image/png", "image/webp"]},  # photo du terrain ou croquis
    "portfolio": {"bucket": "portfolios", "max_bytes": 10 * 1024 * 1024, "mimes": ["image/jpeg", "image/png", "image/webp", "application/pdf"]},
}
EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

ADMIN_ROLES = {"SUPER": "Super-admin", "VERIF": "Vérificateur", "SUPPORT": "Support", "MODER": "Modérateur"}

PHONE_RE = r"^(\+221)?\s?7[05678](\s?\d){7}$"


def all_docs(statut: str) -> list[str]:
    return [*STATUTS[statut]["docs"], *STATUTS[statut]["optional"]]


def docs_for(statut: str | None) -> list[str]:
    """Statut pas encore choisi : pièce d'identité et diplôme, comme pour un professionnel individuel."""
    return all_docs(statut if statut in STATUTS else "TECH")


def required_docs(statut: str | None) -> list[str]:
    return STATUTS[statut if statut in STATUTS else "TECH"]["docs"]


def can_do_studies(statut: str | None) -> bool:
    return statut in ("ING", "ENT")


def pro_can_see(statut: str | None, category: str, levels: str | None) -> bool:
    """Études de structure réservées aux ingénieurs et entreprises ; projets de 5 niveaux ou plus exclus pour les techniciens."""
    return bool(statut) and (category != "etudes" or can_do_studies(statut)) and not (statut == "TECH" and levels == "R+4 ou plus")


def plan_rank(p) -> int:
    """Rang d'affichage : Premium d'abord, puis Pro, puis les profils gratuits (sans effet tant que les formules sont inactives)."""
    if not PLANS_ACTIVE:
        return 2
    return {"PREMIUM": 0, "PRO": 1}.get(active_plan(p.plan, p.plan_until), 2)


BIG_BUDGET = 5_000_000  # FCFA : les demandes à gros budget sont réservées aux abonnés (Pro, Premium)


def big_locked(p, budget: int) -> bool:
    """Demande à gros budget fermée à ce professionnel : formule gratuite, quand les formules sont actives."""
    return PLANS_ACTIVE and budget >= BIG_BUDGET and active_plan(p.plan, p.plan_until) == "FREE"


def pro_sees(p, r) -> bool:
    """Le professionnel (profil p) peut voir et contacter cette demande : statut, niveaux et, pour les gros budgets, formule."""
    return pro_can_see(p.statut, r.category, r.levels) and not big_locked(p, r.budget)


def dossier_editable(state: str) -> bool:
    return state in ("DRAFT", "MORE")


def _has_doc(credentials, doc: str) -> bool:
    return any(c.type == doc and c.state != "REFUSED" for c in credentials)


def dossier_complete(p) -> bool:
    """Statut, formation et diplôme renseignés, justificatifs obligatoires envoyés et non refusés."""
    return bool(p.statut in STATUTS and p.school and p.diploma) and all(_has_doc(p.credentials, d) for d in STATUTS[p.statut]["docs"])


def can_decide(p) -> bool:
    """L'équipe décide sur un dossier soumis, ou sur un dossier complet que le professionnel a oublié de soumettre."""
    return p.state == "PENDING" or (p.state in ("DRAFT", "MORE") and dossier_complete(p))


def missing_items(p) -> list[str]:
    """Éléments manquants d'un dossier, en clair (la soumission reste possible même incomplète)."""
    out = []
    if not p.statut:
        out.append("statut")
    if not p.school:
        out.append("établissement de formation")
    if not p.diploma:
        out.append("intitulé du diplôme")
    out += [DOC_TYPES[d].lower() for d in required_docs(p.statut) if not _has_doc(p.credentials, d)]
    return out


def dossier_next(p) -> dict | None:
    """Prochaine étape du dossier d'un professionnel non vérifié (accueil et espace pro)."""
    if p.state in ("VERIFIED", "REFUSED"):
        return None
    if p.state == "PENDING":
        return {"title": "Votre dossier est en cours d'examen", "text": f"L'équipe BUILDSEN l'examine sous {REVIEW_DELAY}. Votre profil sera visible par les clients dès qu'il sera vérifié : vous serez prévenu par e-mail.", "cta": None}
    more = f" Complément demandé : {p.decision_reason}" if p.state == "MORE" and p.decision_reason else ""
    if not (p.statut and p.school and p.diploma):
        return {"title": "Étape 1 sur 3 : vos informations", "text": "Votre profil n'est pas encore visible. Choisissez votre statut, puis indiquez votre formation et votre diplôme." + more, "cta": "Compléter mon dossier"}
    missing = [d for d in STATUTS[p.statut]["docs"] if not _has_doc(p.credentials, d)]
    if missing:
        names = ", ".join(DOC_TYPES[d].lower() for d in missing)
        return {"title": "Étape 2 sur 3 : vos justificatifs", "text": f"Votre profil n'est pas encore visible. Il manque : {names}. Une photo nette prise au téléphone suffit." + more, "cta": "Envoyer mes documents"}
    return {"title": "Étape 3 sur 3 : soumettez votre dossier", "text": "Tout est prêt : il ne reste qu'à cliquer sur « Soumettre mon dossier pour vérification »." + more, "cta": "Soumettre mon dossier"}


GRACE_DAYS = 2  # après l'échéance, la formule reste active 2 jours : le temps de renouveler sans être coupé


def active_plan(plan: str, plan_until: datetime | None) -> str:
    """Formule réellement active : une formule payante ou offerte repasse en formule gratuite 2 jours après son échéance."""
    if plan == "FREE":
        return "FREE"
    return "FREE" if plan_until and plan_until + timedelta(days=GRACE_DAYS) < now() else plan


def whatsapp_link(text: str = "Bonjour BUILDSEN, j'ai une question.") -> str:
    from urllib.parse import quote

    return f"https://wa.me/{SUPPORT_WHATSAPP}?text={quote(text)}"
