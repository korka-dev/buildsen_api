"""Assistant WhatsApp : sécurité du webhook, lecture des messages Meta, envoi, dialogue complet, alertes, relais, codes de connexion."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.models import Conversation, ProProfile, ServiceRequest, User, WaCode, WaMessage, WaSession
from app.services.wa import bot, meta_payload, provider, store
from app.services.wa.types import Inbound
from app.utils import cuid, now

CLIENT = "771000091"
PRO = "771000092"


# --- aides ------------------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_voice(monkeypatch):
    """Aucun appel à Soynade : la voix est simulée."""
    monkeypatch.setattr(bot, "prompt_audio", lambda key: b"MP3-" + key.encode())
    monkeypatch.setattr(bot, "say_audio", lambda text: b"MP3")
    monkeypatch.setattr(settings, "whatsapp_provider", "sim")


def msg(db, phone, text=None, button=None, audio=None, mid=None, lang="fr"):
    """Un message reçu. Par défaut la personne a déjà choisi sa langue (lang=None : pas encore, l'assistant la lui demande)."""
    if lang and not (store.get_session(db, phone).data or {}).get("lang"):
        store.save_data(store.get_session(db, phone), lang=lang)
        db.commit()
    if button:
        ib = Inbound(id=mid or "in_" + cuid(), phone=phone, kind="button", button_id=button, text=button)
    elif audio is not None:
        ib = Inbound(id=mid or "in_" + cuid(), phone=phone, kind="audio", audio=audio)
    else:
        ib = Inbound(id=mid or "in_" + cuid(), phone=phone, kind="text", text=text or "")
    bot.handle(db, ib)
    db.commit()


def outbox(db, phone):
    return db.scalars(select(WaMessage).where(WaMessage.phone == phone, WaMessage.direction == "OUT").order_by(WaMessage.created_at, WaMessage.id)).all()


def last(db, phone):
    return outbox(db, phone)[-1]


def make_client(db, phone=CLIENT, first="Awa", last_name="Diop"):
    u = User(email=f"{phone}@x.test", phone=phone, first_name=first, last_name=last_name, role="CLIENT", email_verified_at=now())
    db.add(u)
    db.commit()
    return u


def make_pro(db, phone=PRO, verified=True, specialties=None):
    u = User(email=f"{phone}@x.test", phone=phone, first_name="Fatou", last_name="Sarr", role="PRO", email_verified_at=now())
    db.add(u)
    db.flush()
    db.add(ProProfile(user_id=u.id, statut="ING", state="VERIFIED" if verified else "DRAFT", school="ESP", diploma="Ingénieur", specialties=specialties or []))
    db.commit()
    return u


ANSWERS = ["des plans pour ma maison", "une maison avec un étage", "Je veux une villa avec quatre chambres et un salon.", "Ngor", "600000", "dans un mois"]


def publish_request(db, phone=CLIENT):
    msg(db, phone, "menu")
    msg(db, phone, button="new")
    for a in ANSWERS:
        msg(db, phone, a)
    msg(db, phone, button="confirm")
    return db.scalar(select(ServiceRequest).order_by(ServiceRequest.created_at.desc()))


# --- sécurité du webhook ------------------------------------------------------------------------------------------------

def test_signature_valide_et_invalide(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "secret")
    body = b'{"entry":[]}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert meta_payload.valid_signature(body, good)
    assert not meta_payload.valid_signature(body + b" ", good)
    assert not meta_payload.valid_signature(body, "sha256=00")
    assert not meta_payload.valid_signature(body, None)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    assert not meta_payload.valid_signature(body, good)  # sans secret configuré, tout est refusé


def _meta_on(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_provider", "meta")
    monkeypatch.setattr(settings, "whatsapp_access_token", "token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "secret")
    monkeypatch.setattr(settings, "whatsapp_verify_token", "verif")


def test_webhook_verification_et_reception(monkeypatch, db):
    from app.main import app

    _meta_on(monkeypatch)
    c = TestClient(app)
    assert c.get("/api/wa/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verif", "hub.challenge": "42"}).text == "42"
    assert c.get("/api/wa/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "faux", "hub.challenge": "42"}).status_code == 403

    payload = json.dumps(
        {"entry": [{"changes": [{"value": {"messages": [{"from": "221771000091", "id": "wamid.A", "type": "text", "text": {"body": "Bonjour"}}]}}]}]}
    ).encode()
    sig = "sha256=" + hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    assert c.post("/api/wa/webhook", content=payload, headers={"x-hub-signature-256": "sha256=faux"}).status_code == 403
    assert c.post("/api/wa/webhook", content=payload).status_code == 403
    r = c.post("/api/wa/webhook", content=payload, headers={"x-hub-signature-256": sig, "content-type": "application/json"})
    assert r.status_code == 200
    # Le message signé est traité (en arrière-plan, terminé quand la requête rend la main)
    from app.db import SessionLocal

    with SessionLocal() as s:
        assert s.get(WaMessage, "wamid.A") is not None


def test_webhook_inactif_hors_meta(db):
    from app.main import app

    assert TestClient(app).post("/api/wa/webhook", content=b"{}").status_code == 404


def test_lecture_des_messages_meta():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "221771000091", "profile": {"name": "Awa"}}],
                            "messages": [
                                {"from": "221771000091", "id": "t1", "type": "text", "text": {"body": "Bonjour"}},
                                {
                                    "from": "221771000091",
                                    "id": "a1",
                                    "type": "audio",
                                    "audio": {"id": "media9", "mime_type": "audio/ogg; codecs=opus", "voice": True},
                                },
                                {
                                    "from": "221771000091",
                                    "id": "i1",
                                    "type": "interactive",
                                    "interactive": {"type": "button_reply", "button_reply": {"id": "confirm", "title": "Confirmer"}},
                                },
                                {"from": "221771000091", "id": "b1", "type": "button", "button": {"payload": "req:XYZ:reply", "text": "Répondre"}},
                                {"from": "221771000091", "id": "s1", "type": "sticker", "sticker": {}},
                                {"from": "pas-un-numero", "id": "x1", "type": "text", "text": {"body": "?"}},
                            ],
                            "statuses": [{"id": "wamid.OUT", "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement"}]}],
                        }
                    }
                ]
            }
        ]
    }
    inbound, statuses = meta_payload.parse(payload)
    assert [(i.id, i.kind) for i in inbound] == [("t1", "text"), ("a1", "audio"), ("i1", "button"), ("b1", "button"), ("s1", "other")]
    assert inbound[0].phone == "771000091" and inbound[0].name == "Awa"
    assert inbound[1].media_id == "media9"
    assert inbound[2].button_id == "confirm" and inbound[3].button_id == "req:XYZ:reply"
    assert statuses == [{"id": "wamid.OUT", "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement"}]}]
    assert meta_payload.parse({}) == ([], [])


