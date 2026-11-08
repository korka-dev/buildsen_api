"""Sécurité : CSRF, droits d'accès par rôle, accès aux données d'autrui, sessions, limites de débit, injections, secrets."""

import subprocess
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_paiements import fake_voice  # noqa: F401
from test_whatsapp import _admin_client, make_client, make_pro, publish_request

from app.config import settings
from app.main import app
from app.models import Conversation, ProProfile, ServiceRequest, Session, User
from app.security import COOKIE, hash_password, issue_user_token, sha256
from app.utils import now

ORIGIN = {"Origin": "http://localhost:3000"}


def login_as(db, user, token=None, mfa=False, origin=True):
    token = token or f"t-{user.id}"
    db.add(Session(token_hash=sha256(token), user_id=user.id, mfa=mfa, expires_at=now() + timedelta(hours=1)))
    db.commit()
    c = TestClient(app, headers=ORIGIN if origin else {})
    c.cookies.set(COOKIE, token)
    return c


def anon():
    return TestClient(app, headers=ORIGIN)


@pytest.fixture
def world(db):
    """Deux particuliers, deux professionnels vérifiés, une demande de chaque particulier et une conversation."""
    a, b = make_client(db, "771000011", "Awa", "Diop"), make_client(db, "771000012", "Bineta", "Sy")
    p1, p2 = make_pro(db, "771000013"), make_pro(db, "771000014")
    p1.pro.statut = p2.pro.statut = "ING"
    db.commit()
    ra = publish_request(db, "771000011")
    rb = publish_request(db, "771000012")
    return {"a": a, "b": b, "p1": p1, "p2": p2, "ra": ra, "rb": rb}


# --- CSRF --------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://localhost:3000.evil.com", "http://evil.com/localhost:3000", "null"])
def test_csrf_origines_refusees(db, origin):
    c = TestClient(app, headers={"Origin": origin} if origin else {})
    for method, path in (("post", "/api/auth/login"), ("post", "/api/auth/logout"), ("put", "/api/client/phone"), ("delete", "/api/pro/portfolio/x"), ("post", "/api/admin/login")):
        r = c.request(method.upper(), path, json={})
        assert r.status_code == 403 and r.json()["error"] == "Origine refusée.", (method, path, origin)


def test_csrf_referer_accepte_comme_secours_et_get_libre(db):
    c = TestClient(app, headers={"Referer": "http://localhost:3000/connexion"})
    assert c.post("/api/auth/login", json={"phone": "771234567", "password": "x"}).status_code == 401  # passe la protection, refusé ensuite
    assert TestClient(app).get("/api/health").status_code == 200


def test_csrf_webhooks_exemptes_mais_proteges_par_signature(db):
    assert TestClient(app).post("/api/wa/webhook", content=b"{}").status_code in (403, 404)  # pas 403 « Origine refusée » : signature/Meta absents
    assert TestClient(app).post("/api/payments/webhook/paydunya", content=b"data[invoice][token]=x").status_code in (200, 404)


# --- droits par rôle ---------------------------------------------------------------------------------------------------------

ADMIN_ROUTES = [
    ("get", "/api/admin/dossiers", ("VERIF",)), ("get", "/api/admin/requests", ("MODER",)), ("get", "/api/admin/reports", ("MODER", "SUPPORT")),
    ("get", "/api/admin/users", ("VERIF", "SUPPORT")), ("get", "/api/admin/payments", ("SUPPORT",)), ("get", "/api/admin/team", ()),
    ("get", "/api/admin/showcase", ("SUPPORT", "MODER")), ("get", "/api/admin/whatsapp", ("SUPPORT", "MODER")), ("get", "/api/admin/journal", ("VERIF", "SUPPORT", "MODER")),
    ("post", "/api/admin/team/invite", ()), ("post", "/api/admin/users/x/delete", ()), ("post", "/api/admin/users/x/plan", ("SUPPORT",)),
    ("post", "/api/admin/whatsapp/enabled", ("SUPPORT", "MODER")), ("post", "/api/admin/ads", ("SUPPORT", "MODER")),
]


