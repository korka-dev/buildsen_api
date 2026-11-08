"""Avis des clients : règles (après réponse du pro, une seule fois), affichage public, modération."""

from datetime import timedelta

from sqlalchemy import select
from test_paiements import fake_voice, pro_client  # noqa: F401
from test_whatsapp import CLIENT, PRO, _admin_client, make_client, make_pro, msg, publish_request

from app.models import Conversation, Message, Review, Session
from app.security import COOKIE, sha256
from app.utils import now


def client_http(db, user):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    token = "tokcl-" + user.id
    db.add(Session(token_hash=sha256(token), user_id=user.id, mfa=False, expires_at=now() + timedelta(hours=1)))
    db.commit()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    c.cookies.set(COOKIE, token)
    return c


def setup(db, pro_replied=True):
    cl = make_client(db)
    pro = make_pro(db)
    pro.pro.visible = True
    db.commit()
    r = publish_request(db)
    msg(db, PRO, button=f"req:{r.id}:reply")
    msg(db, PRO, "Bonjour, je peux faire vos plans sous trois semaines, voici ma proposition détaillée.")
    conv = db.scalar(select(Conversation))
    if not pro_replied:  # cas limite : aucun message du professionnel dans la conversation
        for m in db.scalars(select(Message).where(Message.conversation_id == conv.id)).all():
            db.delete(m)
        db.commit()
    return cl, pro, conv


def test_avis_seulement_apres_la_reponse_du_pro_et_une_seule_fois(db):
    cl, pro, conv = setup(db, pro_replied=False)
    c = client_http(db, cl)
    assert c.get(f"/api/conversations/{conv.id}").json()["conversation"]["canReview"] is False
    assert c.post(f"/api/conversations/{conv.id}/review", json={"rating": 5}).status_code == 409  # le pro n'a pas encore répondu
    db.add(Message(conversation_id=conv.id, sender_id=pro.id, body="Bonjour, voici ma proposition."))
    db.commit()
    for bad in (0, 6, "5", True, None, 4.5):
        assert c.post(f"/api/conversations/{conv.id}/review", json={"rating": bad}).status_code == 400
    assert c.post(f"/api/conversations/{conv.id}/review", json={"rating": 5, "comment": "x" * 601}).status_code == 400
    assert c.post(f"/api/conversations/{conv.id}/review", json={"rating": 5, "comment": "Très sérieux, plans livrés à temps."}).status_code == 200
    assert c.post(f"/api/conversations/{conv.id}/review", json={"rating": 1}).status_code == 409  # un seul avis par conversation
    out = c.get(f"/api/conversations/{conv.id}").json()["conversation"]
    assert out["review"]["rating"] == 5 and out["other"]["rating"] == {"avg": 5.0, "count": 1}


def test_avis_refuse_au_pro_a_un_autre_client_et_aux_visiteurs(db):
    cl, pro, conv = setup(db)
    other = make_client(db, "771000077", "Moussa", "Ba")
    assert client_http(db, other).post(f"/api/conversations/{conv.id}/review", json={"rating": 5}).status_code == 404
    assert pro_client(db, pro).post(f"/api/conversations/{conv.id}/review", json={"rating": 5}).status_code in (401, 403, 404)
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    assert TestClient(app, headers={"Origin": settings.origins[0]}).post(f"/api/conversations/{conv.id}/review", json={"rating": 5}).status_code == 401


def test_avis_visible_sur_la_fiche_publique_et_masquable(db):
    cl, pro, conv = setup(db)
    c = client_http(db, cl)
    c.post(f"/api/conversations/{conv.id}/review", json={"rating": 4, "comment": "Bon travail."})
    from fastapi.testclient import TestClient

    from app.main import app

    fiche = TestClient(app).get(f"/api/public/pros/{pro.pro.id}").json()
    assert fiche["pro"]["rating"] == {"avg": 4.0, "count": 1}
    assert fiche["reviews"][0]["author"] == "Awa D." and fiche["reviews"][0]["comment"] == "Bon travail."  # jamais le nom complet
    adm = _admin_client(db, "MODER")
    rid = db.scalar(select(Review.id))
    assert adm.get("/api/admin/reviews").json()["reviews"][0]["id"] == rid
    assert adm.post(f"/api/admin/reviews/{rid}/hide").json()["state"] == "HIDDEN"
    fiche = TestClient(app).get(f"/api/public/pros/{pro.pro.id}").json()
    assert fiche["pro"]["rating"]["count"] == 0 and fiche["reviews"] == []
    assert adm.post(f"/api/admin/reviews/{rid}/delete").status_code == 404
    assert adm.post(f"/api/admin/reviews/{rid}/show").json()["state"] == "VISIBLE"
    assert CLIENT and True
