"""Tests des règles métier et des validations (sans base de données). Lancer : pytest"""

from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.db import sqlalchemy_url
from app.errors import ApiError
from app.rules import active_plan, can_decide, docs_for, dossier_complete, dossier_next, missing_items, pro_can_see
from app.security import check_password, check_totp, hash_password, password_problem
from app.utils import add_months, cuid, fcfa, format_phone, iso, now
from app.validators import experience_years, phone, service_request


def pro(**kw):
    base = dict(statut=None, school=None, diploma=None, credentials=[], state="DRAFT", decision_reason=None)
    return NS(**{**base, **kw})


def doc(t, state="RECEIVED"):
    return NS(type=t, state=state)


def test_docs_for_defaults_to_individual():
    assert docs_for(None) == ["ID", "DIPLOME", "EXPERIENCE"]
    assert docs_for("ENT") == ["NINEA", "RCCM", "ID_GERANT", "DIPLOME_RT"]


def test_dossier_complete_and_can_decide():
    p = pro(statut="TECH", school="ESP", diploma="BTS 2018", credentials=[doc("ID"), doc("DIPLOME")])
    assert dossier_complete(p) and can_decide(p)
    p.credentials[1].state = "REFUSED"
    assert not dossier_complete(p) and not can_decide(p)
    # Dossier soumis : l'équipe peut décider même incomplet
    assert can_decide(pro(state="PENDING"))


def test_missing_items():
    assert missing_items(pro()) == ["statut", "établissement de formation", "intitulé du diplôme", "pièce d'identité", "diplôme ou titre professionnel"]


def test_dossier_next_steps():
    assert dossier_next(pro())["title"].startswith("Étape 1")
    p = pro(statut="TECH", school="ESP", diploma="BTS")
    assert dossier_next(p)["title"].startswith("Étape 2")
    p.credentials = [doc("ID"), doc("DIPLOME")]
    assert dossier_next(p)["title"].startswith("Étape 3")
    assert dossier_next(pro(state="PENDING"))["cta"] is None
    assert dossier_next(pro(state="VERIFIED")) is None


def test_pro_can_see():
    assert not pro_can_see("TECH", "etudes", None)
    assert pro_can_see("ING", "etudes", None)
    assert not pro_can_see("TECH", "plans", "R+4 ou plus")
    assert not pro_can_see(None, "plans", None)


def test_active_plan_expires():
    assert active_plan("PRO", now() + timedelta(days=1)) == "PRO"
    assert active_plan("PRO", now() - timedelta(days=1)) == "PRO"  # 2 jours de grâce
    assert active_plan("PRO", now() - timedelta(days=3)) == "FREE"


def test_passwords():
    h = hash_password("un-mot-de-passe-long")
    assert check_password("un-mot-de-passe-long", h) and not check_password("autre", h)
    assert password_problem("court")
    # 8 caractères suffisent pour un particulier ou un pro, l'équipe garde 12
    assert not password_problem("8carac!!") and password_problem("7carac!")
    assert password_problem("8carac!!", 12) and not password_problem("douze-carac!")
    assert not check_password("x", None) and not check_password("x", "pas-une-empreinte")


def test_totp_rejects_bad_codes():
    assert not check_totp(None, "123456")
    assert not check_totp("JBSWY3DPEHPK3PXP", "abc")


def test_utils():
    assert len(cuid()) == 25 and cuid() != cuid()
    assert fcfa(1500000) == "1 500 000 FCFA"
    assert format_phone("771234567") == "77 123 45 67"
    assert format_phone("+221771234567") == "+221 77 123 45 67"
    assert iso(datetime(2026, 10, 19, 12, 0)) == "2026-10-19T12:00:00.000Z"
    assert add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)


def test_database_url_adapted():
    url = sqlalchemy_url("postgresql://u:p@host/db?sslmode=require&pgbouncer=true&connection_limit=1")
    assert url == "postgresql+psycopg://u:p@host/db?sslmode=require"


def test_validators():
    assert phone({"phone": "77 123 45 67"}) == "771234567"
    with pytest.raises(ApiError):
        phone({"phone": "12345"})
    assert experience_years("5 ans") == 5 and experience_years("") is None
    with pytest.raises(ApiError, match="60 au maximum"):
        experience_years("99")


def test_service_request():
    body = {
        "category": "plans", "service": "Plans 2D", "buildingType": "Autre", "description": "x" * 25, "commune": "Ngor",
        "landStatus": "Bail", "phase": "Pas encore de terrain", "deadline": "Sous 1 mois", "budget": "1 500 000 FCFA", "confirm": "on",
    }
    data = service_request(body)
    assert data["budget"] == 1_500_000 and data["levels"] is None
    with pytest.raises(ApiError, match="trop faible"):
        service_request({**body, "budget": "500"})


def test_phone_local():
    from app.utils import phone_local
    # Tous les formats saisis donnent le même identifiant de connexion
    for raw in ("77 123 45 67", "771234567", "+221 77 123 45 67", "+221771234567", "221771234567"):
        assert phone_local(raw) == "771234567"
    assert phone_local("12345") is None and phone_local(None) is None
