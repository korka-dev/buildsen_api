"""Assistant vocal : interprétation des réponses (dites en wolof, traduites en français par Soynade) en valeurs du formulaire.

Chaque fonction reçoit le texte français et renvoie la valeur attendue par le formulaire « Nouvelle demande »,
ou None quand la réponse n'a pas été comprise (l'assistant demande alors de répéter, ou de choisir à l'écran).
"""

import re
import unicodedata

from ..rules import BUILDING_TYPES, CATEGORIES, COMMUNES, DEADLINES, MAX_BUDGET, MIN_BUDGET


def norm(s: str) -> str:
    """Minuscules, sans accents ni ponctuation superflue."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9+ ]+", " ", s).strip()


def _has(t: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}", t) for w in words)


def parse_category(text: str) -> str | None:
    t = norm(text)
    if _has(t, "etude", "structure", "beton", "ferraill", "calcul", "coffrage", "ingenieur", "structural", "concrete", "engineer", "reinforc"):
        return "etudes"
    if _has(t, "devis", "metre", "quantit", "dqe", "cout", "estimation", "bordereau", "prix", "quote", "estimate", "cost", "pricing"):
        return "metres"
    if _has(t, "dao", "autocad", "dessin technique", "numeris", "reproduction", "mise au propre", "cad", "technical draw", "drafting", "digiti"):
        return "dao"
    if _has(t, "plan", "conception", "dessin", "3d", "2d", "maison", "villa", "amenagement", "design", "house", "drawing", "layout", "blueprint"):
        return "plans"
    return None


def parse_building_type(text: str) -> str | None:
    t = norm(text)
    rules: list[tuple[tuple[str, ...], str]] = [
        (("immeuble", "appartement", "batiment collectif", "apartment", "building block", "flats"), "Immeuble d'habitation"),
        (("commerc", "boutique", "magasin", "bureau", "local", "shop", "store", "office"), "Local commercial ou bureaux"),
        (("extension", "agrandi", "surelev", "ajouter un etage", "rajouter", "add a floor", "extra floor", "add floor", "expand", "enlarge"), "Extension ou surélévation"),
        (("interieur", "decoration", "renovation interieure", "interior", "fit out", "fitout", "renovation"), "Aménagement intérieur"),
        (("etage", "r+", "duplex", "deux niveaux", "niveau", "floor", "storey", "story", "two level", "upstairs"), "Maison individuelle (R+1 ou plus)"),
        (("maison", "villa", "plain pied", "rez de chaussee", "ker", "house", "bungalow", "ground floor", "single"), "Maison individuelle (plain-pied)"),
    ]
    for words, value in rules:
        if _has(t, *words):
            return value
    return None


# Noms courants des communes, du plus précis au plus général (« Grand Yoff » avant « Yoff »)
_COMMUNE_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("grand yoff",), "Grand Yoff"),
    (("parcelles",), "Parcelles Assainies"),
    (("keur massar",), "Keur Massar"),
    (("plateau", "centre ville"), "Dakar-Plateau"),
    (("point e", "fann", "amitie"), "Fann-Point E-Amitié"),
    (("mermoz", "sacre coeur"), "Mermoz-Sacré-Cœur"),
    (("guediawaye",), "Guédiawaye"),
    (("diamniadio",), "Diamniadio"),
    (("rufisque",), "Rufisque"),
    (("pikine",), "Pikine"),
    (("medina",), "Médina"),
    (("ouakam",), "Ouakam"),
    (("ngor",), "Ngor"),
    (("yoff",), "Yoff"),
    (("hlm",), "HLM"),
]


def _phon(s: str) -> str:
    """Forme phonétique simple : « Ouakam », « Wakam » et « waa kam » donnent « wakam »."""
    s = norm(s).replace("ou", "w").replace("ch", "s").replace(" ", "")
    return re.sub(r"(.)\1+", r"\1", s)


def parse_commune(text: str) -> str | None:
    t = norm(text)
    for words, value in _COMMUNE_ALIASES:
        if _has(t, *words) and value in COMMUNES:
            return value
    # Prononciation approchée : on compare chaque mot et chaque paire de mots à la forme phonétique des communes
    words = t.split()
    chunks = {_phon(w) for w in words} | {_phon(a + b) for a, b in zip(words, words[1:], strict=False)}
    for words_, value in _COMMUNE_ALIASES:
        # Nom entier ou collé à un autre mot par la transcription (« ci Ngor » -> « Sangor »)
        if any(len(_phon(w)) >= 4 and any(_phon(w) in c for c in chunks) for w in words_):
            return value
    return None


_UNITS = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
    "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60, "septante": 70, "huitante": 80, "nonante": 90,
}


def _words_to_number(words: list[str]) -> int | None:
    """« cinq cent mille », « un million cinq cent mille », « deux millions »… (nombres en lettres)."""
    total, current, seen, prev = 0, 0, False, ""
    for w in words:
        if w == "vingts":
            w = "vingt"
        if w in ("et", "de", "d"):
            continue
        if w == "vingt" and prev == "quatre":
            current += 80 - 4  # quatre-vingt(s)
        elif w in _UNITS:
            current += _UNITS[w]
        elif re.fullmatch(r"\d+(?:[.,]\d+)?", w):
            current += float(w.replace(",", "."))
        elif w in ("cent", "cents"):
            current = (current or 1) * 100
        elif w == "mille":
            total += (current or 1) * 1000
            current = 0
        elif w in ("million", "millions"):
            total += (current or 1) * 1_000_000
            current = 0
        elif w in ("milliard", "milliards"):
            total += (current or 1) * 1_000_000_000
            current = 0
        else:
            prev = w
            continue
        seen = True
        prev = w
    return int(total + current) if seen else None


_EN_NUMBERS = {
    "hundred": "cent", "thousand": "mille", "one": "un", "two": "deux", "three": "trois", "four": "quatre", "five": "cinq",
    "seven": "sept", "eight": "huit", "nine": "neuf", "ten": "dix", "twenty": "vingt", "thirty": "trente", "forty": "quarante", "fifty": "cinquante",
    "sixty": "soixante", "seventy": "septante", "eighty": "huitante", "ninety": "nonante",
}


def parse_budget(text: str) -> int | None:
    """« 600 000 francs », « 1,5 million », « cinq cent mille », « deux millions »…"""
    t = text.lower().replace("\u202f", " ").replace("\xa0", " ")
    for en, fr in _EN_NUMBERS.items():  # « five hundred thousand » -> « cinq cent mille »
        t = re.sub(rf"\b{en}\b", fr, t)
    t = re.sub(r"(\d)[ .](?=\d{3}\b)", r"\1", t)  # 600 000 et 600.000 -> 600000
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)  # 1,5 -> 1.5
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    tokens = re.sub(r"[^a-z0-9. ]+", " ", t).split()
    if any(w in ("mille", "million", "millions", "milliard", "milliards") for w in tokens):
        value = _words_to_number(tokens)
    else:
        m = re.search(r"\d+", t)
        value = int(m.group(0)) if m else _words_to_number(tokens)
    if value is None or value < MIN_BUDGET or value > MAX_BUDGET:
        return None
    return value


# Nombres dits en wolof (transcription Soynade) : « juróom-benni téeméeri junni » = 600 000
_WO_UNITS = {"benn": 1, "ñaar": 2, "naar": 2, "ñett": 3, "nett": 3, "ñeent": 4, "neent": 4, "juróom": 5, "jurom": 5, "fukk": 10}
_WO_MULT = {
    "téeméer": 100,
    "temeer": 100,
    "teemeer": 100,
    "junni": 1000,
    "juni": 1000,
    "milyoŋ": 1_000_000,
    "milyon": 1_000_000,
    "million": 1_000_000,
    "miliyoŋ": 1_000_000,
    "miliyon": 1_000_000,
}


def parse_budget_wo(text: str) -> int | None:
    t = re.sub(r"[^\wñŋóéëàÀ-ÿ ]+", " ", text.lower().replace("-", " "))
    total, current, seen = 0, 0, False
    for raw in t.split():
        w = re.sub(r"(i|u)$", "", raw) if raw not in _WO_UNITS and raw not in _WO_MULT else raw  # « téeméeri », « junnii »
        if w in _WO_UNITS:
            current += _WO_UNITS[w]
        elif w in _WO_MULT:
            m = _WO_MULT[w]
            if m == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * m
                current = 0
        elif raw.isdigit():
            current += int(raw)
        else:
            continue
        seen = True
    value = total + current if seen else None
    return value if value and MIN_BUDGET <= value <= MAX_BUDGET else None


def parse_deadline(text: str) -> str | None:
    t = norm(text)
    if _has(t, "no rush", "no hurry", "no deadline", "no constraint", "whenever", "take my time", "pas presse", "pas de contrainte", "pas d urgence", "pas urgent", "quand", "n importe", "prendre le temps", "rien ne presse"):
        return "Pas de contrainte"
    if _has(t, "deux mois", "2 mois", "two months", "2 months"):
        return "Sous 2 mois"
    if _has(t, "un mois", "1 mois", "mois", "one month", "1 month", "month"):
        return "Sous 1 mois"
    if _has(t, "semaine", "quinze jours", "15 jours", "two weeks", "2 weeks", "fortnight", "week"):
        return "Sous 2 semaines"
    if _has(t, "vite", "urgent", "rapidement", "maintenant", "possible", "aujourd", "demain", "tot", "asap", "quickly", "soon", "immediately", "now"):
        return "Dès que possible"
    return None


def parse_description(text: str) -> str | None:
    t = text.strip()
    return t[:2000] if len(t) >= 20 else None


def parse_confirm(text: str) -> bool | None:
    t = norm(text)
    if re.fullmatch(r"(no|nope|nah|not ok|not correct|wrong|change|edit)( .*)?", t) or _has(t, "non", "pas correct", "faux", "erreur", "recommence", "deedeet", "deet"):
        return False
    if re.fullmatch(r"(yes|yeah|yep|yup|sure|correct|right|confirm|okay|fine|good|perfect)( .*)?", t) or _has(t, "oui", "d accord", "ok", "c est bon", "exact", "correct", "parfait", "valide", "waaw", "wao"):
        return True
    return None


_FR_NUMBER_WORDS = set(_UNITS) | {"cent", "cents", "mille", "milliard", "milliards"}


def _french_words(text: str) -> bool:
    """La réponse a été dite en français (assez de mots courants du français)."""
    words = norm(text).split()
    common = {
        "je",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "de",
        "du",
        "et",
        "avec",
        "pour",
        "dans",
        "c",
        "est",
        "mon",
        "ma",
        "mes",
        "veux",
        "voudrais",
        "oui",
        "non",
        "maison",
    }
    return sum(w in common for w in words) >= 2 or (len(words) <= 3 and any(w in common for w in words))


def interpret(field: str, fr: str, wo: str, fr_said: str = ""):
    """Valeur du formulaire à partir de la traduction française, de la transcription wolof (noms propres)
    et de la transcription française (réponse dite directement en français)."""
    said_in_french = bool(fr_said) and _french_words(fr_said)
    if field == "description":
        return parse_description(fr_said if said_in_french else (fr or wo))
    if field == "budget":
        if said_in_french or any(w in _FR_NUMBER_WORDS for w in norm(wo).split()):
            return parse_budget(fr_said) or parse_budget(wo) or parse_budget(fr)
        # Réponse en wolof : la transcription d'abord, la traduction française déforme parfois les montants
        return parse_budget_wo(wo) or parse_budget(wo) or parse_budget(fr)
    texts = [fr_said, fr, wo] if said_in_french else [fr, wo, fr_said]
    value = None
    for t in texts:
        if t and value is None:
            value = PARSERS[field](t)
    if value is None and field == "confirm":
        w = norm(wo)
        value = True if re.search(r"\bwa+w?\b|\bwao\b", w) else False if _has(w, "deedeet", "deet") else None
    return value


PARSERS = {
    "category": parse_category,
    "buildingType": parse_building_type,
    "description": parse_description,
    "commune": parse_commune,
    "budget": parse_budget,
    "deadline": parse_deadline,
    "confirm": parse_confirm,
}


def label(field: str, value) -> str:
    """Texte affiché « J'ai compris : … »."""
    if field == "category":
        return CATEGORIES[value]["name"]
    if field == "budget":
        return f"{value:,}".replace(",", " ") + " FCFA"
    if field == "confirm":
        return "Oui" if value else "Non"
    return str(value)


# Vérifie que les valeurs renvoyées existent bien dans les listes du formulaire
assert all(
    v in BUILDING_TYPES
    for v in (
        "Immeuble d'habitation",
        "Local commercial ou bureaux",
        "Extension ou surélévation",
        "Aménagement intérieur",
        "Maison individuelle (R+1 ou plus)",
        "Maison individuelle (plain-pied)",
    )
)
assert all(v in DEADLINES for v in ("Pas de contrainte", "Sous 2 mois", "Sous 1 mois", "Sous 2 semaines", "Dès que possible"))