@pytest.mark.parametrize(("method", "path", "allowed"), ADMIN_ROUTES)
def test_matrice_des_roles_admin(db, method, path, allowed):
    for role in ("VERIF", "SUPPORT", "MODER", "SUPER"):
        db.query(Session).delete()
        db.query(User).filter(User.role == "ADMIN").delete()
        db.commit()
        c = _admin_client(db, role)
        r = c.request(method.upper(), path, json={})
        permitted = role == "SUPER" or role in allowed
        if permitted:
            assert r.status_code not in (401, 403), (role, path, r.status_code)
        else:
            assert r.status_code == 403, (role, path, r.status_code)


def test_anonymes_et_mauvais_roles_exclus(db, world):
    for path in ("/api/client/requests", "/api/client/conversations", "/api/pro/profile", "/api/pro/requests", "/api/admin/overview", "/api/admin/me", "/api/pro/plan"):
        assert anon().get(path).status_code == 401, path
    client = login_as(db, world["a"])
    pro = login_as(db, world["p1"])
    for path in ("/api/pro/profile", "/api/pro/requests", "/api/admin/overview", "/api/pro/plan"):
        assert client.get(path).status_code == 401, ("client", path)
    for path in ("/api/client/requests", "/api/client/conversations", "/api/admin/overview"):
        assert pro.get(path).status_code == 401, ("pro", path)


def test_admin_sans_double_authentification_exclu_partout(db):
    adm = User(email="half@x.test", first_name="H", last_name="A", role="ADMIN", admin_role="SUPER", totp_enabled=True)
    db.add(adm)
    db.commit()
    c = login_as(db, adm, mfa=False)
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/dossiers", "/api/admin/journal"):
        assert c.get(path).status_code == 401, path


# --- données d'autrui (IDOR) -------------------------------------------------------------------------------------------------

def test_un_particulier_ne_voit_pas_les_demandes_d_un_autre(db, world):
    a = login_as(db, world["a"])
    assert a.get(f"/api/client/requests/{world['ra'].ref}").status_code == 200
    assert a.get(f"/api/client/requests/{world['rb'].ref}").status_code == 404
    assert a.post(f"/api/client/requests/{world['rb'].ref}/close", json={}).status_code == 404
    assert db.get(ServiceRequest, world["rb"].id).state == "PUBLISHED"
    refs = [r["ref"] for r in a.get("/api/client/requests").json()["requests"]]
    assert refs == [world["ra"].ref]


def _talk(db, world):
    from app.services import actions

    conv_id, _ = actions.start_conversation(db, world["p1"], world["ra"].id, "Bonjour, je peux faire vos plans en trois semaines, voici ma proposition.")
    return conv_id


def test_conversations_reservees_a_leurs_deux_participants(db, world):
    conv_id = _talk(db, world)
    for user, code in ((world["a"], 200), (world["p1"], 200), (world["b"], 404), (world["p2"], 404)):
        c = login_as(db, user, token=f"c-{user.id}")
        assert c.get(f"/api/conversations/{conv_id}").status_code == code, user.email
        r = c.post(f"/api/conversations/{conv_id}/messages", json={"body": "Un message de test assez long."})
        assert r.status_code == (200 if code == 200 else 404), user.email
        assert c.post(f"/api/conversations/{conv_id}/report", json={"reason": "Un motif de signalement assez long."}).status_code in ((200, 429) if code == 200 else (404,))
    # le client ne peut pas partager le numéro d'une conversation qui n'est pas la sienne
    assert login_as(db, world["b"], token="b2").post(f"/api/client/conversations/{conv_id}/share", json={}).status_code == 404
    assert not db.get(Conversation, conv_id).shared