# --- envoi vers Meta -----------------------------------------------------------------------------------------------------

class _Resp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_envoi_meta_formats(monkeypatch):
    _meta_on(monkeypatch)
    sent = []

    def fake_post(url, **kw):
        sent.append((url, kw))
        return _Resp({"id": "media1"} if url.endswith("/media") else {"messages": [{"id": "wamid.1"}]})

    monkeypatch.setattr(provider.httpx, "post", fake_post)
    p = provider.MetaProvider()
    assert p.send_text("771000091", "Bonjour") == "wamid.1"
    p.send_text("771000091", "Choisir", [("a", "Un titre beaucoup trop long pour WhatsApp"), ("b", "B"), ("c", "C"), ("d", "D")])
    p.send_audio("771000091", b"mp3")
    p.send_template("771000091", "nouvelle_demande", ["Ngor", "Plans", "600 000 FCFA"], ["req:1:view", "req:1:reply"])
    p.send_template("771000091", "code_connexion", ["123456"], code="123456")

    url, kw = sent[0]
    assert url == "https://graph.facebook.com/v21.0/123/messages" and kw["headers"]["Authorization"] == "Bearer token"
    assert kw["json"] == {"messaging_product": "whatsapp", "to": "221771000091", "type": "text", "text": {"body": "Bonjour", "preview_url": False}}
    inter = sent[1][1]["json"]["interactive"]
    assert inter["type"] == "button" and len(inter["action"]["buttons"]) == 3  # 3 boutons au maximum
    assert all(len(b["reply"]["title"]) <= 20 for b in inter["action"]["buttons"])  # titres de 20 caractères au maximum
    assert sent[2][0].endswith("/123/media") and sent[3][1]["json"]["type"] == "audio" and sent[3][1]["json"]["audio"] == {"id": "media1"}
    tpl = sent[4][1]["json"]["template"]
    assert tpl["name"] == "nouvelle_demande" and tpl["language"] == {"code": "fr"}
    assert tpl["components"][0] == {
        "type": "body",
        "parameters": [{"type": "text", "text": "Ngor"}, {"type": "text", "text": "Plans"}, {"type": "text", "text": "600 000 FCFA"}],
    }
    assert tpl["components"][1] == {"type": "button", "sub_type": "quick_reply", "index": "0", "parameters": [{"type": "payload", "payload": "req:1:view"}]}
    code = sent[5][1]["json"]["template"]["components"]
    assert {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "123456"}]} in code


