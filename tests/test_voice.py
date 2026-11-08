"""Assistant vocal : interprétation des réponses (traduction française et transcription wolof) en valeurs du formulaire."""

from app.services.voice_parse import interpret, parse_budget, parse_budget_wo, parse_building_type, parse_category, parse_commune, parse_confirm, parse_deadline


def test_category():
    assert parse_category("Je veux des plans pour ma maison") == "plans"
    assert parse_category("un devis et des métrés") == "metres"
    assert parse_category("une étude de structure en béton") == "etudes"
    assert parse_category("du dessin sur AutoCAD") == "dao"
    assert parse_category("je ne sais pas") is None


def test_building_type():
    assert parse_building_type("une villa") == "Maison individuelle (plain-pied)"
    assert parse_building_type("une maison avec un étage") == "Maison individuelle (R+1 ou plus)"
    assert parse_building_type("un immeuble de quatre étages") == "Immeuble d'habitation"
    assert parse_building_type("ajouter un étage à ma maison") == "Extension ou surélévation"


def test_commune():
    assert parse_commune("À Ouakam") == "Ouakam"
    assert parse_commune("Sàmba ci waa kàm la sama projet nekk") == "Ouakam"  # prononciation approchée
    assert parse_commune("à Grand Yoff") == "Grand Yoff" and parse_commune("Yoff") == "Yoff"
    assert parse_commune("Sangor la sama projet nekk.") == "Ngor"  # « ci Ngor » collé par la transcription
    assert parse_commune("ci Thiès") is None


def test_budget():
    assert parse_budget("600 000 francs") == 600_000
    assert parse_budget("un million cinq cent mille") == 1_500_000
    assert parse_budget("1,5 million") == 1_500_000
    assert parse_budget("quatre-vingt mille") == 80_000
    assert parse_budget("cent") is None  # sous le minimum
    assert parse_budget_wo("juróom-benni téeméeri junni") == 600_000
    assert parse_budget_wo("benn milyoŋ ak juróomi téeméeri junni") == 1_500_000
    # Le wolof l'emporte sur une traduction française erronée
    assert interpret("budget", "C'est soixante-dix mille.", "Juróom benn téeméeri junni la.") == 600_000


def test_deadline_and_confirm():
    assert parse_deadline("le plus vite possible") == "Dès que possible"
    assert parse_deadline("dans deux semaines") == "Sous 2 semaines"
    assert parse_deadline("dans deux mois") == "Sous 2 mois"
    assert parse_deadline("je ne suis pas pressé") == "Pas de contrainte"
    assert parse_confirm("Oui, c'est bon") is True and parse_confirm("Non") is False and parse_confirm("peut-être") is None
    assert interpret("confirm", "", "Waaw, baax na.") is True
    assert interpret("confirm", "", "Waa") is True  # « waaw » coupé par la transcription
    assert parse_budget_wo("Ñaari miliyon") == 2_000_000


def test_reponses_en_francais():
    # Réponse dite en français : la transcription française l'emporte sur une traduction déformée
    assert interpret("category", "Un, c'est comme si c'était au président.", "…", "Je voudrais des plans pour ma maison.") == "plans"
    assert interpret("budget", "Un million deux cent mille francs.", "Un million deux cent mille francs.", "Un million deux cent mille francs.") == 1_200_000
    assert interpret("confirm", "Oui, c'est bon.", "", "Oui, c'est bon.") is True
    # Réponse en wolof : inchangé
    assert interpret("budget", "C'est soixante-dix mille.", "Juróom benn téeméeri junni la.", "Joroom benne temeri june") == 600_000
