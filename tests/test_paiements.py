"""Formules, limites de contacts et paiement : par l'assistant WhatsApp, par le site et par la notification du fournisseur."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_whatsapp import (
    PRO,
    fake_voice,  # noqa: F401  (voix simulée pour tous les tests)
    last,
    make_client,
    make_pro,
    msg,
    outbox,
    publish_request,
)

from app.config import settings
from app.models import Conversation, Payment, ProProfile, Session, User
from app.services.pay import manual, service
from app.utils import add_months, now


@pytest.fixture
def plans_on(monkeypatch):
    for mod in ("app.services.pay.service", "app.services.wa.bot", "app.services.alerts", "app.services.wa.scenarios"):
        monkeypatch.setattr(f"{mod}.PLANS_ACTIVE", True)
    monkeypatch.setattr(settings, "payment_provider", "sim")
    return True


def three_contacts(db):
    """Un client, un pro (formule gratuite) et trois demandes auxquelles le pro répond : la limite du mois est atteinte."""
    make_client(db)
    pro = make_pro(db)
    for i in range(3):
        r = publish_request(db)
        msg(db, PRO, button=f"req:{r.id}:reply")
        msg(db, PRO, f"Bonjour, je peux faire vos plans sous trois semaines, proposition numéro {i}.")
    assert db.scalar(select(func.count()).select_from(Conversation)) == 3
    return pro


def pay_link(db):
    return db.scalar(select(Payment).order_by(Payment.created_at.desc()))


def sim(token, result, origin=None):
    from app.main import app

    return TestClient(app, headers={"Origin": origin or settings.origins[0]}).post(f"/api/payments/sim/{token}/{result}")


def test_formules_inactives_aucune_limite(db):
    make_client(db)
    make_pro(db)
    for i in range(4):
        r = publish_request(db)
        msg(db, PRO, button=f"req:{r.id}:reply")
        msg(db, PRO, f"Bonjour, je peux faire vos plans sous trois semaines, proposition numéro {i}.")
    assert db.scalar(select(func.count()).select_from(Conversation)) == 4


def test_quatrieme_contact_gratuit_bloque_et_propose_une_formule(db, plans_on):
    three_contacts(db)
    r = publish_request(db)
    msg(db, PRO, button=f"req:{r.id}:reply")
    m = last(db, PRO)
    assert "3 contacts du mois" in m.body and "formule Gratuit" in m.body
    assert {b["id"] for b in m.payload["buttons"]} == {"plan:PRO", "plan:PREMIUM", "menu"}
    assert db.scalar(select(func.count()).select_from(Conversation)) == 3
    assert "touchez de nouveau" in m.body
    msg(db, PRO, "Bonjour, un message tapé après le refus ne doit être relayé à personne.")  # pas de conversation en cours
    assert "Envoyé" not in last(db, PRO).body


def test_contact_deja_ouvert_ne_compte_pas_deux_fois(db, plans_on):
    three_contacts(db)
    conv = db.scalar(select(Conversation).order_by(Conversation.created_at))
    msg(db, PRO, button=f"req:{conv.request_id}:reply")  # déjà en conversation avec ce client : pas bloqué
    assert "contacts du mois" not in last(db, PRO).body


def test_paiement_par_whatsapp_active_la_formule_puis_debloque(db, plans_on):
    pro = three_contacts(db)
    r = publish_request(db)
    msg(db, PRO, button=f"req:{r.id}:reply")
    msg(db, PRO, button="plan:PRO")
    pay = pay_link(db)
    assert pay.state == "PENDING" and pay.amount == 5000 and pay.source == "wa"
    assert pay.checkout_url in last(db, PRO).body
    db.refresh(pro.pro)
    assert pro.pro.plan == "FREE"  # rien n'est activé tant que le paiement n'est pas confirmé

    res = sim(pay.token, "paid")
    assert res.status_code == 200 and res.json()["state"] == "PAID"
    db.expire_all()
    p = db.get(ProProfile, pro.pro.id)
    assert p.plan == "PRO" and add_months(now(), 1) - p.plan_until < timedelta(minutes=1)
    assert "Paiement reçu" in last(db, PRO).body and "Formule Pro".lower() in last(db, PRO).body.lower()

    until = p.plan_until
    assert sim(pay.token, "paid").json()["state"] == "PAID"  # confirmation en double : rien ne change
    db.expire_all()
    assert db.get(ProProfile, p.id).plan_until == until
    assert len([m for m in outbox(db, PRO) if "Paiement reçu" in m.body]) == 1

    msg(db, PRO, button=f"req:{r.id}:reply")  # le quatrième contact passe
    msg(db, PRO, "Bonjour, je peux faire vos plans sous trois semaines, quatrième proposition.")
    assert db.scalar(select(func.count()).select_from(Conversation)) == 4


def test_paiement_echoue_n_active_rien(db, plans_on):
    pro = three_contacts(db)
    msg(db, PRO, button="plan:PREMIUM")
    pay = pay_link(db)
    assert sim(pay.token, "failed").json()["state"] == "FAILED"
    db.expire_all()
    assert db.get(ProProfile, pro.pro.id).plan == "FREE"


def test_renouvellement_prolonge_et_pas_de_retour_en_arriere(db, plans_on):
    pro = make_pro(db)
    p = pro.pro
    for _ in range(2):
        pay = service.create_checkout(db, pro, "PRO", 1)
        sim(pay.token, "paid")
        db.expire_all()
    p = db.get(ProProfile, p.id)
    assert abs((p.plan_until - add_months(now(), 2)).total_seconds()) < 120  # deux mois cumulés
    pay = service.create_checkout(db, pro, "PREMIUM", 1)
    sim(pay.token, "paid")
    db.expire_all()
    assert db.get(ProProfile, p.id).plan == "PREMIUM"
    from app.errors import ApiError

    with pytest.raises(ApiError) as e:
        service.create_checkout(db, pro, "PRO", 1)
    assert e.value.status == 409


def test_formule_expiree_redevient_gratuite_et_limitee(db, plans_on):
    pro = three_contacts(db)
    pro.pro.plan, pro.pro.plan_until = "PRO", now() - timedelta(days=3)  # 2 jours de grâce passés
    db.commit()
    r = publish_request(db)
    msg(db, PRO, button=f"req:{r.id}:reply")
    assert "formule Gratuit" in last(db, PRO).body


def test_pro_non_verifie_ou_paiement_ferme_ne_peut_pas_payer(db, plans_on, monkeypatch):
    from app.errors import ApiError

    pro = make_pro(db, verified=False)
    with pytest.raises(ApiError):
        service.create_checkout(db, pro, "PRO", 1)
    ok = make_pro(db, phone="771000093")
    monkeypatch.setattr(settings, "payment_provider", "off")
    with pytest.raises(ApiError):
        service.create_checkout(db, ok, "PRO", 1)


def test_statut_et_aide_sans_formules(db):
    make_client(db)
    make_pro(db)
    msg(db, PRO, "abonnement")
    assert "tout est gratuit" in last(db, PRO).body


def test_statut_avec_formules(db, plans_on):
    make_client(db)
    make_pro(db)
    msg(db, PRO, "abonnement")
    m = last(db, PRO)
    assert "Gratuit" in m.body and "0/3" in m.body and "5\xa0000" in m.body and "15\xa0000" in m.body
    assert "plan:PRO" in {b["id"] for b in m.payload["buttons"]}


# --- notification du fournisseur --------------------------------------------------------------------------------------------

class FakeResp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def paydunya_setup(monkeypatch, status, amount):
    monkeypatch.setattr(settings, "payment_provider", "paydunya")
    monkeypatch.setattr(settings, "paydunya_master_key", "m")
    monkeypatch.setattr(settings, "paydunya_private_key", "p")
    monkeypatch.setattr(settings, "paydunya_token", "t")
    from app.services.pay import provider

    def post(url, **kw):
        assert url.endswith("/checkout-invoice/create") and "sandbox" in url and kw["headers"]["PAYDUNYA-MASTER-KEY"] == "m"
        assert kw["json"]["invoice"]["total_amount"] == 5000
        return FakeResp({"response_code": "00", "response_text": "https://paydunya.test/pay/abc", "token": "pd_tok"})

    monkeypatch.setattr(provider.httpx, "post", post)
    monkeypatch.setattr(provider.httpx, "get", lambda url, **kw: FakeResp({"status": status, "invoice": {"total_amount": amount}}))


def webhook(token):
    from app.main import app

    return TestClient(app).post("/api/payments/webhook/paydunya", data={"data[invoice][token]": token, "data[status]": "completed"})


def test_paydunya_notification_verifiee_aupres_du_fournisseur(db, plans_on, monkeypatch):
    pro = make_pro(db)
    paydunya_setup(monkeypatch, "completed", 5000)
    pay = service.create_checkout(db, pro, "PRO", 1)
    assert pay.token == "pd_tok" and pay.checkout_url == "https://paydunya.test/pay/abc"
    assert webhook("pd_tok").status_code == 200
    db.expire_all()
    assert db.get(ProProfile, pro.pro.id).plan == "PRO"


def test_paydunya_fausse_notification_sans_paiement(db, plans_on, monkeypatch):
    pro = make_pro(db)
    paydunya_setup(monkeypatch, "pending", 5000)
    service.create_checkout(db, pro, "PRO", 1)
    webhook("pd_tok")
    webhook("inconnu")
    db.expire_all()
    assert db.get(ProProfile, pro.pro.id).plan == "FREE" and pay_link(db).state == "PENDING"


def test_paydunya_montant_different_refuse(db, plans_on, monkeypatch):
    pro = make_pro(db)
    paydunya_setup(monkeypatch, "completed", 100)
    service.create_checkout(db, pro, "PRO", 1)
    webhook("pd_tok")
    db.expire_all()
    assert db.get(ProProfile, pro.pro.id).plan == "FREE" and pay_link(db).state == "FAILED"


def test_page_de_retour_verifie_aussi(db, plans_on, monkeypatch):
    from app.main import app

    pro = make_pro(db)
    paydunya_setup(monkeypatch, "completed", 5000)
    service.create_checkout(db, pro, "PRO", 1)
    r = TestClient(app).get("/api/payments/pd_tok")
    assert r.json()["state"] == "PAID" and r.json()["planName"] == "Pro"
    assert TestClient(app).get("/api/payments/nimporte").status_code == 404


# --- site : API du professionnel, sécurité ---------------------------------------------------------------------------------

def pro_client(db, pro):
    from app.main import app
    from app.security import COOKIE, sha256

    token = "tokpro-" + pro.id  # un jeton par professionnel : on peut en connecter plusieurs dans un même test
    db.add(Session(token_hash=sha256(token), user_id=pro.id, mfa=False, expires_at=now() + timedelta(hours=1)))
    db.commit()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    c.cookies.set(COOKIE, token)
    return c


def test_api_site_formule_et_paiement(db, plans_on):
    pro = make_pro(db)
    c = pro_client(db, pro)
    st = c.get("/api/pro/plan").json()
    assert st["plan"] == "FREE" and st["limit"] == 3 and st["used"] == 0 and st["plansActive"] and st["sandbox"]
    assert [o["plan"] for o in st["options"]] == ["PRO", "PREMIUM"]
    assert c.post("/api/pro/checkout", json={"plan": "FREE"}).status_code == 400
    assert c.post("/api/pro/checkout", json={"plan": "PRO", "months": 7}).status_code == 400
    out = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 3}).json()
    assert out["url"].endswith(f"/paiement/simule?ref={out['ref']}")
    assert pay_link(db).amount == 15000
    c.post(f"/api/payments/sim/{out['ref']}/paid")
    assert c.get("/api/pro/plan").json()["plan"] == "PRO"


def test_api_site_limite_atteinte_renvoie_402(db, plans_on):
    pro = three_contacts(db)
    r = publish_request(db)
    c = pro_client(db, pro)
    res = c.post(f"/api/pro/requests/{r.id}/start", json={"body": "Bonjour, je peux faire vos plans sous trois semaines."})
    assert res.status_code == 402 and res.json()["code"] == "PLAN_LIMIT" and "Mon abonnement" in res.json()["error"]


def test_paiement_simule_absent_en_production(db, plans_on, monkeypatch):
    pro = make_pro(db)
    pay = service.create_checkout(db, pro, "PRO", 1)
    monkeypatch.setattr(settings, "environment", "production")
    assert sim(pay.token, "paid").status_code == 404
    db.expire_all()
    assert pay_link(db).state == "PENDING"


def test_telephone_du_meme_reseau_accepte_en_developpement_seulement(db, monkeypatch):
    from app.main import app

    c = TestClient(app)
    assert c.post("/api/wa/sim/reset", json={"phone": "771000090"}, headers={"Origin": "http://192.168.1.20:3000"}).status_code != 403
    assert c.post("/api/wa/sim/reset", json={"phone": "771000090"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/wa/sim/reset", json={"phone": "771000090"}, headers={"Origin": "http://8.8.8.8:3000"}).status_code == 403
    monkeypatch.setattr(settings, "environment", "production")
    assert c.post("/api/auth/logout", headers={"Origin": "http://192.168.1.20:3000"}).status_code == 403


def test_admin_voit_les_paiements(db, plans_on):
    from test_whatsapp import _admin_client

    pro = make_pro(db)
    service.create_checkout(db, pro, "PRO", 1)
    res = _admin_client(db).get("/api/admin/payments").json()
    assert res["payments"][0]["plan"] == "PRO" and res["payments"][0]["state"] == "PENDING"


def test_cartes_et_limites_coherentes():
    from app.rules import PLAN_CONTACTS, PLANS

    assert set(PLAN_CONTACTS) == set(PLANS) and PLAN_CONTACTS["PREMIUM"] is None


# --- comptes de test et tâches planifiées -----------------------------------------------------------------------------------

def test_comptes_de_test_crees_une_fois_et_refuses_en_production(db, monkeypatch, capsys):
    from app import devseed

    monkeypatch.setattr(settings, "admin_dev_password", "")  # sans mot de passe fixe : l'administrateur reçoit un lien d'activation
    assert devseed.main() == 0
    out = capsys.readouterr().out
    assert out.count("mot de passe") == 4 and "/admin/activation?jeton=" in out
    assert db.scalar(select(func.count()).select_from(User)) == 5
    assert devseed.main() == 0 and "existent déjà" in capsys.readouterr().out
    fatou = db.scalar(select(User).where(User.phone == "771000003"))
    assert fatou.pro.plan == "PRO" and fatou.pro.state == "VERIFIED"
    monkeypatch.setattr(settings, "environment", "production")
    assert devseed.main() == 1
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@ep-prod.neon.tech/neondb")
    assert devseed.main() == 1


# --- scénarios du simulateur et connexion en un clic ------------------------------------------------------------------------

def test_scenario_limite_puis_paiement_a_la_main(db, plans_on):
    from app import devseed
    from app.main import app

    devseed.main()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    out = c.post("/api/wa/sim/scenario", json={"name": "limite"}).json()
    assert out["pro"] == "771000004" and "Répondre" in out["next"]
    assert db.scalar(select(func.count()).select_from(Conversation)) == 3
    alert = [m for m in outbox(db, "771000004") if m.kind == "template"][-1]
    reply = next(b["id"] for b in alert.payload["buttons"] if b["id"].endswith(":reply"))
    msg(db, "771000004", button=reply)
    assert {"plan:PRO", "plan:PREMIUM"} <= {b["id"] for b in last(db, "771000004").payload["buttons"]}
    assert c.post("/api/wa/sim/scenario", json={"name": "depart"}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Conversation)) == 0
    assert c.post("/api/wa/sim/scenario", json={"name": "inconnu"}).status_code == 400


def test_connexion_en_un_clic_seulement_pour_les_comptes_de_test(db, monkeypatch):
    from app import devseed
    from app.main import app

    devseed.main()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    ok = c.post("/api/wa/sim/login", json={"phone": "771000003"})
    assert ok.status_code == 200 and ok.json()["redirect"] == "/pro" and c.get("/api/pro/plan").status_code == 200
    real = make_pro(db, phone="771000095")
    real.email = "vrai.pro@gmail.com"
    db.commit()
    assert c.post("/api/wa/sim/login", json={"phone": "771000095"}).status_code == 403
    monkeypatch.setattr(settings, "environment", "production")
    assert c.post("/api/wa/sim/login", json={"phone": "771000003"}).status_code == 404


# --- administrateur de test local : code fixe de développement ------------------------------------------------------------------

def test_code_fixe_de_developpement_jamais_en_production(monkeypatch):
    from app.security import check_totp, new_totp_secret

    secret = new_totp_secret()
    monkeypatch.setattr(settings, "admin_dev_code", "041212")
    assert check_totp(secret, "041212")  # base locale, hors production
    assert not check_totp(secret, "041213")
    monkeypatch.setattr(settings, "environment", "production")
    assert not check_totp(secret, "041212")
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@ep-prod.neon.tech/neondb")
    assert not check_totp(secret, "041212")
    monkeypatch.setattr(settings, "admin_dev_code", "")
    assert not check_totp(secret, "041212")


def test_administrateur_de_test_avec_mot_de_passe_fixe(db, monkeypatch, capsys):
    from app import devseed
    from app.main import app

    monkeypatch.setattr(settings, "admin_dev_password", "Amadou_dev301030")
    monkeypatch.setattr(settings, "admin_dev_code", "041212")
    assert devseed.main() == 0
    assert "/admin/activation" not in capsys.readouterr().out  # déjà activé
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    bad = c.post("/api/admin/login", json={"email": "admin@buildsen.test", "password": "Amadou_dev301030", "code": "000000"})
    assert bad.status_code == 401
    ok = c.post("/api/admin/login", json={"email": "admin@buildsen.test", "password": "Amadou_dev301030", "code": "041212"})
    assert ok.status_code == 200


def test_donnees_de_demonstration_admin(db, monkeypatch):
    from test_whatsapp import _admin_client

    from app import demodata
    from app.main import app
    from app.models import AuditLog, Report, ServiceRequest

    c = _admin_client(db, "SUPER")  # un super-administrateur existe : le journal d'audit de démonstration est alors rempli
    out = demodata.seed(db)
    assert out["pros"] == 7 and out["requests"] == 10
    assert demodata.seed(db) == {"pros": 0, "requests": 0}  # relançable sans doublon
    states = {p.state for p in db.scalars(select(ProProfile)).all()}
    assert states == {"DRAFT", "PENDING", "MORE", "VERIFIED", "REFUSED"}
    assert {r.state for r in db.scalars(select(ServiceRequest)).all()} == {"PENDING", "PUBLISHED", "REJECTED", "CLOSED"}
    assert db.scalar(select(func.count()).select_from(Report)) == 4 and db.scalar(select(func.count()).select_from(AuditLog)) >= 5
    # Le scénario « tout remettre à zéro » garde ces données
    assert len(c.get("/api/admin/payments").json()["payments"]) == 4
    c2 = TestClient(app, headers={"Origin": settings.origins[0]})
    c2.post("/api/wa/sim/scenario", json={"name": "depart"})
    assert db.scalar(select(func.count()).select_from(ServiceRequest)) == 10
    monkeypatch.setattr(settings, "environment", "production")
    assert demodata.main() == 1


def test_rappels_de_fin_de_formule_par_etape_une_seule_fois(db, plans_on, monkeypatch):
    from app.services.pay import renewal

    soon, later, ended, gone = (make_pro(db, f"7710002{i}") for i in range(1, 5))
    for u, delta in ((soon, timedelta(days=2)), (later, timedelta(days=20)), (ended, -timedelta(days=1)), (gone, -timedelta(days=3))):
        u.pro.plan, u.pro.plan_until = "PRO", now() + delta
    db.commit()
    monkeypatch.setattr("app.services.pay.renewal.PLANS_ACTIVE", True)
    mails = []
    monkeypatch.setattr(renewal, "send_mail", lambda to, m: mails.append((to, m.subject)))
    assert renewal.send_reminders(db) == 2
    assert sorted(mails) == sorted([(soon.email, "Votre formule Pro se termine bientôt"), (ended.email, "Votre formule Pro est terminée")])
    assert renewal.send_reminders(db) == 0  # une seule fois par étape
    later.pro.plan_until = now() + timedelta(days=6)
    db.commit()
    assert renewal.send_reminders(db) == 1  # étape « 7 jours »
    monkeypatch.setattr("app.services.pay.renewal.PLANS_ACTIVE", False)
    later.pro.plan_until = now() + timedelta(days=1)
    db.commit()
    assert renewal.send_reminders(db) == 0  # formules inactives : rien


# --- Wave : lien de paiement, activation immédiate, confirmation sans intervention ---------------------------------------------------

LINK = "https://pay.wave.com/m/M_sn_test/c/sn/"


@pytest.fixture
def wave(plans_on, monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "wave")
    monkeypatch.setattr(settings, "wave_pay_link", LINK)
    monkeypatch.setattr(settings, "om_pay_link", "")  # indépendant du .env local
    monkeypatch.setattr(settings, "wave_webhook_secret", "s3cret-wave")
    monkeypatch.setattr(settings, "wave_grace_hours", 0)
    return True


@pytest.fixture
def grace(wave, monkeypatch):
    monkeypatch.setattr(settings, "wave_grace_hours", 24)


def wave_hook(body, secret="s3cret-wave"):
    from app.main import app

    return TestClient(app).post("/api/payments/webhook/wave", json=body, headers={"X-Webhook-Secret": secret} if secret else {})


def test_wave_lien_avec_le_montant_puis_activation_immediate(db, grace):
    pro = make_pro(db)
    c = pro_client(db, pro)
    out = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 3}).json()
    assert out["url"] == f"{LINK}?amount=15000"
    assert c.get(f"/api/payments/{out['ref']}").json()["state"] == "PENDING"  # l'ouverture de la page ne vaut pas paiement
    assert c.get("/api/pro/plan").json()["plan"] == "FREE"
    r = c.post(f"/api/payments/{out['ref']}/claim").json()
    assert r["state"] == "PROVISIONAL" and r["until"]
    assert c.get("/api/pro/plan").json()["plan"] == "PRO"  # activée tout de suite
    # un autre professionnel ne peut pas valider ce paiement
    other = pro_client(db, make_pro(db, phone="0770000099"))
    assert other.post(f"/api/payments/{out['ref']}/claim").status_code == 404


def test_wave_avis_de_paiement_regle_sans_admin(db, wave):
    pro = make_pro(db)
    c = pro_client(db, pro)
    out = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()
    c.post(f"/api/payments/{out['ref']}/claim")
    assert wave_hook({"amount": 5000, "reference": "TX1", "phone": PRO}).json()["matched"] is True
    p = pay_link(db)
    db.refresh(p)
    assert p.state == "PAID"
    st = c.get("/api/pro/plan").json()
    assert st["plan"] == "PRO"
    # même avis une seconde fois : rien ne change
    assert wave_hook({"amount": 5000, "reference": "TX1", "phone": PRO}).json()["matched"] is False


def test_wave_avis_refuse_sans_le_bon_secret(db, wave):
    assert wave_hook({"amount": 5000}, secret="faux").status_code == 404
    assert wave_hook({"amount": 5000}, secret=None).status_code == 404
    assert wave_hook({"amount": "abc"}).status_code == 400


def test_wave_avis_ambigu_ou_montant_inconnu_ne_regle_rien(db, wave):
    a, b = make_pro(db), make_pro(db, phone="0770000088")
    for u in (a, b):
        service.create_checkout(db, u, "PRO", 1)
    assert wave_hook({"amount": 5000}).json()["matched"] is False  # deux paiements identiques : l'équipe tranche
    assert wave_hook({"amount": 4999}).json()["matched"] is False
    assert db.scalar(select(func.count()).select_from(Payment).where(Payment.state == "PAID")) == 0


def test_wave_activation_provisoire_expire_et_retablit_la_formule(db, grace):
    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PREMIUM", "months": 1}).json()["ref"]
    c.post(f"/api/payments/{ref}/claim")
    p = pay_link(db)
    past = now() - timedelta(minutes=1)
    p.data = {**p.data, "provisional_until": past.isoformat()}
    db.get(User, pro.id).pro.plan_until = past  # la formule provisoire arrive à son terme
    db.commit()
    assert manual.expire_provisional(db) == 1
    db.refresh(p)
    db.refresh(pro.pro)
    assert p.state == "EXPIRED" and pro.pro.plan == "FREE"
    # le paiement arrive finalement : la formule est donnée pour de bon
    assert wave_hook({"amount": 15000, "phone": PRO}).json()["matched"] is True
    db.refresh(p)
    assert p.state == "PAID" and c.get("/api/pro/plan").json()["plan"] == "PREMIUM"


def test_wave_une_seule_activation_provisoire_par_periode(db, grace):
    pro = make_pro(db)
    c = pro_client(db, pro)
    r1 = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()["ref"]
    assert c.post(f"/api/payments/{r1}/claim").json()["state"] == "PROVISIONAL"
    r2 = c.post("/api/pro/checkout", json={"plan": "PREMIUM", "months": 1}).json()["ref"]
    assert c.post(f"/api/payments/{r2}/claim").json()["state"] == "PENDING"  # pas de seconde activation avant confirmation


def test_wave_equipe_confirme_ou_refuse(db, wave):
    from test_whatsapp import _admin_client

    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()["ref"]
    c.post(f"/api/payments/{ref}/claim")
    adm = _admin_client(db)
    pid = pay_link(db).id
    assert adm.post(f"/api/admin/payments/{pid}/refuser").status_code == 404
    assert adm.post(f"/api/admin/payments/{pid}/reject").json()["state"] == "FAILED"
    assert c.get("/api/pro/plan").json()["plan"] == "FREE"  # activation provisoire retirée
    assert adm.post(f"/api/admin/payments/{pid}/confirm").status_code == 409
    ref2 = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()["ref"]
    pid2 = db.scalar(select(Payment.id).where(Payment.token == ref2))
    assert adm.post(f"/api/admin/payments/{pid2}/confirm").json()["state"] == "PAID"
    assert c.get("/api/pro/plan").json()["plan"] == "PRO"


def test_tarifs_modifiables_par_la_direction(db, wave):
    from test_whatsapp import _admin_client

    adm = _admin_client(db, "SUPER")
    assert adm.get("/api/admin/pricing").json()["prices"] == {"PRO": 5000, "PREMIUM": 15000}
    for bad in ({"PRO": 10}, {"PRO": "5000"}, {"PRO": True}, {"FREE": 1000}, {"X": 1000}, {}):
        assert adm.post("/api/admin/pricing", json={"prices": bad}).status_code == 400
    assert adm.post("/api/admin/pricing", json={"prices": {"PRO": 7000}}).json()["prices"]["PRO"] == 7000
    pro = make_pro(db)
    c = pro_client(db, pro)
    assert [o["price"] for o in c.get("/api/pro/plan").json()["options"]] == [7000, 15000]
    c.post("/api/pro/checkout", json={"plan": "PRO", "months": 3})
    assert pay_link(db).amount == 21000


def test_wave_bouton_j_ai_paye_ailleurs_que_wave_refuse(db, plans_on):
    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PRO"}).json()["ref"]
    assert c.post(f"/api/payments/{ref}/claim").status_code == 400


def test_wave_par_whatsapp_bouton_j_ai_paye_puis_avis(db, grace):
    pro = make_pro(db)
    msg(db, PRO, button="plan:PRO")
    pay = pay_link(db)
    assert pay.provider == "wave" and f"{LINK}?amount=5000" in last(db, PRO).body
    msg(db, PRO, button=f"paid:{pay.token}")
    db.expire_all()
    assert db.get(ProProfile, pro.pro.id).plan == "PRO" and "active" in last(db, PRO).body
    assert wave_hook({"amount": 5000, "phone": "+221 77 000 00 00"}).json()["matched"] in (True, False)


def test_wave_j_ai_paye_sans_paiement_n_active_rien(db, wave):
    """Faille corrigée : cliquer sur « J'ai payé » sans avoir payé ne donne aucune formule."""
    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()["ref"]
    for _ in range(3):
        assert c.post(f"/api/payments/{ref}/claim").json()["state"] == "PENDING"
    assert c.get("/api/pro/plan").json()["plan"] == "FREE"
    assert c.get(f"/api/payments/{ref}").json()["claimed"] is True