def test_erreur_meta_journalisee_sans_bloquer(monkeypatch, db):
    from app.services.wa import sender

    _meta_on(monkeypatch)

    def refuse(url, **kw):
        r = _Resp({"error": {"message": "Re-engagement message", "code": 131047}})
        r.status_code = 400
        return r

    monkeypatch.setattr(provider.httpx, "post", refuse)
    assert sender.send_text(db, CLIENT, "Bonjour") is False
    row = db.scalar(select(WaMessage).where(WaMessage.phone == CLIENT))
    assert row.status == "failed" and row.payload["code"] == 131047


# --- dialogue : accueil et création de compte ----------------------------------------------------------------------------

def test_accueil_puis_creation_du_compte(db):
    msg(db, CLIENT, "Bonjour", lang=None)
    chooser = last(db, CLIENT)
    assert [b["id"] for b in chooser.payload["buttons"]] == ["lang:fr", "lang:wo", "lang:en"]  # la langue d'abord
    msg(db, CLIENT, button="lang:wo", lang=None)
    welcome = next(m for m in outbox(db, CLIENT) if m.kind == "buttons" and "Dalal ak jamm" in m.body)
    assert [b["id"] for b in welcome.payload["buttons"]] == ["client", "pro"]
    msg(db, CLIENT, button="client")
    assert "sutura" in last(db, CLIENT).body.lower()
    assert db.scalar(select(User).where(User.phone == CLIENT)) is None  # pas de compte avant d'avoir accepté
    msg(db, CLIENT, button="accept")
    msg(db, CLIENT, "Awa")
    assert "sant" in last(db, CLIENT).body  # nom incomplet refusé, en wolof
    msg(db, CLIENT, "Awa Diop")
    u = db.scalar(select(User).where(User.phone == CLIENT))
    assert u and u.role == "CLIENT" and u.password_hash is None and u.email.endswith("@wa.buildsen.invalid") and u.email_verified_at
    assert db.get(WaSession, CLIENT).state == "ASK"
    assert any(m.kind == "audio" for m in outbox(db, CLIENT))  # en wolof, la question est lue en note vocale


def test_refus_des_conditions_ne_cree_rien(db):
    msg(db, CLIENT, "Bonjour")
    msg(db, CLIENT, button="client")
    msg(db, CLIENT, button="decline")
    assert db.scalar(select(User).where(User.phone == CLIENT)) is None
    assert db.get(WaSession, CLIENT).state == "NEW"


