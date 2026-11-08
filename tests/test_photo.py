"""Photo du terrain ou croquis sur la demande : envoi par le client, contrôle, lecture réservée."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_avis import client_http
from test_fichiers import JPG, PDF, PNG  # noqa: F401
from test_paiements import fake_voice, pro_client  # noqa: F401
from test_whatsapp import _admin_client, make_client, make_pro

from app import storage
from app.config import settings
from app.main import app
from app.models import ServiceRequest
from app.utils import now

FORM = {
    "category": "plans", "buildingType": "Maison individuelle (plain-pied)", "description": "Maison de quatre chambres avec un grand salon.",
    "commune": "Pikine", "deadline": "Dès que possible", "budget": "600000", "confirm": "on",
}


@pytest.fixture(autouse=True)
def disk(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "environment", "development")


def upload(c, content, ctype="image/jpeg"):
    s = c.post("/api/uploads/sign", json={"kind": "request", "mime": ctype, "size": len(content)})
    if s.status_code != 200:
        return s, None
    assert c.put(s.json()["uploadUrl"], content=content, headers={"Content-Type": ctype}).status_code == 200
    cf = c.post("/api/uploads/confirm", json={"kind": "request", "path": s.json()["path"], "name": "terrain.jpg"})
    return cf, s.json()["path"]


def test_photo_du_terrain_de_bout_en_bout(db):
    cl = make_client(db)
    c = client_http(db, cl)
    cf, path = upload(c, JPG)
    assert cf.status_code == 200 and cf.json()["path"] == path
    assert c.post("/api/client/requests", json={**FORM, "photo": path}).status_code == 200
    r = db.scalar(select(ServiceRequest))
    assert r.photo_path == path and r.photo_mime == "image/jpeg"
    assert c.get(f"/api/client/requests/{r.ref}").json()["request"]["hasPhoto"] is True
    # le client voit sa photo ; un autre client non ; un pro vérifié oui ; un visiteur non
    assert c.get(f"/api/fichiers/demande/{r.id}", follow_redirects=False).status_code == 307
    assert client_http(db, make_client(db, "771000066", "Moussa", "Ba")).get(f"/api/fichiers/demande/{r.id}", follow_redirects=False).status_code == 403
    pro = make_pro(db)
    assert pro_client(db, pro).get(f"/api/fichiers/demande/{r.id}", follow_redirects=False).status_code == 307
    assert TestClient(app).get(f"/api/fichiers/demande/{r.id}", follow_redirects=False).status_code == 401
    assert _admin_client(db, "MODER").get(f"/api/fichiers/demande/{r.id}", follow_redirects=False).status_code == 307


def test_photo_refus_format_faux_fichier_et_chemin_etranger(db):
    cl = make_client(db)
    c = client_http(db, cl)
    assert upload(c, PDF, "application/pdf")[0].status_code == 400  # une photo, pas un PDF
    s, _ = upload(c, b"<html>pas une image</html>")  # type annoncé image/jpeg, contenu faux
    assert s.status_code == 400
    other = make_client(db, "771000067", "Moussa", "Ba")
    _, foreign = upload(client_http(db, other), JPG)
    assert c.post("/api/client/requests", json={**FORM, "photo": foreign}).status_code == 400  # fichier d'un autre client
    assert c.post("/api/client/requests", json={**FORM, "photo": "../../etc/passwd"}).status_code == 400
    assert c.post("/api/client/requests", json={**FORM}).status_code == 200  # la photo reste facultative
    assert db.scalar(select(ServiceRequest).where(ServiceRequest.photo_path.is_not(None))) is None


def test_photo_demande_reservee_aux_clients_et_credentials_aux_pros(db):
    pro = make_pro(db, verified=False)
    pc = pro_client(db, pro)
    assert pc.post("/api/uploads/sign", json={"kind": "request", "mime": "image/jpeg", "size": 100}).status_code == 403
    cl = make_client(db)
    assert client_http(db, cl).post("/api/uploads/sign", json={"kind": "credential", "type": "ID", "mime": "application/pdf", "size": 100}).status_code == 403
    assert timedelta(seconds=1) and now()
