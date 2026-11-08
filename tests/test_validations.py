"""Validations de saisie : inscription, demande, messages, profil, dossier. Chaque règle refuse proprement (jamais d'erreur serveur)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_paiements import fake_voice  # noqa: F401
from test_securite import ORIGIN, anon, login_as
from test_whatsapp import make_client, make_pro

from app.main import app
from app.models import ProProfile, ServiceRequest, User
from app.security import hash_password

GOOD = {"firstName": "Awa", "lastName": "Diop", "email": "awa@x.test", "phone": "77 123 45 67", "password": "Motdepasse-1234", "cgu": True}


def signup(**over):
    return anon().post("/api/auth/signup-client", json={**GOOD, **over})


# --- inscription -------------------------------------------------------------------------------------------------------------

def test_inscription_valide_et_donnees_normalisees(db):
    r = signup(email="  Awa.DIOP@X.Test ", phone="+221 77 123 45 67", firstName="  Awa  ")
    assert r.status_code == 200
    u = db.scalar(select(User))
    assert u.email == "awa.diop@x.test" and u.phone == "771234567" and u.first_name == "Awa" and u.password_hash != "Motdepasse-1234"


@pytest.mark.parametrize("over", [
    {"firstName": ""}, {"firstName": "   "}, {"lastName": ""}, {"email": ""}, {"email": "pas-un-email"}, {"email": "a@b"}, {"email": "a b@x.test"}, {"email": "a@x.test" + "x" * 200},
    {"phone": ""}, {"phone": "12345"}, {"phone": "771234"}, {"phone": "99 123 45 67"}, {"phone": "77 123 45 6789"}, {"phone": "abc"},
    {"password": ""}, {"password": "court"}, {"password": "1234567"}, {"password": "x" * 201}, {"password": "password"}, {"password": "motdepasse123"}, {"password": "aaaaaaaaaa"}, {"password": "1212121212"}, {"password": "pass\x00word12"}, {"cgu": False}, {"cgu": "non"}, {"cgu": None},
])
def test_inscription_refusee(db, over):
    r = signup(**over)
    assert r.status_code == 400, (over, r.text)
    assert db.scalar(select(User.id)) is None


def test_inscription_doublons_et_variantes(db):
    assert signup().status_code == 200
    assert signup(email="AWA@x.test", phone="78 000 00 01").status_code == 409  # même e-mail, casse différente
    assert signup(email="autre@x.test", phone="+221771234567").status_code == 409  # même numéro, autre écriture
    assert signup(email="autre@x.test", phone="77-123-45-67").status_code in (400, 409)


def test_inscription_champs_de_mauvais_type_et_enormes(db):
    for body in ({**GOOD, "firstName": ["a"]}, {**GOOD, "email": {"x": 1}}, {**GOOD, "phone": 771234567}, {**GOOD, "password": 12345678}, {**GOOD, "firstName": "A" * 100_000}):
        assert anon().post("/api/auth/signup-client", json=body).status_code < 500
    assert anon().post("/api/auth/signup-client", json={**GOOD, "password": "pass\x00word-12345"}).status_code < 500
    assert anon().post("/api/auth/signup-client", json={**GOOD, "email": "nul\x00@x.test", "phone": "77 000 00 02"}).status_code < 500


def test_noms_html_gardes_comme_texte_et_tronques(db):
    assert signup(firstName="<script>alert(1)</script>" + "x" * 100).status_code == 200
    assert len(db.scalar(select(User.first_name))) <= 60


def test_inscription_pro_entreprise_exige_un_nom(db):
    base = {**GOOD, "email": "ent@x.test", "kind": "ENT"}
    assert anon().post("/api/auth/signup-pro", json={**base, "companyName": ""}).status_code == 400
    assert anon().post("/api/auth/signup-pro", json={**base, "companyName": "A"}).status_code == 400
    assert anon().post("/api/auth/signup-pro", json={**base, "companyName": "Fall BTP SARL"}).status_code == 200
    p = db.scalar(select(ProProfile))
    assert p.statut == "ENT" and p.company_name == "Fall BTP SARL" and p.state == "DRAFT"


# --- demande -----------------------------------------------------------------------------------------------------------------

REQ = {"category": "plans", "buildingType": "Maison individuelle (plain-pied)", "description": "Je veux une villa avec quatre chambres et un salon.", "commune": "Ngor",
       "deadline": "Sous 1 mois", "budget": "600000", "confirm": "on"}


def client_session(db):
    u = make_client(db, "771000021")
    return u, login_as(db, u)


def post_request(c, **over):
    return c.post("/api/client/requests", json={**REQ, **over})


def test_demande_valide(db):
    u, c = client_session(db)
    r = post_request(c, budget="600 000 FCFA", district="Mermoz", rooms="6", surface="200", levels="R+1")
    assert r.status_code == 200
    q = db.scalar(select(ServiceRequest))
    assert q.budget == 600_000 and q.state == "PUBLISHED" and q.client_id == u.id and q.rooms == 6 and q.surface == 200


@pytest.mark.parametrize("over", [
    {"category": "inconnue"}, {"category": ""}, {"service": "Une prestation inventée"}, {"buildingType": "Château"}, {"buildingType": ""},
    {"description": ""}, {"description": "trop court"}, {"description": "x" * 2001}, {"commune": "Paris"}, {"commune": ""}, {"deadline": "Hier"}, {"deadline": ""},
    {"budget": ""}, {"budget": "abc"}, {"budget": "-5000"}, {"budget": "0"}, {"budget": "9999"}, {"budget": "2000000001"}, {"budget": "12.5 millions"},
    {"landStatus": "Inventé"}, {"phase": "Inventée"}, {"levels": "R+99"}, {"rooms": "0"}, {"rooms": "101"}, {"rooms": "deux"}, {"surface": "0"}, {"surface": "abc"},
    {"district": "x" * 81}, {"dims": "x" * 161}, {"confirm": ""}, {"confirm": False},
])
def test_demande_refusee(db, over):
    _, c = client_session(db)
    r = post_request(c, **over)
    assert r.status_code == 400, (over, r.text)
    assert db.scalar(select(ServiceRequest.id)) is None


@pytest.mark.parametrize("budget", ["²²²²²", "①②③④⑤", "٦٠٠٠٠٠", "9" * 5000, "1" + "0" * 4400, "６００００００", "6e5", "0x1F4"])
def test_budgets_exotiques_jamais_d_erreur_serveur(db, budget):
    _, c = client_session(db)
    assert post_request(c, budget=budget).status_code < 500


@pytest.mark.parametrize("field", ["rooms", "surface"])
def test_nombres_exotiques_jamais_d_erreur_serveur(db, field):
    _, c = client_session(db)
    for v in ("²", "٣", "9" * 5000, "１２"):
        assert post_request(c, **{field: v}).status_code < 500, (field, v)


def test_demande_description_html_et_nul(db):
    _, c = client_session(db)
    r = post_request(c, description="<script>alert('x')</script> villa de quatre chambres avec salon")
    assert r.status_code == 200 and "<script>" in db.scalar(select(ServiceRequest.description))  # stocké tel quel : l'affichage échappe (React)
    assert post_request(c, description="Villa de quatre chambres\x00 avec salon et cuisine.").status_code < 500


def test_limite_de_cinq_demandes_par_jour(db):
    _, c = client_session(db)
    codes = [post_request(c).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_demande_refusee_si_e_mail_non_confirme_ou_sans_session(db):
    assert anon().post("/api/client/requests", json=REQ).status_code == 401
    u = make_client(db, "771000022")
    u.email_verified_at = None
    db.commit()
    assert login_as(db, u).post("/api/client/requests", json=REQ).status_code == 403


# --- messages ----------------------------------------------------------------------------------------------------------------

def test_messages_limites(db):
    from app.services import actions

    a, p = make_client(db, "771000031"), make_pro(db, "771000032")
    r = ServiceRequest(ref="BS-V-1", client_id=a.id, category="plans", service="Plans et conception", building_type="Autre", description="Une description suffisamment longue.", commune="Ngor",
                       land_status="Non précisé", phase="Non précisé", deadline="Sous 1 mois", budget=100_000, state="PUBLISHED")
    db.add(r)
    db.commit()
    conv_id, _ = actions.start_conversation(db, p, r.id, "Bonjour, je peux réaliser vos plans en trois semaines.")
    c = login_as(db, a)
    url = f"/api/conversations/{conv_id}/messages"
    assert c.post(url, json={"body": ""}).status_code == 400
    assert c.post(url, json={"body": "   \n\t  "}).status_code == 400
    assert c.post(url, json={"body": "x" * 2001}).status_code == 400
    assert c.post(url, json={"body": "x" * 2000}).status_code == 200
    assert c.post(url, json={"body": {"a": 1}}).status_code < 500
    assert c.post(url, json={}).status_code == 400
    assert c.post(f"/api/conversations/{conv_id}/report", json={"reason": "court"}).status_code == 400
    # premier message d'un professionnel : 20 caractères minimum
    p2 = make_pro(db, "771000033")
    pc = login_as(db, p2)
    assert pc.post(f"/api/pro/requests/{r.id}/start", json={"body": "trop court"}).status_code == 400
    assert pc.post(f"/api/pro/requests/{r.id}/start", json={"body": "y" * 2001}).status_code == 400


# --- profil, dossier, téléphone ----------------------------------------------------------------------------------------------

def test_profil_professionnel_validations(db):
    p = make_pro(db, "771000041", verified=False)
    c = login_as(db, p)
    assert c.put("/api/pro/profile", json={"bio": "x" * 801, "specialties": []}).status_code == 400
    assert c.put("/api/pro/profile", json={"bio": "ok", "specialties": ["inconnue", {"a": 1}, 5, None, "plans"]}).status_code == 200
    db.expire_all()
    assert db.scalar(select(ProProfile.specialties).where(ProProfile.user_id == p.id)) == ["plans"]  # valeurs inconnues écartées
    assert c.put("/api/pro/profile", json={"bio": "ok", "specialties": "plans"}).status_code < 500
    assert c.put("/api/pro/profile", json={"bio": "ok", "commune": "Paris", "specialties": []}).status_code == 400
    assert c.put("/api/pro/profile", json={"bio": "Bureau d'études", "specialties": ["plans", "metres"], "commune": "Ngor", "visible": True}).status_code == 200
    assert c.put("/api/pro/profile", json={"bio": "\x00bio", "specialties": []}).status_code < 500


def test_informations_du_dossier_validations(db):
    p = make_pro(db, "771000042", verified=False)
    c = login_as(db, p)
    ok = {"statut": "ING", "school": "ESP Dakar", "diploma": "Ingénieur civil", "experienceYears": "5 ans"}
    assert c.put("/api/pro/dossier", json=ok).status_code == 200
    assert c.put("/api/pro/dossier", json={**ok, "statut": "ROI"}).status_code == 400
    assert c.put("/api/pro/dossier", json={**ok, "experienceYears": "99"}).status_code == 400
    assert c.put("/api/pro/dossier", json={**ok, "experienceYears": "²"}).status_code < 500
    assert c.put("/api/pro/dossier", json={**ok, "school": "x" * 500}).status_code < 500
    verified = login_as(db, make_pro(db, "771000043"), token="v")
    assert verified.put("/api/pro/dossier", json=ok).status_code in (400, 403, 409)  # dossier vérifié : plus modifiable


def test_changement_de_telephone_validations(db):
    a, b = make_client(db, "771000051"), make_client(db, "771000052")
    a.password_hash = hash_password("Motdepasse-1234")
    db.commit()
    c = login_as(db, a)
    pw = {"password": "Motdepasse-1234"}
    assert c.put("/api/client/phone", json={"phone": "abc", **pw}).status_code == 400
    assert c.put("/api/client/phone", json={"phone": "77 000 00 53"}).status_code == 403  # sans mot de passe : refusé
    assert c.put("/api/client/phone", json={"phone": "77 000 00 53", "password": "faux"}).status_code == 403
    assert c.put("/api/client/phone", json={"phone": "77 000 00 53", **pw}).status_code == 200
    assert c.put("/api/client/phone", json={"phone": "771000052", **pw}).status_code == 409  # numéro d'un autre compte
    assert db.get(User, b.id).phone == "771000052"


def test_checkout_validations(db, monkeypatch):
    for mod in ("app.services.pay.service", "app.services.wa.bot", "app.services.alerts"):
        monkeypatch.setattr(f"{mod}.PLANS_ACTIVE", True)
    c = login_as(db, make_pro(db, "771000061"))
    for body in ({"plan": "FREE"}, {"plan": "ULTRA"}, {"plan": "PRO", "months": 13}, {"plan": "PRO", "months": "abc"}, {"plan": ["PRO"]}, {}):
        assert c.post("/api/pro/checkout", json=body).status_code in (400, 403), body


def test_connexion_champs_invalides(db):
    c = anon()
    for body in ({}, {"phone": ""}, {"phone": "771234567"}, {"password": "x"}, {"phone": "abc", "password": "x"}, {"phone": ["77"], "password": ["x"]}, {"phone": "771234567", "password": "x" * 100_000}):
        assert c.post("/api/auth/login", json=body).status_code in (400, 401, 429), body


def test_mot_de_passe_hache_et_jamais_renvoye(db):
    r = signup()
    assert "Motdepasse" not in r.text and "passwordHash" not in r.text
    u = db.scalar(select(User))
    assert u.password_hash.startswith("$2") and hash_password("a") != hash_password("a")  # bcrypt avec sel
    c = login_as(db, u)
    assert "passwordHash" not in c.get("/api/me").text and "password_hash" not in c.get("/api/me").text
    assert "tokenHash" not in c.get("/api/me").text and "totpSecret" not in c.get("/api/me").text


def test_pages_publiques_sans_donnees_privees(db):
    p = make_pro(db, "771000071")
    p.pro.bio = "Bureau"
    db.commit()
    home = TestClient(app, headers=ORIGIN).get("/api/public/home").text
    card = TestClient(app, headers=ORIGIN).get(f"/api/public/pros/{p.pro.id}").text
    for blob in (home, card):
        assert "771000071" not in blob and p.email not in blob and "passwordHash" not in blob and "decisionReason" not in blob