def test_professionnel_sans_compte_est_renvoye_au_site(db):
    msg(db, CLIENT, "Bonjour")
    msg(db, CLIENT, button="pro")
    assert "/inscription?profil=pro" in last(db, CLIENT).body


def test_message_en_double_ignore(db):
    msg(db, CLIENT, "Bonjour", mid="dup1")
    n = len(outbox(db, CLIENT))
    msg(db, CLIENT, "Bonjour", mid="dup1")
    assert len(outbox(db, CLIENT)) == n


# --- dialogue : une demande ---------------------------------------------------------------------------------------------

def test_demande_complete_par_ecrit(db):
    make_client(db)
    r = publish_request(db)
    assert r and r.state == "PUBLISHED" and r.category == "plans" and r.commune == "Ngor" and r.budget == 600000 and r.deadline == "Sous 1 mois"
    assert r.service == "Plans et conception" and r.land_status == "Non précisé"  # champs facultatifs
    assert "est publiée" in last(db, CLIENT).body and r.ref in last(db, CLIENT).body


def test_reponse_non_comprise_puis_choix_par_numero(db):
    make_client(db)
    msg(db, CLIENT, "menu")
    msg(db, CLIENT, button="new")
    msg(db, CLIENT, "blabla")
    assert any("Je n'ai pas bien compris" in m.body for m in outbox(db, CLIENT))  # première fois : on redemande
    msg(db, CLIENT, "encore du blabla")
    assert "Choisissez en écrivant le numéro" in last(db, CLIENT).body
    msg(db, CLIENT, "2")  # 2 = Devis et métrés
    assert db.get(WaSession, CLIENT).data["answers"]["category"] == "metres"


def test_modifier_une_reponse_au_recapitulatif(db):
    make_client(db)
    msg(db, CLIENT, "menu")
    msg(db, CLIENT, button="new")
    for a in ANSWERS:
        msg(db, CLIENT, a)
    msg(db, CLIENT, button="edit")
    msg(db, CLIENT, "4")  # la commune
    msg(db, CLIENT, "Pikine")
    assert db.get(WaSession, CLIENT).state == "RECAP" and "Pikine" in last(db, CLIENT).body
    msg(db, CLIENT, button="cancel")
    assert db.scalar(select(ServiceRequest)) is None  # annulée : rien n'est publié


def test_reponse_vocale_comprise_par_la_voix(db, monkeypatch):
    make_client(db)
    monkeypatch.setattr(
        bot, "understand_wav", lambda wav, field: {"heard": "x", "heardWo": "Dama bëgg plan u kër", "value": "plans", "label": "Plans et conception"}
    )
    monkeypatch.setattr(bot, "to_wav", lambda data: data)
    msg(db, CLIENT, "menu")
    msg(db, CLIENT, button="new")
    msg(db, CLIENT, audio=b"RIFFxxxx")
    assert db.get(WaSession, CLIENT).data["answers"]["category"] == "plans"
    assert db.get(WaSession, CLIENT).data["voice"] is True


def test_voix_indisponible_demande_d_ecrire(db, monkeypatch):
    from app.services.voice_core import VoiceError

    make_client(db)

    def boom(wav, field):
        raise VoiceError("L'assistant vocal est momentanément indisponible.", 503)

    monkeypatch.setattr(bot, "understand_wav", boom)
    monkeypatch.setattr(bot, "to_wav", lambda data: data)
    msg(db, CLIENT, "menu")
    msg(db, CLIENT, button="new")
    msg(db, CLIENT, audio=b"RIFFxxxx")
    assert "indisponible" in last(db, CLIENT).body
    assert db.get(WaSession, CLIENT).state == "ASK"  # la conversation continue par écrit


# --- alertes, réponses, relais -------------------------------------------------------------------------------------------

