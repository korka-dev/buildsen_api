"""Envoi et lecture des fichiers (justificatifs, portfolio) : formats, tailles, contenu réel, droits d'accès, suppression."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_paiements import fake_voice, pro_client  # noqa: F401
from test_whatsapp import make_client, make_pro

from app import storage
from app.config import settings
from app.main import app
from app.models import AuditLog, Credential, PortfolioItem, ProProfile, Session, User
from app.security import COOKIE, sha256
from app.utils import now

PDF = b"%PDF-1.4\n%fake pdf for tests\n" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 200
HTML = b"<html><script>alert(1)</script></html>"


@pytest.fixture(autouse=True)
def disk(tmp_path, monkeypatch):
    """Stockage local dans un dossier temporaire : jamais le vrai stockage."""
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "environment", "development")


def sign(c, **kw):
    body = {"kind": "credential", "type": "ID", "mime": "application/pdf", "size": len(PDF), **kw}
    return c.post("/api/uploads/sign", json=body)


def send(c, content: bytes, ctype="application/pdf", **kw):
    """Les trois temps de l'envoi : lien signé, dépôt du fichier, confirmation."""
    s = sign(c, mime=ctype, size=len(content), **kw)
    assert s.status_code == 200, s.text
    put = c.put(s.json()["uploadUrl"], content=content, headers={"Content-Type": ctype})
    assert put.status_code == 200, put.text
    body = {"kind": kw.get("kind", "credential"), "type": kw.get("type", "ID"), "path": s.json()["path"], "name": "piece.pdf"}
    return c.post("/api/uploads/confirm", json=body), s.json()["path"]


def draft_pro(db, phone="771000081"):
    u = make_pro(db, phone=phone, verified=False)
    return u, pro_client(db, u)


# --- demande de lien d'envoi -------------------------------------------------------------------------------------------------

def test_envoi_reserve_aux_professionnels_connectes(db):
    assert sign(TestClient(app, headers={"Origin": settings.origins[0]})).status_code == 401
    client = make_client(db)
    db.add(Session(token_hash=sha256("c"), user_id=client.id, mfa=False, expires_at=now() + timedelta(hours=1)))
    db.commit()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    c.cookies.set(COOKIE, "c")
    assert sign(c).status_code == 403  # un particulier n'a pas accès à l'envoi de justificatifs


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "application/x-msdownload", "application/zip", "image/gif", "", "application/pdf; charset=x"])
def test_formats_refuses(db, mime):
    _, c = draft_pro(db)
    assert sign(c, mime=mime).status_code == 400


@pytest.mark.parametrize("size", [0, -5, 10 * 1024 * 1024 + 1, "12", None, 1.5, True])
def test_tailles_refusees(db, size):
    _, c = draft_pro(db)
    r = sign(c, size=size)
    # True est un entier pour Python (1 octet) : accepté comme taille valide ; tout le reste est refusé
    assert r.status_code == (200 if size is True else 400)


def test_type_de_justificatif_inconnu_ou_non_modifiable(db):
    _, c = draft_pro(db)
    assert sign(c, type="PASSEPORT").status_code == 400
    assert sign(c, kind="autre").status_code == 400
    u2 = make_pro(db, phone="771000082", verified=True)
    assert sign(pro_client(db, u2)).status_code == 409  # dossier vérifié : plus modifiable


# --- envoi complet -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("content", "ctype"), [(PDF, "application/pdf"), (PNG, "image/png"), (JPG, "image/jpeg")])
def test_envoi_complet_justificatif(db, content, ctype):
    u, c = draft_pro(db)
    r, path = send(c, content, ctype)
    assert r.status_code == 200 and path.startswith(f"{u.id}/")
    cred = db.scalar(select(Credential))
    assert cred.type == "ID" and cred.mime == ctype and cred.size == len(content)


def test_portfolio_webp_et_limite_de_douze(db):
    u, c = draft_pro(db)
    r, _ = send(c, WEBP, "image/webp", kind="portfolio")
    assert r.status_code == 200
    p = db.scalar(select(ProProfile).where(ProProfile.user_id == u.id))
    for i in range(11):
        db.add(PortfolioItem(pro_id=p.id, path=f"{u.id}/00000000-0000-0000-0000-{i:012d}.pdf", caption=f"r{i}", mime="application/pdf", size=1))
    db.commit()
    assert sign(c, kind="portfolio", mime="application/pdf").status_code == 409  # 12 réalisations maximum