def test_wave_avis_sans_numero_ou_d_un_autre_numero_ne_regle_rien(db, wave):
    pro = make_pro(db)
    c = pro_client(db, pro)
    c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1})
    assert wave_hook({"amount": 5000}).json()["matched"] is False
    assert wave_hook({"amount": 5000, "phone": "0771111111"}).json()["matched"] is False  # le paiement d'un inconnu n'active pas ce compte
    assert c.get("/api/pro/plan").json()["plan"] == "FREE"


def test_wave_numero_wave_different_du_compte(db, wave):
    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()["ref"]
    assert c.post(f"/api/payments/{ref}/claim", json={"phone": "12"}).status_code == 400
    c.post(f"/api/payments/{ref}/claim", json={"phone": "78 111 22 33"})
    assert wave_hook({"amount": 5000, "phone": "+221781112233"}).json()["matched"] is True
    assert c.get("/api/pro/plan").json()["plan"] == "PRO"


# --- Import de l'export Wave, identifiant de transaction ------------------------------------------------------------------------------

def export(*rows):
    return "Date;Description;Montant;Identifiant de transaction\n" + "\n".join(";".join(r) for r in rows)


def test_import_export_confirme_par_numero_et_par_identifiant(db, wave):
    a, b, c = make_pro(db), make_pro(db, phone="771000051"), make_pro(db, phone="771000052")
    ca, cb, cc = (pro_client(db, u) for u in (a, b, c))
    ca.post("/api/pro/checkout", json={"plan": "PRO", "months": 1})
    ref_b = cb.post("/api/pro/checkout", json={"plan": "PRO", "months": 1}).json()["ref"]
    cc.post("/api/pro/checkout", json={"plan": "PREMIUM", "months": 1})
    cb.post(f"/api/payments/{ref_b}/claim", json={"phone": "70 999 88 77", "reference": "T_ABC123XYZ"})
    from test_whatsapp import _admin_client

    adm = _admin_client(db)
    csvtxt = export(
        ("07/11/2026", "Paiement de +221 77 100 00 92", "5 000 F", "T_AAA111AAA"),   # a : par numéro
        ("07/11/2026", "Paiement de 70 999 88 77", "5000", "T_ABC123XYZ"),          # b : par identifiant
        ("07/11/2026", "Paiement de 77 555 55 55", "5000", "T_ZZZ999ZZZ"),          # inconnu
        ("07/11/2026", "Retrait", "-5000", "T_OUT000OUT"),                          # sortie : ignorée
    )
    out = adm.post("/api/admin/payments-import", json={"csv": csvtxt}).json()
    assert sorted(x["who"] for x in out["confirmed"]) == ["Fatou Sarr", "Fatou Sarr"] and out["unmatched"] == 1 and out["ignored"] == 1
    assert ca.get("/api/pro/plan").json()["plan"] == "PRO" and cb.get("/api/pro/plan").json()["plan"] == "PRO"
    assert cc.get("/api/pro/plan").json()["plan"] == "FREE"
    # réimporter le même fichier ne change rien (transaction déjà utilisée)
    again = adm.post("/api/admin/payments-import", json={"csv": csvtxt}).json()
    assert again["confirmed"] == []