def test_alerte_au_pro_puis_conversation_relayee(db):
    make_client(db)
    pro = make_pro(db)
    r = publish_request(db)
    alert = last(db, PRO)
    assert alert.kind == "template" and "Nouvelle demande à Ngor" in alert.body  # fenêtre fermée : message modèle
    assert {b["id"] for b in alert.payload["buttons"]} == {f"req:{r.id}:view", f"req:{r.id}:reply", f"req:{r.id}:skip"}

    msg(db, PRO, button=f"req:{r.id}:reply")
    msg(db, PRO, "Bonjour")
    assert "20 caractères" in last(db, PRO).body
    msg(db, PRO, "Bonjour, je peux faire vos plans sous trois semaines pour 450 000 FCFA.")
    conv = db.scalar(select(Conversation))
    assert conv and conv.pro_id == pro.id
    got = [m for m in outbox(db, CLIENT) if m.body.startswith("💬")][-1]
    assert "450 000" in got.body and {b["id"] for b in got.payload["buttons"]} == {f"conv:{conv.id}:reply", f"conv:{conv.id}:share"}

    msg(db, CLIENT, "Merci, un exemple de plans ?")  # réponse libre du client : relayée au pro
    assert last(db, CLIENT).body.startswith("Envoyé")
    assert "exemple de plans" in [m for m in outbox(db, PRO) if m.body.startswith("💬")][-1].body

    msg(db, CLIENT, button=f"conv:{conv.id}:share")
    db.refresh(conv)
    assert conv.shared and "a partagé son numéro" in last(db, PRO).body


def test_pro_non_verifie_ne_recoit_pas_d_alerte_et_est_informe(db):
    make_client(db)
    make_pro(db, verified=False)
    publish_request(db)
    assert outbox(db, PRO) == []
    msg(db, PRO, "Bonjour")
    assert "vérification" in last(db, PRO).body


def test_cinq_professionnels_au_maximum(db):
    make_client(db)
    pros = [make_pro(db, phone=f"77100010{i}") for i in range(6)]
    r = publish_request(db)
    for i, p in enumerate(pros[:5]):
        msg(db, p.phone, button=f"req:{r.id}:reply")
        msg(db, p.phone, f"Bonjour, professionnel numéro {i}, je peux faire vos plans rapidement.")
    msg(db, pros[5].phone, button=f"req:{r.id}:reply")
    assert "complète" in last(db, pros[5].phone).body
    assert db.query(Conversation).count() == 5


def test_stop_coupe_les_alertes_start_les_reprend(db):
    make_client(db)
    make_pro(db)
    msg(db, PRO, "stop")
    publish_request(db)
    assert all("Nouvelle demande" not in m.body for m in outbox(db, PRO))
    msg(db, PRO, "start")
    publish_request(db)
    assert any("Nouvelle demande" in m.body for m in outbox(db, PRO))


def test_dans_la_fenetre_de_24h_l_alerte_est_un_message_libre(db):
    make_client(db)
    make_pro(db)
    msg(db, PRO, "Bonjour")  # le pro écrit : la fenêtre s'ouvre
    publish_request(db)
    assert last(db, PRO).kind == "buttons"


def test_interrupteur_d_urgence(db):
    make_client(db)
    make_pro(db)
    store.set_enabled(db, False)
    db.commit()
    msg(db, CLIENT, "Bonjour")
    assert "indisponible" in last(db, CLIENT).body
    store.set_enabled(db, True)
    db.commit()
    assert provider.get_provider().name == "sim"


def test_aucun_e_mail_pour_les_comptes_whatsapp(monkeypatch):
    from app import mailer

    monkeypatch.setattr(mailer.settings, "resend_api_key", "cle")
    called = []
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: called.append(1))
    assert mailer.send_email("771000091@wa.buildsen.invalid", "x", "y") is False
    assert called == []