def test_un_professionnel_ne_voit_que_ce_que_son_statut_permet(db, world):
    tech = make_pro(db, "771000015")
    tech.pro.statut = "TECH"
    r = ServiceRequest(ref="BS-ETUDES-1", client_id=world["a"].id, category="etudes", service="Études techniques", building_type="Autre", description="Étude de structure complète pour une villa R+2.",
                       commune="Ngor", land_status="Non précisé", phase="Non précisé", deadline="Sous 1 mois", budget=900_000, state="PUBLISHED", published_at=now())
    db.add(r)
    db.commit()
    c = login_as(db, tech)
    assert c.get(f"/api/pro/requests/{r.id}").status_code == 404  # études de structure : réservées aux ingénieurs et entreprises
    assert r.ref not in [x["ref"] for x in c.get("/api/pro/requests?tout=true").json()["requests"]]
    assert c.post(f"/api/pro/requests/{r.id}/start", json={"body": "Bonjour, je peux vous aider pour cette étude."}).status_code == 404
    unverified = make_pro(db, "771000016", verified=False)
    assert login_as(db, unverified).get("/api/pro/requests").status_code == 403


def test_pas_de_fuite_des_coordonnees_du_client(db, world):
    """Le professionnel ne voit jamais l'e-mail ni le numéro du client tant qu'il ne l'a pas partagé."""
    conv_id = _talk(db, world)
    pro = login_as(db, world["p1"])
    body = pro.get(f"/api/conversations/{conv_id}").text
    assert world["a"].email not in body and "771000011" not in body
    login_as(db, world["a"], token="share-a").post(f"/api/client/conversations/{conv_id}/share", json={})
    assert "771000011" in pro.get(f"/api/conversations/{conv_id}").text  # visible après partage seulement


def test_statut_d_un_paiement_ne_revele_rien_d_autre(db, world):
    from app.models import Payment
    from app.services.pay import service

    monkey = pytest.MonkeyPatch()
    monkey.setattr("app.services.pay.service.PLANS_ACTIVE", True)
    monkey.setattr(settings, "payment_provider", "sim")
    try:
        pay = service.create_checkout(db, world["p1"], "PRO", 1)
    finally:
        monkey.undo()
    out = anon().get(f"/api/payments/{pay.token}").json()
    assert set(out) == {"state", "plan", "planName", "amount", "months", "sandbox", "provider", "payUrl", "until", "claimed"}  # ni identité, ni e-mail
    assert anon().get("/api/payments/inconnu").status_code == 404
    assert db.scalar(select(Payment.id)) is not None


# --- sessions ----------------------------------------------------------------------------------------------------------------

def test_deconnexion_invalide_le_cookie(db, world):
    c = login_as(db, world["a"])
    assert c.get("/api/me").json()["user"]
    c.post("/api/auth/logout")
    assert not c.cookies.get(COOKIE) or c.get("/api/me").json()["user"] is None
    old = TestClient(app, headers=ORIGIN)
    old.cookies.set(COOKIE, f"t-{world['a'].id}")
    assert old.get("/api/me").json()["user"] is None  # l'ancien jeton ne marche plus


def test_session_expiree_ou_compte_suspendu_refuses(db, world):
    c = login_as(db, world["a"])
    s = db.scalar(select(Session))
    s.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert c.get("/api/client/requests").status_code == 401
    s.expires_at = now() + timedelta(hours=1)
    world["a"].status = "SUSPENDED"
    db.commit()
    assert c.get("/api/client/requests").status_code == 401