def test_import_export_fichier_invalide_et_droits(db, wave):
    from test_whatsapp import _admin_client

    adm = _admin_client(db)
    assert adm.post("/api/admin/payments-import", json={"csv": ""}).status_code == 400
    assert adm.post("/api/admin/payments-import", json={"csv": "a;b\n1;2"}).status_code == 400
    assert adm.post("/api/admin/payments-import", json={}).status_code == 400
    from app.main import app

    assert TestClient(app, headers={"Origin": settings.origins[0]}).post("/api/admin/payments-import", json={"csv": "x"}).status_code == 401


def test_identifiant_de_transaction_invalide_refuse(db, wave):
    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PRO"}).json()["ref"]
    assert c.post(f"/api/payments/{ref}/claim", json={"reference": "ab"}).status_code == 400
    assert c.post(f"/api/payments/{ref}/claim", json={"reference": "<script>"}).status_code == 400
    assert c.post(f"/api/payments/{ref}/claim", json={"reference": "T_ABC123XYZ"}).json()["state"] == "PENDING"


def test_avis_automatique_par_identifiant_sans_numero(db, wave):
    pro = make_pro(db)
    c = pro_client(db, pro)
    ref = c.post("/api/pro/checkout", json={"plan": "PRO"}).json()["ref"]
    c.post(f"/api/payments/{ref}/claim", json={"reference": "T_QWERTY123"})
    assert wave_hook({"amount": 5000, "reference": "T_OTHER9999"}).json()["matched"] is False
    assert wave_hook({"amount": 5000, "reference": "t_qwerty123"}).json()["matched"] is True
    assert c.get("/api/pro/plan").json()["plan"] == "PRO"


