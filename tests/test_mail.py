"""Système d'e-mails : clé Resend, test depuis l'administration, e-mails des événements (demande, avis, rappels de dossier)."""

from datetime import timedelta

from sqlalchemy import select
from test_avis import client_http, setup  # noqa: F401
from test_paiements import fake_voice, pro_client  # noqa: F401
from test_whatsapp import PRO, _admin_client, make_pro, publish_request

from app import emails, mailer
from app.models import ProProfile
from app.services.actions import after_publish
from app.services.alerts import remind_dossiers
from app.utils import now


class Resp:
    def __init__(self, status=200, body="{}"):
        self.status_code, self.text = status, body


def capture(monkeypatch, status=200):
    sent = []
    monkeypatch.setattr(mailer.settings, "resend_api_key", "re_test_key")

    def post(url, **kw):
        j = kw.get("json")
        sent.extend(j if isinstance(j, list) else [j])
        return Resp(status, "refused" if status >= 300 else "{}")

    monkeypatch.setattr(mailer.httpx, "post", post)
    return sent


def test_cle_absente_ou_factice_est_signalee(monkeypatch):
    monkeypatch.setattr(mailer.settings, "resend_api_key", "")
    assert mailer.send_email_detail("a@b.sn", "s", "t") == (False, "RESEND_API_KEY est vide.")
    for fake in ("re_A_REMPLACER", "abc123"):
        monkeypatch.setattr(mailer.settings, "resend_api_key", fake)
        assert "vraie clé" in mailer.key_problem()
    monkeypatch.setattr(mailer.settings, "resend_api_key", "re_vraie_cle")
    assert mailer.key_problem() is None


def test_test_d_envoi_depuis_l_administration(db, monkeypatch):
    c = _admin_client(db, "SUPPORT")
    assert "configured" in c.get("/api/admin/mail").json()
    sent = capture(monkeypatch)
    r = c.post("/api/admin/mail/test").json()
    assert r["ok"] is True and sent and sent[0]["to"] == r["to"]
    assert c.get("/api/admin/mail").json()["last"]["ok"] is True
    capture(monkeypatch, status=403)
    r = c.post("/api/admin/mail/test").json()
    assert r["ok"] is False and "403" in r["error"]
    assert c.get("/api/admin/mail").json()["last"]["ok"] is False
    from fastapi.testclient import TestClient

    from app.main import app
    assert TestClient(app, headers={"Origin": mailer.settings.origins[0]}).post("/api/admin/mail/test").status_code == 401  # réservé à l'équipe


def test_demande_publiee_previent_le_client(db, monkeypatch):
    from test_whatsapp import make_client

    make_client(db)
    r = publish_request(db)
    sent = capture(monkeypatch)
    after_publish(db, r.id)
    assert any("publiée" in (m or {}).get("subject", "") for m in sent)


def test_avis_previent_le_professionnel(db, monkeypatch):
    cl, pro, conv = setup(db)
    c = client_http(db, cl)
    sent = capture(monkeypatch)
    assert c.post(f"/api/conversations/{conv.id}/review", json={"rating": 4, "comment": "Bon travail, merci."}).status_code == 200
    assert [m for m in sent if m["to"] == pro.email and "avis" in m["subject"]]


def test_rappels_de_dossier_incomplet_une_fois_par_etape(db, monkeypatch):
    u = make_pro(db)
    u.pro.state = "DRAFT"
    u.created_at = now() - timedelta(days=2)
    db.commit()
    sent = capture(monkeypatch)
    assert remind_dossiers(db) == 1 and remind_dossiers(db) == 0  # une seule fois pour l'étape « 1 jour »
    assert sent and "profil BUILDSEN" in sent[0]["subject"]
    u.created_at = now() - timedelta(days=5)
    db.commit()
    assert remind_dossiers(db) == 1  # second rappel après 4 jours
    u.pro.state = "VERIFIED"
    db.commit()
    u.created_at = now() - timedelta(days=6)
    assert remind_dossiers(db) == 0  # dossier validé : plus de rappel
    assert db.scalar(select(ProProfile.id).where(ProProfile.user_id == u.id))


def test_modeles_d_e_mail_nouveaux():
    ctx = mailer.mail_ctx()
    for m in (emails.request_published_mail(ctx, "Awa", "Plan R+1", "BS-1", "https://x.sn/a", 5),
              emails.review_received_mail(ctx, "Moussa", 5, "https://x.sn/b"),
              emails.plan_declined_mail(ctx, "Fatou", "Pro", "https://x.sn/c")):
        assert m.subject and "<html" in m.html and "https://x.sn" in m.text
