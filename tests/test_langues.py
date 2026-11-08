"""Choix de la langue (français, wolof, anglais) : jamais de mélange dans une discussion."""

import re

from sqlalchemy import select
from test_paiements import fake_voice  # noqa: F401  (voix simulée)
from test_whatsapp import CLIENT, PRO, last, make_client, make_pro, msg, outbox

from app.models import ServiceRequest, WaSession
from app.services.wa import i18n

FRENCH_ONLY = re.compile(r"\b(votre|vos|vous|demande|prénom|merci|bienvenue|professionnel)\b", re.I)
ENGLISH_ONLY = re.compile(r"\b(your|you|request|welcome|thank|please)\b", re.I)


def lang_of(db, phone):
    return (db.get(WaSession, phone).data or {}).get("lang")


def texts_of(db, phone):
    """Messages de l'assistant, sans le choix de langue (volontairement dans les trois langues)."""
    return [m.body for m in outbox(db, phone) if m.kind in ("text", "buttons", "template") and "Tànnal sa làkk" not in m.body and "✅" not in m.body[:25]]


def window_closed(db, phone):
    """Plus de message reçu depuis 24 h : les alertes passent par les messages modèles."""
    db.get(WaSession, phone).last_inbound_at = None
    db.commit()


def test_premier_message_demande_la_langue_avant_tout(db):
    msg(db, CLIENT, "Bonjour", lang=None)
    m = last(db, CLIENT)
    assert [b["title"] for b in m.payload["buttons"]] == ["Français", "Wolof", "English"]
    assert "Welcome" not in m.body and "Bienvenue" not in m.body  # rien avant le choix
    msg(db, CLIENT, "n'importe quoi", lang=None)  # tant qu'on n'a pas choisi, on repose la question
    assert last(db, CLIENT).payload["buttons"][0]["id"] == "lang:fr" and lang_of(db, CLIENT) is None


def test_langue_choisie_en_ecrivant_son_nom(db):
    msg(db, CLIENT, "Bonjour", lang=None)
    msg(db, CLIENT, "english", lang=None)
    assert lang_of(db, CLIENT) == "en" and "Welcome to BUILDSEN" in " ".join(texts_of(db, CLIENT))


def test_discussion_anglaise_sans_francais_ni_wolof(db):
    msg(db, CLIENT, "hello", lang=None)
    msg(db, CLIENT, button="lang:en", lang=None)
    msg(db, CLIENT, button="client")
    msg(db, CLIENT, button="accept")
    msg(db, CLIENT, "Awa Diop")
    for a in ["I need house plans", "a house with a floor", "I want a villa with four bedrooms and a living room.", "Ngor", "600000", "within one month"]:
        msg(db, CLIENT, a)
    recap = last(db, CLIENT)
    assert "Here is your request" in recap.body and "Plans and design" in recap.body and "Within 1 month" in recap.body
    assert [b["title"] for b in recap.payload["buttons"]] == ["Confirm", "Edit", "Cancel"]
    msg(db, CLIENT, "yes")  # confirmation écrite en anglais
    r = db.scalar(select(ServiceRequest))
    assert r and r.category == "plans" and r.budget == 600000 and r.deadline == "Sous 1 mois"
    assert "is published" in last(db, CLIENT).body
    bodies = " ".join(texts_of(db, CLIENT))
    assert not FRENCH_ONLY.search(bodies)  # aucun mot de français
    assert not any(m.kind == "audio" for m in outbox(db, CLIENT))  # pas de voix wolof dans une discussion anglaise


def test_discussion_francaise_sans_voix_wolof_ni_anglais(db):
    msg(db, CLIENT, "Bonjour", lang=None)
    msg(db, CLIENT, button="lang:fr", lang=None)
    msg(db, CLIENT, button="client")
    msg(db, CLIENT, button="accept")
    msg(db, CLIENT, "Awa Diop")
    bodies = " ".join(texts_of(db, CLIENT))
    assert "Bienvenue" in bodies and "Quelle prestation" in bodies and "Dalal" not in bodies and "Ban liggéey" not in bodies
    assert not ENGLISH_ONLY.search(bodies) and not any(m.kind == "audio" for m in outbox(db, CLIENT))


def test_wolof_lit_les_questions_en_wolof_seulement(db):
    msg(db, CLIENT, "Bonjour", lang=None)
    msg(db, CLIENT, button="lang:wo", lang=None)
    msg(db, CLIENT, button="client")
    msg(db, CLIENT, button="accept")
    msg(db, CLIENT, "Awa Diop")
    q = [m.body for m in outbox(db, CLIENT) if "Ban liggéey" in m.body]
    assert q and "Quelle prestation" not in q[0] and any(m.kind == "audio" for m in outbox(db, CLIENT))