def test_delai_de_grace_deux_jours_apres_l_echeance(db, plans_on):
    pro = make_pro(db)
    pro.pro.plan, pro.pro.plan_until = "PRO", now() - timedelta(days=1)
    db.commit()
    assert pro_client(db, pro).get("/api/pro/plan").json()["plan"] == "PRO"  # encore active : il peut renouveler sans coupure


# --- Orange Money : choix du moyen de paiement ----------------------------------------------------------------------------------------

OM = "https://sugu.orange-sonatel.com/mp/abcDEF123"


@pytest.fixture
def links(wave, monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "links")
    monkeypatch.setattr(settings, "om_pay_link", OM)
    return True


def om_hook(body, secret="s3cret-wave"):
    from app.main import app

    return TestClient(app).post("/api/payments/webhook/om", json=body, headers={"X-Webhook-Secret": secret})


def test_om_le_client_choisit_wave_ou_orange_money(db, links):
    pro = make_pro(db)
    c = pro_client(db, pro)
    assert [m["id"] for m in c.get("/api/pro/plan").json()["methods"]] == ["wave", "om"]
    assert c.post("/api/pro/checkout", json={"plan": "PRO", "method": "paypal"}).status_code == 400
    w = c.post("/api/pro/checkout", json={"plan": "PRO", "method": "wave"}).json()
    o = c.post("/api/pro/checkout", json={"plan": "PRO", "method": "om"}).json()
    assert w["url"] == f"{LINK}?amount=5000" and o["url"] == OM  # le lien Orange Money ne porte pas le montant
    st = c.get(f"/api/payments/{o['ref']}").json()
    assert st["provider"] == "om" and st["payUrl"] == OM and st["state"] == "PENDING" and st["amount"] == 5000