def test_cookie_de_session_attributs(db, world, monkeypatch):
    world["a"].password_hash = hash_password("Motdepasse-1234")
    db.commit()
    r = anon().post("/api/auth/login", json={"phone": "771000011", "password": "Motdepasse-1234"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie.replace("samesite", "")
    monkeypatch.setattr(settings, "environment", "production")
    cookie = anon().post("/api/auth/login", json={"phone": "771000011", "password": "Motdepasse-1234"}).headers["set-cookie"].lower()
    assert "secure" in cookie  # en production : cookie réservé à https


def test_cookie_inconnu_ou_forge(db):
    c = TestClient(app, headers=ORIGIN)
    for value in ("", "x" * 5000, "' OR 1=1 --", "../../etc/passwd", "\x00"):
        c.cookies.set(COOKIE, value)
        assert c.get("/api/me").status_code == 200 and c.get("/api/me").json()["user"] is None


# --- connexion et mots de passe ----------------------------------------------------------------------------------------------

def test_connexion_limitee_et_sans_fuite(db, world):
    world["a"].password_hash = hash_password("Motdepasse-1234")
    db.commit()
    unknown = anon().post("/api/auth/login", json={"phone": "771999999", "password": "nimportequoi"})
    wrong = anon().post("/api/auth/login", json={"phone": "771000011", "password": "mauvais-mot-de-passe"})
    assert (unknown.status_code, unknown.json()) == (wrong.status_code, wrong.json())  # même réponse : on ne révèle pas les numéros inscrits
    for _ in range(6):
        last = anon().post("/api/auth/login", json={"phone": "771000011", "password": "encore-faux-123"})
    assert last.status_code == 429
    assert anon().post("/api/auth/login", json={"phone": "771000011", "password": "Motdepasse-1234"}).status_code == 429  # même le bon mot de passe est bloqué pendant la pause


def test_reinitialisation_jeton_a_usage_unique_et_sessions_fermees(db, world):
    u = world["a"]
    login_as(db, u, token="old-session")
    token = issue_user_token(db, u, "reset", 1)
    c = anon()
    assert c.get(f"/api/auth/reset/check?token={token}").json()["valid"]
    assert c.post("/api/auth/reset", json={"token": token, "password": "court"}).status_code == 400
    ok = c.post("/api/auth/reset", json={"token": token, "password": "NouveauMotDePasse-77"})
    assert ok.status_code == 200
    assert c.post("/api/auth/reset", json={"token": token, "password": "AutreMotDePasse-88"}).status_code == 410  # déjà utilisé
    assert db.scalar(select(Session)) is None  # toutes les sessions fermées
    assert c.post("/api/auth/login", json={"phone": "771000011", "password": "NouveauMotDePasse-77"}).status_code == 200


def test_jeton_expire_ou_d_un_autre_usage_refuse(db, world):
    u = world["a"]
    token = issue_user_token(db, u, "reset", 1)
    u.token_expires_at = now() - timedelta(minutes=1)
    db.commit()
    assert anon().post("/api/auth/reset", json={"token": token, "password": "NouveauMotDePasse-77"}).status_code == 410
    vtoken = issue_user_token(db, u, "verify", 48)
    assert anon().post("/api/auth/reset", json={"token": vtoken, "password": "NouveauMotDePasse-77"}).status_code == 410  # un lien de confirmation ne réinitialise pas le mot de passe
    assert anon().post("/api/auth/reset", json={"token": "", "password": "NouveauMotDePasse-77"}).status_code == 410


def test_mot_de_passe_oublie_meme_reponse_et_pas_pour_les_admins(db, world):
    adm = User(email="boss@x.test", first_name="B", last_name="S", role="ADMIN", admin_role="SUPER", totp_enabled=True)
    db.add(adm)
    db.commit()
    known = anon().post("/api/auth/forgot", json={"email": world["a"].email}).json()
    unknown = anon().post("/api/auth/forgot", json={"email": "personne@x.test"}).json()
    assert known == unknown
    anon().post("/api/auth/forgot", json={"email": "boss@x.test"})
    db.refresh(adm)
    assert adm.token_hash is None  # les administrateurs ne passent pas par ce lien


def test_connexion_administration_limitee_et_message_unique(db):
    from app.security import new_totp_secret

    adm = User(email="adm@x.test", first_name="A", last_name="D", role="ADMIN", admin_role="SUPER", totp_enabled=True, totp_secret=new_totp_secret(), password_hash=hash_password("Un-Tres-Bon-Mot-De-Passe-1"))
    db.add(adm)
    db.commit()
    bad = [anon().post("/api/admin/login", json={"email": "adm@x.test", "password": "Un-Tres-Bon-Mot-De-Passe-1", "code": "000000"}) for _ in range(6)]
    assert bad[0].status_code == 401 and bad[-1].status_code == 429
    unknown = anon().post("/api/admin/login", json={"email": "nobody@x.test", "password": "x", "code": "123456"})
    assert unknown.status_code in (401, 429)


# --- limites de débit --------------------------------------------------------------------------------------------------------

def test_inscriptions_limitees_par_adresse(db):
    codes = []
    for i in range(12):
        r = anon().post("/api/auth/signup-client", json={"firstName": "A", "lastName": "B", "email": f"u{i}@x.test", "phone": f"77{3000000 + i}", "password": "Motdepasse-1234", "cgu": True})
        codes.append(r.status_code)
    assert 429 in codes and codes.count(200) <= 10


# --- injections et contenus hostiles -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("payload", ["' OR '1'='1", "'; DROP TABLE \"User\"; --", "%' UNION SELECT 1 --", "<script>alert(1)</script>", "{{7*7}}", "../../../etc/passwd"])
def test_parametres_hostiles_ne_provoquent_pas_d_erreur_serveur(db, world, payload):
    admin = _admin_client(db, "SUPER")
    for path in ("/api/admin/dossiers?etat=", "/api/admin/requests?etat=", "/api/admin/reports?voir=", "/api/auth/reset/check?token="):
        assert admin.get(path + payload).status_code < 500, (path, payload)
    pro = login_as(db, world["p1"])
    assert pro.get(f"/api/pro/requests/{payload}").status_code < 500
    assert login_as(db, world["a"]).get(f"/api/client/requests/{payload}").status_code < 500
    assert db.scalar(select(User.id)) is not None  # la table existe toujours


def test_caractere_nul_dans_les_textes(db, world):
    """Le caractère NUL fait échouer PostgreSQL : il doit être refusé proprement, jamais provoquer une erreur 500."""
    conv_id = _talk(db, world)
    a = login_as(db, world["a"])
    r = a.post(f"/api/conversations/{conv_id}/messages", json={"body": "Bonjour\x00 voici un message avec un caractère nul."})
    assert r.status_code < 500
    r = anon().post("/api/auth/signup-client", json={"firstName": "A\x00", "lastName": "B", "email": "nul@x.test", "phone": "773111111", "password": "Motdepasse-1234", "cgu": True})
    assert r.status_code < 500


def test_contenus_html_echappes_dans_les_e_mails(db):
    from app.emails import new_request_pro_mail
    from app.mailer import mail_ctx

    html = new_request_pro_mail(mail_ctx(), "<b>Fatou</b>", id="1", title="<script>alert(1)</script>", commune="<img src=x onerror=alert(1)>", budget=1000).html
    assert "<script>" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_attribution_massive_ignoree(db, world):
    pro = login_as(db, world["p1"])
    before = db.scalar(select(ProProfile).where(ProProfile.user_id == world["p1"].id))
    state, plan, featured = before.state, before.plan, before.featured
    r = pro.put("/api/pro/profile", json={"bio": "Bonjour", "specialties": ["plans"], "visible": True, "plan": "PREMIUM", "state": "VERIFIED", "featured": True, "spotlight": True, "role": "ADMIN", "planUntil": "2099-01-01"})
    assert r.status_code == 200
    db.expire_all()
    after = db.scalar(select(ProProfile).where(ProProfile.user_id == world["p1"].id))
    assert (after.state, after.plan, after.featured, after.spotlight) == (state, plan, featured, False)
    assert db.get(User, world["p1"].id).role == "PRO"


def test_inscription_ne_permet_pas_de_choisir_son_role(db):
    r = anon().post("/api/auth/signup-client", json={"firstName": "A", "lastName": "B", "email": "role@x.test", "phone": "773222222", "password": "Motdepasse-1234", "cgu": True, "role": "ADMIN", "adminRole": "SUPER", "status": "ACTIVE"})
    assert r.status_code == 200
    u = db.scalar(select(User).where(User.email == "role@x.test"))
    assert u.role == "CLIENT" and u.admin_role is None


# --- tâches planifiées et configuration --------------------------------------------------------------------------------------

def test_nettoyage_exige_le_secret(db, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "secret-de-test")
    assert TestClient(app).get("/api/cron/nettoyage").status_code == 401
    assert TestClient(app).get("/api/cron/nettoyage", headers={"Authorization": "Bearer faux"}).status_code == 401
    assert TestClient(app).get("/api/cron/nettoyage", headers={"Authorization": "Bearer secret-de-test"}).status_code == 200
    monkeypatch.setattr(settings, "cron_secret", "")
    assert TestClient(app).get("/api/cron/nettoyage", headers={"Authorization": "Bearer "}).status_code == 401  # secret vide : jamais accepté


GOOD_PROD = {
    "ENVIRONMENT": "production", "DATABASE_URL": "postgresql://u:p@db:5432/buildsen", "CRON_SECRET": "c" * 24, "INTERNAL_API_KEY": "i" * 24,
    "ALLOWED_ORIGINS": "https://www.buildsen.com,https://buildsen.com", "APP_URL": "https://www.buildsen.com", "ADMIN_DEV_CODE": "", "ADMIN_DEV_PASSWORD": "",
    "RESEND_API_KEY": "re_test", "PLANS_ENABLED": "false", "WAVE_GRACE_HOURS": "0",
}


def run_app(**env):
    import os

    code = "from app.main import app\nprint(app.docs_url, app.openapi_url, app.redoc_url)"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/srv", env={**os.environ, **GOOD_PROD, **env})


def test_en_production_documentation_absente():
    r = run_app()
    assert r.stdout.strip() == "None None None", r.stderr[-400:]


@pytest.mark.parametrize("bad", [
    {"ADMIN_DEV_CODE": "041212"}, {"ADMIN_DEV_PASSWORD": "Amadou_dev301030"}, {"CRON_SECRET": ""}, {"CRON_SECRET": "court"}, {"INTERNAL_API_KEY": ""},
    {"ALLOWED_ORIGINS": "http://localhost:3000"}, {"ALLOWED_ORIGINS": "https://www.buildsen.com,http://localhost:3000"}, {"ALLOWED_ORIGINS": ""}, {"APP_URL": "http://localhost:3000"},
    {"RESEND_API_KEY": ""}, {"WAVE_GRACE_HOURS": "2"}, {"PLANS_ENABLED": "true", "PAYMENT_PROVIDER": "sim"},
])
def test_production_refuse_une_configuration_fragile(bad):
    r = run_app(**bad)
    assert r.returncode != 0 and "Configuration de production refusée" in r.stderr, bad


def test_outils_de_test_refuses_en_production(db, world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    c = anon()
    for method, path in (("get", "/api/wa/sim/contacts"), ("post", "/api/wa/sim/login"), ("post", "/api/wa/sim/scenario"), ("post", "/api/wa/sim/reset"), ("post", "/api/payments/sim/x/paid"), ("get", "/api/wa/sim/messages?phone=771000011")):
        r = c.request(method.upper(), path, **({"json": {"phone": "771000011", "name": "depart"}} if method == "post" else {}))
        assert r.status_code == 404, path


def test_en_tetes_de_securite_et_pas_de_cache(db):
    r = TestClient(app).get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


def test_erreurs_sans_details_techniques(db):
    c = anon()
    r = c.post("/api/auth/login", content=b"{pas du json", headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422) and "Traceback" not in r.text and "File \"" not in r.text
    r = c.post("/api/auth/login", content=b"[1,2,3]", headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)
    assert "Traceback" not in c.get("/api/client/requests/%00").text


def test_adresse_du_visiteur_non_falsifiable(db, monkeypatch):
    """Une adresse inventée en tête de X-Forwarded-For ne doit pas permettre de contourner les limites par adresse."""
    from starlette.requests import Request

    from app.throttle import client_ip

    def req(xff):
        return Request({"type": "http", "headers": [(b"x-forwarded-for", xff.encode())] if xff else [], "client": ("10.0.0.9", 1)})

    monkeypatch.setattr(settings, "trust_proxy", True)
    monkeypatch.setattr(settings, "proxy_hops", 1)
    assert client_ip(req("1.2.3.4, 203.0.113.7")) == "203.0.113.7"  # la valeur ajoutée par notre relais, pas celle du visiteur
    assert client_ip(req("203.0.113.7")) == "203.0.113.7"
    monkeypatch.setattr(settings, "proxy_hops", 2)
    assert client_ip(req("1.2.3.4, 203.0.113.7, 198.51.100.2")) == "203.0.113.7"
    assert client_ip(req("")) == "10.0.0.9"
    monkeypatch.setattr(settings, "trust_proxy", False)
    assert client_ip(req("1.2.3.4")) == "10.0.0.9"


def test_envois_de_fichiers_et_voix_limites(db, world, monkeypatch):
    from app import storage

    monkeypatch.setattr(storage, "LOCAL_DIR", __import__("pathlib").Path("/tmp/buildsen-test-storage"))
    c = login_as(db, world["p1"])
    codes = [c.post("/api/uploads/sign", json={"kind": "portfolio", "mime": "image/png", "size": 100}).status_code for _ in range(62)]
    assert codes[0] == 200 and codes[-1] == 429


# --- Paiements par lien, avis, photos : contrôles d'accès ----------------------------------------------------------------------------

def test_avis_paiements_et_tarifs_selon_les_roles(db):
    csv_body = {"csv": "Montant;Identifiant de transaction\n5000;T_AAA111AAA"}
    for role, import_ok, pricing_ok, reviews_ok in (("SUPER", True, True, True), ("SUPPORT", True, False, True), ("VERIF", False, False, False), ("MODER", False, False, True)):
        db.query(User).filter(User.email == "admin@x.test").delete()
        db.commit()
        c = _admin_client(db, role)
        assert (c.post("/api/admin/payments-import", json=csv_body).status_code != 403) is import_ok, role
        assert (c.post("/api/admin/pricing", json={"prices": {"PRO": 6000}}).status_code != 403) is pricing_ok, role
        assert (c.get("/api/admin/reviews").status_code != 403) is reviews_ok, role


def test_avis_secret_du_webhook_freine_les_essais(db, monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "wave")
    monkeypatch.setattr(settings, "wave_pay_link", "https://pay.wave.com/m/M_sn_test/c/sn/")
    monkeypatch.setattr(settings, "wave_webhook_secret", "le-vrai-secret-long")
    codes = {anon().post("/api/payments/webhook/wave", json={"amount": 5000}, headers={"X-Webhook-Secret": f"essai-{i}"}).status_code for i in range(70)}
    assert 429 in codes and 200 not in codes  # aucun essai ne passe, et on est freiné


def test_garde_fous_de_production(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    for k, v in (("cron_secret", "x" * 20), ("internal_api_key", "y" * 20), ("allowed_origins", "https://a.sn"), ("app_url", "https://a.sn"),
                 ("admin_dev_code", ""), ("admin_dev_password", ""), ("resend_api_key", "re_x"), ("plans_enabled", False), ("wave_grace_hours", 0)):
        monkeypatch.setattr(settings, k, v)
    assert settings.production_problems() == []
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "wave_grace_hours", 2)
    monkeypatch.setattr(settings, "plans_enabled", True)
    monkeypatch.setattr(settings, "payment_provider", "sim")  # le paiement simulé est interdit en production : aucun moyen actif
    problems = " ".join(settings.production_problems())
    assert "RESEND_API_KEY" in problems and "WAVE_GRACE_HOURS" in problems and "aucun moyen de paiement" in problems


def test_admin_sans_double_authentification(db, monkeypatch):
    from app.models import User
    from app.security import hash_password

    u = User(email="sans2fa@test.sn", first_name="A", last_name="B", role="ADMIN", admin_role="SUPER", status="ACTIVE",
             password_hash=hash_password("Un-mot-de-passe-long-1"), totp_enabled=False, totp_secret="X" * 32)
    db.add(u)
    db.commit()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    body = {"email": "sans2fa@test.sn", "password": "Un-mot-de-passe-long-1"}
    assert c.post("/api/admin/login", json=body).status_code == 401  # double authentification active (par défaut) : un compte non enrôlé ne passe pas
    monkeypatch.setattr(settings, "admin_2fa", False)
    assert c.post("/api/admin/login", json={**body, "password": "mauvais-mot-de-passe-1"}).status_code == 401  # le mot de passe reste exigé
    assert c.post("/api/admin/login", json=body).status_code == 200
