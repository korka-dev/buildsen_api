"""Actions de l'administration : décisions sur les dossiers, retrait de demandes, formules, suppression, équipe, annonces, signalements."""

import pytest
from sqlalchemy import select
from test_paiements import fake_voice  # noqa: F401
from test_whatsapp import _admin_client, make_client, make_pro

from app.models import Ad, AuditLog, Credential, Payment, ProProfile, Report, ServiceRequest, User
from app.utils import now


def pro_with_docs(db, phone="771000101", state="PENDING", statut="ING"):
    u = make_pro(db, phone, verified=False)
    p = u.pro
    p.state, p.statut, p.school, p.diploma = state, statut, "ESP", "Ingénieur"
    for t in ("ID", "DIPLOME"):
        db.add(Credential(pro_id=p.id, type=t, path=f"{u.id}/{t}.pdf", file_name=f"{t}.pdf", mime="application/pdf", size=10))
    db.commit()
    return u


def audit_actions(db):
    return [a.action for a in db.scalars(select(AuditLog)).all()]


# --- dossiers ----------------------------------------------------------------------------------------------------------------

def test_decision_sur_un_dossier_complet(db):
    u = pro_with_docs(db)
    c = _admin_client(db, "VERIF")
    url = f"/api/admin/dossiers/{u.pro.id}/decide"
    assert c.post(url, json={"decision": "AUTRE"}).status_code == 400
    assert c.post(url, json={"decision": "REFUSED", "reason": "non"}).status_code == 400  # motif de 5 caractères minimum
    assert c.post(url, json={"decision": "MORE", "reason": "Pièce illisible, merci de renvoyer."}).status_code == 200
    db.refresh(u.pro)
    assert u.pro.state == "MORE" and "illisible" in u.pro.decision_reason
    u.pro.state = "PENDING"
    db.commit()
    assert c.post(url, json={"decision": "VERIFIED"}).status_code == 200
    db.refresh(u.pro)
    assert u.pro.state == "VERIFIED" and u.pro.decided_at and all(cr.state == "ACCEPTED" for cr in u.pro.credentials)
    assert any(a.startswith("Dossier accepté") for a in audit_actions(db))


def test_decision_impossible_dossier_incomplet_ou_inconnu(db):
    u = make_pro(db, "771000102", verified=False)
    c = _admin_client(db, "VERIF")
    assert c.post(f"/api/admin/dossiers/{u.pro.id}/decide", json={"decision": "VERIFIED"}).status_code == 409
    assert c.post("/api/admin/dossiers/inconnu/decide", json={"decision": "VERIFIED"}).status_code == 404


def test_pas_de_validation_avec_un_justificatif_refuse(db):
    u = pro_with_docs(db, "771000103")
    c = _admin_client(db, "VERIF")
    cred = u.pro.credentials[0]
    assert c.post(f"/api/admin/credentials/{cred.id}/decide", json={"accept": False, "reason": "x"}).status_code == 400
    assert c.post(f"/api/admin/credentials/{cred.id}/decide", json={"accept": False, "reason": "Photo floue, illisible"}).status_code == 200
    assert c.post(f"/api/admin/dossiers/{u.pro.id}/decide", json={"decision": "VERIFIED"}).status_code == 409
    assert c.post("/api/admin/credentials/inconnu/decide", json={"accept": True}).status_code == 404


def test_offre_de_lancement_une_seule_fois_et_limitee(db):
    u = pro_with_docs(db, "771000104")
    c = _admin_client(db, "VERIF")
    c.post(f"/api/admin/dossiers/{u.pro.id}/decide", json={"decision": "VERIFIED"})
    db.refresh(u.pro)
    assert u.pro.launch_offer and u.pro.plan == "PRO" and u.pro.plan_until
    until = u.pro.plan_until
    u.pro.state = "PENDING"
    db.commit()
    c.post(f"/api/admin/dossiers/{u.pro.id}/decide", json={"decision": "VERIFIED"})
    db.refresh(u.pro)
    assert u.pro.plan_until == until  # pas de prolongation en revalidant


# --- demandes et signalements ------------------------------------------------------------------------------------------------