def test_om_avis_et_import_ne_melangent_pas_les_moyens(db, links):
    from test_whatsapp import _admin_client

    pro = make_pro(db)
    c = pro_client(db, pro)
    o = c.post("/api/pro/checkout", json={"plan": "PRO", "method": "om"}).json()["ref"]
    c.post(f"/api/payments/{o}/claim")
    assert wave_hook({"amount": 5000, "phone": PRO}).json()["matched"] is False  # avis Wave : ne règle pas un paiement Orange Money
    csvtxt = export(("07/11/2026", "Paiement de 77 100 00 92", "5000", "OM_TX_0001"))
    adm = _admin_client(db)
    assert adm.post("/api/admin/payments-import", json={"csv": csvtxt, "provider": "wave"}).json()["confirmed"] == []
    assert adm.post("/api/admin/payments-import", json={"csv": csvtxt, "provider": "paypal"}).status_code == 400
    assert len(adm.post("/api/admin/payments-import", json={"csv": csvtxt, "provider": "om"}).json()["confirmed"]) == 1
    assert c.get("/api/pro/plan").json()["plan"] == "PRO"


def test_om_avis_automatique_secret_requis(db, links):
    pro = make_pro(db)
    c = pro_client(db, pro)
    c.post(f"/api/payments/{c.post('/api/pro/checkout', json={'plan': 'PRO', 'method': 'om'}).json()['ref']}/claim")
    assert om_hook({"amount": 5000, "phone": PRO}, secret="faux").status_code == 404
    assert om_hook({"amount": 5000, "phone": PRO}).json()["matched"] is True