# --- connexion par code ----------------------------------------------------------------------------------------------------

def test_connexion_par_code_whatsapp(db):
    from app.main import app

    make_client(db)
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    assert c.post("/api/auth/code/request", json={"phone": "77 100 00 91"}).status_code == 200
    sent = last(db, CLIENT)
    code = sent.payload["params"][0]  # le simulateur garde le code pour pouvoir le lire en test
    assert len(code) == 6 and code.isdigit()

    assert c.post("/api/auth/code/login", json={"phone": CLIENT, "code": "000000"}).status_code == 401
    r = c.post("/api/auth/code/login", json={"phone": CLIENT, "code": code})
    assert r.status_code == 200 and r.json()["redirect"] == "/espace"
    assert c.get("/api/me").json()["user"]["id"]  # la session est ouverte
    assert c.post("/api/auth/code/login", json={"phone": CLIENT, "code": code}).status_code == 401  # un code ne sert qu'une fois


def test_code_inconnu_meme_reponse_et_cinq_essais(db):
    from app.main import app

    make_client(db)
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    inconnu = c.post("/api/auth/code/request", json={"phone": "77 999 99 99"})
    connu = c.post("/api/auth/code/request", json={"phone": CLIENT})
    assert inconnu.json() == connu.json()  # ne révèle pas quels numéros ont un compte
    assert db.scalar(select(WaCode).where(WaCode.phone == "779999999")) is None
    for _ in range(5):
        c.post("/api/auth/code/login", json={"phone": CLIENT, "code": "111111"})
    right = last(db, CLIENT).payload["params"][0]
    assert c.post("/api/auth/code/login", json={"phone": CLIENT, "code": right}).status_code == 401  # trop d'essais : le code est brûlé


def test_journal_meta_ne_garde_pas_le_code(monkeypatch, db):
    from app.services.wa import notify

    _meta_on(monkeypatch)
    monkeypatch.setattr(provider.httpx, "post", lambda url, **kw: _Resp({"messages": [{"id": "wamid.C"}]}))
    assert notify.login_code(db, CLIENT, "987654")
    row = db.get(WaMessage, "wamid.C")
    assert "987654" not in row.body and "987654" not in json.dumps(row.payload)


# --- administration ----------------------------------------------------------------------------------------------------------

def _admin_client(db, role="SUPPORT"):
    from datetime import timedelta

    from app.main import app
    from app.models import Session
    from app.security import COOKIE, sha256

    admin = User(email="admin@x.test", phone=None, first_name="Ad", last_name="Min", role="ADMIN", admin_role=role, totp_enabled=True)
    db.add(admin)
    db.flush()
    db.add(Session(token_hash=sha256("tok"), user_id=admin.id, mfa=True, expires_at=now() + timedelta(hours=1)))
    db.commit()
    c = TestClient(app, headers={"Origin": settings.origins[0]})
    c.cookies.set(COOKIE, "tok")
    return c


def test_administration_masque_les_numeros_et_coupe_l_assistant(db):
    make_client(db)
    msg(db, CLIENT, "Bonjour")
    c = _admin_client(db)
    o = c.get("/api/admin/whatsapp").json()
    assert o["enabled"] and o["provider"] == "sim" and o["last24h"]["in"] == 1
    assert all(CLIENT not in m["phone"] for m in o["messages"])  # numéros masqués
    assert c.post("/api/admin/whatsapp/enabled", json={"enabled": False}).json()["enabled"] is False
    msg(db, CLIENT, "Bonjour")
    assert "indisponible" in last(db, CLIENT).body
    assert c.get("/api/admin/whatsapp").json()["enabled"] is False


def test_administration_refusee_sans_le_bon_role(db):
    assert TestClient(__import__("app.main", fromlist=["app"]).app).get("/api/admin/whatsapp").status_code == 401
    assert _admin_client(db, role="VERIF").get("/api/admin/whatsapp").status_code == 403