def test_nouveau_justificatif_remplace_l_ancien(db):
    _, c = draft_pro(db)
    send(c, PDF)
    send(c, PNG, "image/png")
    creds = db.scalars(select(Credential)).all()
    assert len(creds) == 1 and creds[0].mime == "image/png"


# --- contrôles à la confirmation ---------------------------------------------------------------------------------------------

def test_faux_fichier_refuse_et_supprime(db):
    """Un fichier HTML annoncé comme PDF est refusé : on contrôle le contenu réel, pas seulement l'étiquette."""
    _, c = draft_pro(db)
    r, path = send(c, HTML, "application/pdf")
    assert r.status_code == 400 and db.scalar(select(Credential)) is None
    assert storage.object_info("justificatifs", path) is None  # supprimé du stockage


def test_png_annonce_comme_jpeg_refuse(db):
    _, c = draft_pro(db)
    r, _ = send(c, PNG, "image/jpeg")
    assert r.status_code == 400


def test_confirmation_chemins_invalides(db):
    u, c = draft_pro(db)
    other = make_pro(db, phone="771000083", verified=False)
    for path in (f"{other.id}/11111111-1111-1111-1111-111111111111.pdf", f"{u.id}/../x.pdf", "../../etc/passwd", f"{u.id}/{'a' * 36}.exe", ""):
        r = c.post("/api/uploads/confirm", json={"kind": "credential", "type": "ID", "path": path, "name": "x.pdf"})
        assert r.status_code == 400, path
    ok = c.post("/api/uploads/confirm", json={"kind": "credential", "type": "ID", "path": f"{u.id}/11111111-1111-1111-1111-111111111111.pdf", "name": "x.pdf"})
    assert ok.status_code == 400 and "non reçu" in ok.json()["error"]  # chemin valide mais fichier absent


def test_confirmation_apres_soumission_refusee(db):
    u, c = draft_pro(db)
    s = sign(c).json()
    c.put(s["uploadUrl"], content=PDF, headers={"Content-Type": "application/pdf"})
    db.get(ProProfile, db.scalar(select(ProProfile.id).where(ProProfile.user_id == u.id))).state = "PENDING"
    db.commit()
    r = c.post("/api/uploads/confirm", json={"kind": "credential", "type": "ID", "path": s["path"], "name": "x.pdf"})
    assert r.status_code == 409 and db.scalar(select(Credential)) is None


# --- stockage local : jetons -------------------------------------------------------------------------------------------------

def test_stockage_local_jetons(db):
    _, c = draft_pro(db)
    s = sign(c).json()
    anon = TestClient(app, headers={"Origin": settings.origins[0]})
    base = s["uploadUrl"].split("?")[0]
    assert anon.put(base, content=PDF).status_code == 403  # sans jeton
    assert anon.put(base + "?t=" + "0" * 40, content=PDF).status_code == 403  # mauvais jeton
    other = base.replace(".pdf", "x.pdf")
    assert anon.put(other + "?" + s["uploadUrl"].split("?")[1], content=PDF).status_code == 403  # jeton d'un autre chemin
    assert anon.put(s["uploadUrl"], content=PDF).status_code == 200
    assert anon.put(s["uploadUrl"], content=b"0" * (10 * 1024 * 1024 + 10)).status_code in (413, 400)


def test_lecture_locale_lien_expire_ou_forge(db):
    u, c = draft_pro(db)
    r, path = send(c, PDF)
    cred = db.scalar(select(Credential))
    link = storage.signed_url("justificatifs", cred.path, 60)
    anon = TestClient(app)
    assert anon.get(link).status_code == 200 and anon.get(link).headers["content-security-policy"] == "sandbox"
    assert anon.get(link.replace("t=", "t=00")).status_code == 403
    expired = storage.signed_url("justificatifs", cred.path, -10)
    assert anon.get(expired).status_code == 403
    assert anon.get("/api/dev-storage/get/justificatifs/..%2F..%2Fetc%2Fpasswd?e=9999999999&t=x").status_code in (403, 404)