def test_changer_de_langue_en_cours_de_route(db):
    make_client(db)
    msg(db, CLIENT, "menu")
    assert "Que voulez-vous faire" in last(db, CLIENT).body
    msg(db, CLIENT, "langue")
    assert [b["id"] for b in last(db, CLIENT).payload["buttons"]] == ["lang:fr", "lang:wo", "lang:en"]
    msg(db, CLIENT, button="lang:en")
    assert lang_of(db, CLIENT) == "en" and "What would you like to do" in last(db, CLIENT).body
    assert [b["title"] for b in last(db, CLIENT).payload["buttons"]] == ["New request", "My requests", "Help"]
    msg(db, CLIENT, "language")
    msg(db, CLIENT, button="lang:wo")
    assert "Lan nga bëgg a def" in last(db, CLIENT).body


def test_une_personne_deja_inscrite_sans_langue_la_choisit_une_fois(db):
    make_client(db)
    msg(db, CLIENT, "Bonjour", lang=None)
    assert last(db, CLIENT).payload["buttons"][1]["id"] == "lang:wo"
    msg(db, CLIENT, button="lang:fr", lang=None)
    assert lang_of(db, CLIENT) == "fr" and "Bonjour Awa" in last(db, CLIENT).body


def test_alerte_au_professionnel_dans_sa_langue(db):
    make_client(db)
    make_pro(db)
    msg(db, PRO, "hello", lang=None)
    msg(db, PRO, button="lang:en", lang=None)
    window_closed(db, PRO)
    from test_whatsapp import publish_request

    r = publish_request(db)
    alert = [m for m in outbox(db, PRO) if m.kind == "template"][-1]
    assert alert.payload["template"] == "nouvelle_demande"  # même modèle Meta, langue « en »
    assert "New request in Ngor: Plans and design" in alert.body
    assert [b["title"] for b in alert.payload["buttons"]] == ["View", "Reply", "Skip"]
    msg(db, PRO, button=f"req:{r.id}:reply")
    assert "Introduce yourself" in last(db, PRO).body


def test_alerte_en_wolof_envoyee_avec_le_modele_francais(db, monkeypatch):
    make_client(db)
    make_pro(db)
    msg(db, PRO, "salaam", lang=None)
    msg(db, PRO, button="lang:wo", lang=None)
    window_closed(db, PRO)
    seen = {}
    from app.services.wa import provider

    real = provider.SimProvider.send_template

    def spy(self, phone, name, params, payloads=None, code=None, lang="fr"):
        seen["lang"] = lang
        return real(self, phone, name, params, payloads, code, lang)

    monkeypatch.setattr(provider.SimProvider, "send_template", spy)
    from test_whatsapp import publish_request

    publish_request(db)
    assert seen["lang"] == "fr"  # Meta n'a pas de wolof : modèle français hors fenêtre
    alert = [m for m in outbox(db, PRO) if m.kind == "template"][-1]
    assert "Nouvelle demande à Ngor" in alert.body  # modèle français, comme annoncé dans WHATSAPP.md


def test_message_vocal_refuse_en_anglais_accepte_en_francais(db):
    make_client(db)
    msg(db, CLIENT, "menu", lang="en")
    msg(db, CLIENT, button="new")
    msg(db, CLIENT, audio=b"RIFFfake")
    assert "Voice notes are understood in French and Wolof only" in last(db, CLIENT).body


def test_erreurs_du_metier_traduites():
    assert i18n.tr_error("en", "Écrivez un message.") == "Write a message."
    assert "xibaar" in i18n.tr_error("wo", "Écrivez un message.")
    assert i18n.tr_error("fr", "Écrivez un message.") == "Écrivez un message."
    assert i18n.tr_error("en", "Cette demande est complète : 5 professionnels ont déjà écrit au client.").startswith("This request is full: 5")
    assert i18n.tr_error("en", "Message inconnu") == "Message inconnu"


def test_catalogue_complet_et_valeurs_traduites():
    for key, row in i18n.C.items():
        assert set(row) == set(i18n.LANGS), key
        import string

        fields = {lg: {f for _, f, _, _ in string.Formatter().parse(row[lg]) if f} for lg in i18n.LANGS if row[lg]}
        assert len({frozenset(v) for v in fields.values()} or [0]) <= 1 or key in {"pro_sent", "a_one", "a_many", "plan_until"}, (key, fields)
    assert i18n.value_label("en", "category", "plans") == "Plans and design"
    assert i18n.value_label("fr", "category", "plans") == "Plans et conception"
    assert i18n.pick("Wolof") == "wo" and i18n.pick("english") == "en" and i18n.pick("bla") is None


def test_connexion_code_dans_la_langue(db):
    u = make_client(db)
    msg(db, CLIENT, "menu", lang="en")
    from app.services.wa import notify

    notify.login_code(db, CLIENT, "123456")
    assert "is your BUILDSEN login code" in last(db, CLIENT).body
    assert u