def test_om_whatsapp_propose_le_choix_puis_le_lien(db, links):
    make_pro(db)
    msg(db, PRO, button="plan:PRO")
    assert pay_link(db) is None and "payer" in last(db, PRO).body.lower()
    msg(db, PRO, button="plan:PRO:om")
    p = pay_link(db)
    assert p.provider == "om" and OM in last(db, PRO).body and "5 000" in last(db, PRO).body.replace("\u202f", " ").replace("\xa0", " ")


def test_abonnement_annuel_avec_dix_pour_cent_de_remise(db, plans_on):
    pro = make_pro(db)
    c = pro_client(db, pro)
    assert c.get("/api/pro/plan").json()["yearDiscount"] == 10
    assert c.post("/api/pro/checkout", json={"plan": "PRO", "months": 6}).status_code == 400
    c.post("/api/pro/checkout", json={"plan": "PRO", "months": 12})
    p = pay_link(db)
    assert p.months == 12 and p.amount == 54000  # 5 000 x 12 = 60 000, moins 10 %
    c.post("/api/pro/checkout", json={"plan": "PREMIUM", "months": 12})
    assert pay_link(db).amount == 162000
    c.post("/api/pro/checkout", json={"plan": "PRO", "months": 3})
    assert pay_link(db).amount == 15000  # pas de remise avant 12 mois
    sim(p.token, "paid")
    db.expire_all()
    until = db.get(User, pro.id).pro.plan_until
    assert add_months(now(), 12) - until < timedelta(minutes=1)


def test_rappel_aux_pros_prioritaires_une_seule_fois(db, plans_on, monkeypatch):
    from app.services import alerts

    make_client(db)
    paid, busy = make_pro(db, "771000301"), make_pro(db, "771000303")
    make_pro(db, "771000302")  # compte gratuit : pas de rappel
    for u in (paid, busy):
        u.pro.plan, u.pro.plan_until = "PRO", now() + timedelta(days=10)
    db.commit()
    r = publish_request(db)
    mails = []
    monkeypatch.setattr(alerts, "send_mail_batch", lambda items: mails.extend(items))
    assert alerts.remind_priority_pros(db) == 0  # trop tôt : moins de 3 h
    r.published_at = now() - timedelta(hours=4)
    db.commit()
    msg(db, "771000303", button=f"req:{r.id}:reply")  # ce pro est déjà en discussion avec le client
    msg(db, "771000303", "Bonjour, je peux faire vos plans sous trois semaines, voici ma proposition.")
    assert alerts.remind_priority_pros(db) == 1
    assert [m[0] for m in mails] == [paid.email] and "Toujours ouverte" in mails[0][1].subject  # ni le compte gratuit, ni celui déjà en discussion
    assert alerts.remind_priority_pros(db) == 0  # une seule fois