def make_request(db, client, ref="BS-A-1"):
    r = ServiceRequest(ref=ref, client_id=client.id, category="plans", service="Plans et conception", building_type="Autre", description="Une description suffisamment longue.", commune="Ngor",
                       land_status="Non précisé", phase="Non précisé", deadline="Sous 1 mois", budget=100_000, state="PUBLISHED", published_at=now())
    db.add(r)
    db.commit()
    return r


def test_retrait_d_une_demande_avec_motif(db):
    r = make_request(db, make_client(db, "771000111"))
    c = _admin_client(db, "MODER")
    assert c.post(f"/api/admin/requests/{r.id}/remove", json={"reason": "no"}).status_code == 400
    assert c.post("/api/admin/requests/inconnue/remove", json={"reason": "Motif suffisant"}).status_code == 409
    assert c.post(f"/api/admin/requests/{r.id}/remove", json={"reason": "Coordonnées dans le texte."}).status_code == 200
    db.refresh(r)
    assert r.state == "REJECTED" and r.reject_reason == "Coordonnées dans le texte."
    assert c.post(f"/api/admin/requests/{r.id}/remove", json={"reason": "Encore un motif"}).status_code == 409  # déjà retirée


def test_signalement_traite_et_compte_suspendu(db):
    a, b = make_client(db, "771000112"), make_client(db, "771000113")
    r = make_request(db, a, "BS-A-2")
    db.add(Report(reporter_id=b.id, request_id=r.id, reason="Demande suspecte et irréaliste."))
    db.commit()
    c = _admin_client(db, "MODER")
    rep = db.scalar(select(Report))
    assert c.post(f"/api/admin/reports/{rep.id}/close", json={}).status_code == 200
    db.refresh(rep)
    assert rep.state == "CLOSED" and rep.handled_at
    assert c.post("/api/admin/reports/inconnu/close", json={}).status_code == 404
    assert c.post(f"/api/admin/users/{a.id}/suspend", json={}).status_code == 200
    db.refresh(a)
    assert a.status == "SUSPENDED"


# --- utilisateurs : formules, suspension, suppression ------------------------------------------------------------------------

def test_attribution_de_formule(db):
    u = make_pro(db, "771000121")
    c = _admin_client(db, "SUPPORT")
    url = f"/api/admin/users/{u.id}/plan"
    for body in ({"plan": "PRO"}, {"plan": "PRO", "months": 0}, {"plan": "PRO", "months": 25}, {"plan": "PRO", "months": "abc"}, {"plan": "ULTRA", "months": 1}, {"plan": ["PRO"], "months": 1}):
        assert c.post(url, json=body).status_code == 400, body
    assert c.post(url, json={"plan": "PREMIUM", "months": 3}).status_code == 200
    db.refresh(u.pro)
    assert u.pro.plan == "PREMIUM" and u.pro.plan_until
    assert c.post(url, json={"plan": "FREE"}).status_code == 200
    client = make_client(db, "771000122")
    assert c.post(f"/api/admin/users/{client.id}/plan", json={"plan": "PRO", "months": 1}).status_code == 400  # les particuliers n'ont pas de formule


def test_un_administrateur_ne_se_suspend_ni_ne_se_supprime_pas(db):
    c = _admin_client(db, "SUPER")
    adm = db.scalar(select(User).where(User.role == "ADMIN"))
    assert c.post(f"/api/admin/users/{adm.id}/suspend", json={}).status_code == 404
    assert c.post(f"/api/admin/users/{adm.id}/delete", json={"confirm": "SUPPRIMER"}).status_code == 404


def test_suppression_demande_confirmation_et_garde_l_historique_des_paiements(db):
    u = make_pro(db, "771000123")
    uid = u.id
    c = _admin_client(db, "SUPER")
    assert c.post(f"/api/admin/users/{u.id}/delete", json={}).status_code == 400
    assert c.post(f"/api/admin/users/{u.id}/delete", json={"confirm": "oui"}).status_code == 400
    db.add(Payment(user_id=u.id, plan="PRO", months=1, amount=5000, provider="sim", token="t-hist", state="PAID", paid_at=now()))
    db.commit()
    r = c.post(f"/api/admin/users/{u.id}/delete", json={"confirm": "SUPPRIMER"})
    assert r.status_code == 409 and db.get(User, u.id) is not None  # a payé : on suspend, on ne supprime pas
    db.query(Payment).delete()
    db.commit()
    assert c.post(f"/api/admin/users/{u.id}/delete", json={"confirm": "SUPPRIMER"}).status_code == 200
    db.expire_all()
    assert db.get(User, uid) is None


