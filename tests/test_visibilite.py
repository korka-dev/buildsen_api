"""Règles du cahier des charges selon la formule : gros budgets réservés aux abonnés, Premium mis en avant, statistiques de zone."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_avis import client_http
from test_paiements import fake_voice, pro_client  # noqa: F401
from test_photo import FORM
from test_whatsapp import make_client, make_pro

from app.main import app
from app.models import ServiceRequest
from app.utils import now


@pytest.fixture
def plans(monkeypatch):
    for mod in ("app.rules", "app.routers.public", "app.serializers", "app.services.pay.service", "app.services.wa.bot", "app.services.alerts", "app.services.notifications"):
        monkeypatch.setattr(f"{mod}.PLANS_ACTIVE", True)


def post_request(c, **kw):
    r = c.post("/api/client/requests", json={**FORM, **kw})
    assert r.status_code == 200, r.text
    return r


def test_gros_budget_invisible_au_pro_gratuit_visible_a_l_abonne(db, plans):
    cl = make_client(db)
    free, paid = make_pro(db, "771000401"), make_pro(db, "771000402")
    paid.pro.plan, paid.pro.plan_until = "PRO", now() + timedelta(days=10)
    db.commit()
    c = client_http(db, cl)
    post_request(c, budget="6000000")
    post_request(c, budget="600000", commune="Pikine")
    f, p = pro_client(db, free), pro_client(db, paid)
    lf, lp = f.get("/api/pro/requests").json(), p.get("/api/pro/requests").json()
    assert sorted(r["budget"] for r in lf["requests"]) == [600000]
    assert sorted(r["budget"] for r in lp["requests"]) == [600000, 6000000]
    assert lf["stats"]["bigLocked"] == 1 and lp["stats"]["bigLocked"] == 0
    big = db.query(ServiceRequest).filter(ServiceRequest.budget == 6000000).one()
    assert f.get(f"/api/pro/requests/{big.id}").status_code == 404
    assert p.get(f"/api/pro/requests/{big.id}").status_code == 200
    assert f.post(f"/api/pro/requests/{big.id}/start", json={"body": "Bonjour, je peux faire ce projet dans les délais."}).status_code in (400, 403, 404)
    # la vitrine publique ne montre jamais un gros budget
    assert [r["budget"] for r in TestClient(app).get("/api/public/home").json()["requests"]] == [600000]


def test_gros_budget_ouvert_a_tous_sans_formules_actives(db):
    cl = make_client(db)
    free = make_pro(db, "771000403")
    post_request(client_http(db, cl), budget="6000000")
    assert len(pro_client(db, free).get("/api/pro/requests").json()["requests"]) == 1


def test_premium_en_vedette_et_en_haut_de_la_vitrine(db, plans):
    free, pro, prem = make_pro(db, "771000411"), make_pro(db, "771000412"), make_pro(db, "771000413")
    for u, plan in ((pro, "PRO"), (prem, "PREMIUM")):
        u.pro.plan, u.pro.plan_until = plan, now() + timedelta(days=10)
    for u in (free, pro, prem):
        u.pro.visible = True
    db.commit()
    cards = TestClient(app).get("/api/public/home").json()["pros"]
    assert [c["id"] for c in cards][:3] == [prem.pro.id, pro.pro.id, free.pro.id]
    assert cards[0]["featured"] is True and cards[2]["featured"] is False


def test_statistiques_de_zone_pour_tous(db, plans):
    cl = make_client(db)
    free = make_pro(db, "771000421")
    free.pro.commune = "Pikine"
    db.commit()
    c = client_http(db, cl)
    post_request(c, commune="Pikine")
    post_request(c, commune="Pikine")
    post_request(c, commune="Rufisque")
    st = pro_client(db, free).get("/api/pro/requests").json()["stats"]
    assert st["monthZone"] == 2 and st["commune"] == "Pikine" and st["full"] == 0


def test_statut_architecte_avec_attestation_de_l_ordre(db):
    from app.rules import DOC_TYPES, STATUTS, all_docs, can_do_studies, pro_can_see, required_docs

    assert "ARCH" in STATUTS and "ORDRE" in DOC_TYPES
    assert required_docs("ARCH") == ["ID", "DIPLOME", "ORDRE"] and "ORDRE" in all_docs("ARCH")
    assert not can_do_studies("ARCH")  # les études de structure restent aux ingénieurs et aux entreprises
    assert pro_can_see("ARCH", "plans", "R+2") and pro_can_see("ARCH", "dao", None) and not pro_can_see("ARCH", "etudes", None)
    u = make_pro(db, "771000431", verified=False)
    u.pro.statut = "ARCH"
    db.commit()
    c = pro_client(db, u)
    s = c.post("/api/uploads/sign", json={"kind": "credential", "type": "ORDRE", "mime": "application/pdf", "size": 100})
    assert s.status_code == 200  # l'attestation de l'Ordre est un justificatif accepté pour ce statut
    ing = make_pro(db, "771000432", verified=False)
    assert pro_client(db, ing).post("/api/uploads/sign", json={"kind": "credential", "type": "ORDRE", "mime": "application/pdf", "size": 100}).status_code == 400


def test_types_de_plan_du_cahier_des_charges():
    from app.rules import CATEGORIES

    items = CATEGORIES["plans"]["items"]
    assert "Plan architectural" in items and "Dossier de permis de construire" in items and "Modélisation 3D" in items
    assert "Modification de plans existants" in items and "Plans de béton armé" in CATEGORIES["etudes"]["items"]