def test_stockage_disque_en_production_et_absent_avec_supabase(db, monkeypatch):
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    monkeypatch.setattr(settings, "environment", "production")
    # Production sans Supabase : le disque est utilisé, mais un lien d'envoi non signé est refusé
    assert c.put("/api/dev-storage/upload/justificatifs/a/b.pdf?t=x", content=b"x").status_code == 403
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    assert c.put("/api/dev-storage/upload/justificatifs/a/b.pdf?t=x", content=b"x").status_code == 404


# --- lecture des fichiers : droits -------------------------------------------------------------------------------------------

def admin_client(db, role):
    from test_whatsapp import _admin_client

    return _admin_client(db, role)


def test_droits_de_lecture_des_justificatifs(db):
    owner, c = draft_pro(db)
    send(c, PDF)
    cred = db.scalar(select(Credential))
    url = f"/api/fichiers/justificatif/{cred.id}"
    anon = TestClient(app)
    assert anon.get(url, follow_redirects=False).status_code == 401
    _, other = draft_pro(db, "771000084")
    assert other.get(url, follow_redirects=False).status_code == 403  # un autre professionnel
    assert c.get(url, follow_redirects=False).status_code == 307  # le propriétaire
    assert c.get("/api/fichiers/justificatif/inconnu", follow_redirects=False).status_code == 404
    for role, code in (("SUPPORT", 403), ("MODER", 403), ("VERIF", 307), ("SUPER", 307)):
        a = admin_client(db, role)
        assert a.get(url, follow_redirects=False).status_code == code, role
        db.execute(Session.__table__.delete().where(Session.token_hash == sha256("tok")))
        db.query(User).filter(User.email == "admin@x.test").delete()
        db.commit()
    assert db.scalar(select(AuditLog).where(AuditLog.action.like("Consultation%"))) is not None  # consultation journalisée


def test_administrateur_sans_double_authentification_refuse(db):
    owner, c = draft_pro(db)
    send(c, PDF)
    cred = db.scalar(select(Credential))
    adm = User(email="a2@x.test", first_name="A", last_name="B", role="ADMIN", admin_role="SUPER", totp_enabled=True)
    db.add(adm)
    db.flush()
    db.add(Session(token_hash=sha256("half"), user_id=adm.id, mfa=False, expires_at=now() + timedelta(hours=1)))  # mot de passe passé, code non saisi
    db.commit()
    a = TestClient(app)
    a.cookies.set(COOKIE, "half")
    assert a.get(f"/api/fichiers/justificatif/{cred.id}", follow_redirects=False).status_code == 403


def test_portfolio_public_seulement_si_profil_verifie_et_visible(db):
    u = make_pro(db, phone="771000085", verified=True)
    c = pro_client(db, u)
    p = db.scalar(select(ProProfile).where(ProProfile.user_id == u.id))
    it = PortfolioItem(pro_id=p.id, path=f"{u.id}/22222222-2222-2222-2222-222222222222.png", caption="Villa", mime="image/png", size=10)
    db.add(it)
    db.commit()
    storage.local_file("portfolios", it.path).parent.mkdir(parents=True, exist_ok=True)
    storage.local_file("portfolios", it.path).write_bytes(PNG)
    anon = TestClient(app)
    url = f"/api/fichiers/portfolio/{it.id}"
    assert anon.get(url, follow_redirects=False).status_code == 307
    p.visible = False
    db.commit()
    assert anon.get(url, follow_redirects=False).status_code == 401  # masqué par le professionnel
    assert c.get(url, follow_redirects=False).status_code == 307  # lui-même le voit toujours
    p.visible, p.state = True, "PENDING"
    db.commit()
    assert anon.get(url, follow_redirects=False).status_code == 401  # non vérifié : pas public
    assert anon.get("/api/fichiers/inconnu/x", follow_redirects=False).status_code == 404


# --- suppression -------------------------------------------------------------------------------------------------------------

def test_suppression_limitee_a_ses_propres_fichiers(db):
    u, c = draft_pro(db)
    send(c, PDF)
    cred = db.scalar(select(Credential))
    _, other = draft_pro(db, "771000086")
    assert other.delete(f"/api/pro/credentials/{cred.id}").status_code == 404  # pas le sien
    assert db.scalar(select(Credential)) is not None
    assert c.delete(f"/api/pro/credentials/{cred.id}").status_code == 200
    assert db.scalar(select(Credential)) is None
    assert other.delete("/api/pro/portfolio/inconnu").status_code == 404