# --- équipe, annonces --------------------------------------------------------------------------------------------------------

def test_invitation_d_un_administrateur(db):
    c = _admin_client(db, "SUPER")
    good = {"firstName": "Aminata", "lastName": "Fall", "email": "aminata@x.test", "role": "VERIF"}
    for over in ({"firstName": ""}, {"lastName": ""}, {"email": "pas-un-email"}, {"role": "ROI"}, {"role": ""}):
        assert c.post("/api/admin/team/invite", json={**good, **over}).status_code == 400, over
    r = c.post("/api/admin/team/invite", json=good)
    assert r.status_code == 200 and "/admin/activation?jeton=" in r.json()["link"]
    assert c.post("/api/admin/team/invite", json=good).status_code == 409  # adresse déjà utilisée
    invited = db.scalar(select(User).where(User.email == "aminata@x.test"))
    assert invited.admin_role == "VERIF" and not invited.totp_enabled and invited.password_hash is None  # inactif tant que le lien n'est pas utilisé


def test_activation_administrateur_exige_le_code_et_un_mot_de_passe_solide(db):
    import pyotp

    from app.security import check_totp  # noqa: F401

    c = _admin_client(db, "SUPER")
    link = c.post("/api/admin/team/invite", json={"firstName": "A", "lastName": "B", "email": "act@x.test", "role": "MODER"}).json()["link"]
    token = link.split("jeton=")[1]
    u = db.scalar(select(User).where(User.email == "act@x.test"))
    code = pyotp.TOTP(u.totp_secret).now()
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app, headers={"Origin": "http://localhost:3000"})
    base = {"token": token, "password": "Un-Mot-De-Passe-Solide-1", "password2": "Un-Mot-De-Passe-Solide-1"}
    assert anon.post("/api/admin/activate", json={**base, "code": "000000"}).status_code == 400
    assert anon.post("/api/admin/activate", json={**base, "password": "court", "password2": "court", "code": code}).status_code == 400
    assert anon.post("/api/admin/activate", json={**base, "password2": "different", "code": code}).status_code == 400
    assert anon.post("/api/admin/activate", json={**base, "token": "faux", "code": code}).status_code == 410
    assert anon.post("/api/admin/activate", json={**base, "code": code}).status_code == 200
    assert anon.post("/api/admin/activate", json={**base, "code": code}).status_code == 410  # lien à usage unique


@pytest.mark.parametrize("over", [
    {"title": ""}, {"title": "x" * 71}, {"text": "abc"}, {"text": "x" * 181}, {"href": "http://pas-securise.example"}, {"href": "javascript:alert(1)"},
    {"href": "data:text/html,<script>alert(1)</script>"}, {"href": "https://a b.example"}, {"href": "https://" + "a" * 300},
])
def test_annonces_validations(db, over):
    c = _admin_client(db, "SUPPORT")
    r = c.post("/api/admin/ads", json={"title": "Ciment Dakar", "text": "Offre réservée aux membres.", "linkLabel": "Voir", "href": "https://exemple.sn", **over})
    assert r.status_code == 400, over
    assert db.scalar(select(Ad.id)) is None


def test_annonce_valide_puis_masquee_puis_supprimee(db):
    c = _admin_client(db, "SUPPORT")
    assert c.post("/api/admin/ads", json={"title": "Ciment Dakar", "text": "Offre réservée aux membres.", "href": "https://exemple.sn"}).status_code == 200
    ad = db.scalar(select(Ad))
    assert c.post(f"/api/admin/ads/{ad.id}/toggle", json={}).status_code == 200
    db.refresh(ad)
    assert not ad.active
    assert c.delete(f"/api/admin/ads/{ad.id}").status_code == 200 and db.scalar(select(Ad.id)) is None


def test_journal_en_lecture_seule(db):
    c = _admin_client(db, "VERIF")
    assert c.get("/api/admin/journal").status_code == 200
    for method in ("post", "put", "delete", "patch"):
        assert c.request(method.upper(), "/api/admin/journal").status_code in (404, 405)
    assert db.scalar(select(ProProfile.id)) is None
