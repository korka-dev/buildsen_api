"""L'assistant WhatsApp : accueil, création de compte, demandes par la voix ou par écrit, réponses des professionnels, messages relayés.

Un message reçu entre par `handle()`. L'étape où en est la personne est gardée dans WaSession (state et data) :

    NEW -> CONSENT -> NAME -> MENU                      nouvelle personne : accueil, conditions, prénom et nom
    MENU -> ASK (6 questions) -> RECAP -> MENU          particulier : décrire un projet (PICK_EDIT pour corriger une réponse)
    MENU -> CLOSE_PICK -> MENU                          particulier : clôturer une demande
    MENU -> PRO_REPLY -> MENU                           professionnel : premier message à un client

Les règles métier (limites, 5 professionnels, messagerie…) sont celles du site : services/actions.py.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from ...audit import audit
from ...config import settings
from ...errors import ApiError
from ...mailer import app_url, notify_team
from ...models import Conversation, ProProfile, ServiceRequest, User, WaMessage, WaSession
from ...rules import MAX_PROS_PER_REQUEST, PLAN_CONTACTS, PLANS, PLANS_ACTIVE, pro_sees
from ...throttle import too_many
from ...utils import fcfa, format_phone, now
from .. import actions
from ..pay import manual
from ..pay import service as pay
from ..queries import conversation_counts
from ..voice_core import VoiceError, prompt_audio, recap_texts, say_audio, transcribe_wav, understand_wav
from ..voice_parse import interpret, norm, parse_confirm
from . import notify, store, texts
from .i18n import pick, tr, tr_error, value_label
from .media import to_wav
from .provider import ProviderError, get_provider
from .sender import send_text, send_voice
from .types import Inbound

log = logging.getLogger("buildsen.wa.bot")

# Mots tapés par les gens, ramenés à une commande
ALIASES = {
    "menu": {"menu", "accueil", "annuler", "retour"},
    "help": {"aide", "help", "?"},
    "new": {"nouvelle demande", "nouvelle", "nouveau projet", "decrire un projet", "decrire mon projet"},
    "mine": {"mes demandes", "demandes", "mes demande"},
    "close": {"cloturer", "cloture", "fermer", "cloturer une demande"},
    "stop": {"stop", "arret", "arreter", "desabonner"},
    "start": {"start", "reprendre", "activer"},
    "plan": {"abonnement", "mon abonnement", "formule", "formules", "ma formule", "payer", "paiement"},
    "lang": {"langue", "langues", "lakk", "language", "languages", "change language", "changer de langue", "choisir la langue"},
    "greet": {
        "hello", "hi", "hey", "salam alaykum", "bonjour", "bonsoir", "salut", "salam", "asalaa maalekum", "salaam aleykum", "nanga def",
        "dalal ak jamm", "ça va", "ca va",
    },
}
ALIASES["menu"] |= {"back", "cancel", "start over"}
ALIASES["help"] |= {"ndimbal"}
ALIASES["new"] |= {"new request", "new project", "describe a project", "describe my project"}
ALIASES["mine"] |= {"my requests", "my request", "samay demande"}
ALIASES["close"] |= {"close a request", "close request", "tej"}
ALIASES["plan"] |= {"plan", "my plan", "subscription", "pay", "payment", "sama abonnement"}
ALIASES["stop"] |= {"unsubscribe"}


def _command(ib: Inbound) -> str:
    if ib.kind == "button":
        return ib.button_id
    t = norm(ib.text)
    for key, words in ALIASES.items():
        if t in words:
            return key
    return ""


def _logged(ib: Inbound) -> str:
    return {"audio": "[note vocale]", "button": f"[bouton] {ib.text or ib.button_id}", "other": "[autre type de message]"}.get(ib.kind, ib.text)


# --------------------------------------------------------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------------------------------------------------------

def handle(db: Db, ib: Inbound) -> None:
    """Traite un message reçu. Les doublons (Meta renvoie un message tant qu'il n'a pas reçu de réponse) sont ignorés."""
    if not settings.wa_on:
        return
    # Limites avant le verrou : too_many() valide la transaction en cours
    if too_many(db, f"wa-in:{ib.phone}", 150, 60):
        log.warning("WhatsApp : trop de messages du même numéro, ignoré")
        return
    store.lock_phone(db, ib.phone)
    if db.get(WaMessage, ib.id):
        return
    db.add(WaMessage(id=ib.id, phone=ib.phone, direction="IN", kind=ib.kind, body=_logged(ib)[:2000]))
    db.flush()
    sess = store.get_session(db, ib.phone)
    sess.last_inbound_at = now()
    chat = Chat(db, ib, sess)
    if not store.enabled(db):
        chat.say(chat.t("maintenance", url=texts.link_site()), force=True)
        return
    cmd = _command(ib)
    if cmd == "stop":
        sess.opted_out = True
        chat.say(chat.t("stop_ok"))
        return
    if cmd == "start":
        sess.opted_out = False
        chat.say(chat.t("start_ok"))
        return
    user = store.find_user(db, ib.phone)
    chat.user = user
    if user:
        sess.user_id = user.id
    # La langue se choisit avant de commencer : on ne mélange jamais le français, le wolof et l'anglais dans une discussion
    if cmd == "lang" or cmd.startswith("lang:") or not (sess.data or {}).get("lang"):
        return chat.choose_language(cmd)
    if not user:
        chat.anonymous(cmd)
    elif user.status != "ACTIVE":
        chat.say(chat.t("suspended"))
    elif user.role == "CLIENT":
        chat.client(cmd)
    else:
        chat.pro(cmd)


def handle_safe(db: Db, ib: Inbound) -> None:
    """Version pour les tâches d'arrière-plan : une erreur ne doit jamais faire échouer la réponse à Meta."""
    try:
        handle(db, ib)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("WhatsApp : erreur de traitement")
        try:
            send_text(db, ib.phone, tr((store.get_session(db, ib.phone).data or {}).get("lang"), "error"))
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()


# --------------------------------------------------------------------------------------------------------------------------
# Conversation avec une personne
# --------------------------------------------------------------------------------------------------------------------------

class Chat:
    def __init__(self, db: Db, ib: Inbound, sess: WaSession):
        self.db, self.ib, self.sess = db, ib, sess
        self.user: User | None = None
        self.lang: str = (sess.data or {}).get("lang") or "fr"

    def t(self, key: str, **kw) -> str:
        return tr(self.lang, key, **kw)

    def err(self, e: ApiError) -> str:
        return tr_error(self.lang, e.message)

    @property
    def menu_buttons(self) -> list[tuple[str, str]]:
        return [("new", self.t("btn_new")), ("mine", self.t("btn_mine")), ("help", self.t("btn_help"))]

    @property
    def pro_menu_buttons(self) -> list[tuple[str, str]]:
        return [("mine", self.t("btn_recent")), ("plan", self.t("btn_plan")), ("help", self.t("btn_help"))]

    # --- envoi -------------------------------------------------------------------------------------------------------
    def say(self, body: str, buttons: list[tuple[str, str]] | None = None, conv_id: str | None = None, force: bool = False) -> None:
        send_text(self.db, self.ib.phone, body, buttons, conv_id=conv_id, force=force)

    def voice_say(self, text: str) -> None:
        """Phrase lue en wolof ; sans effet si la voix n'est pas disponible (le texte a déjà été envoyé)."""
        try:
            send_voice(self.db, self.ib.phone, say_audio(text))
        except VoiceError as e:
            log.warning("WhatsApp : voix indisponible (%s)", e.message)

    def voice_prompt(self, key: str) -> None:
        try:
            send_voice(self.db, self.ib.phone, prompt_audio(key))
        except VoiceError as e:
            log.warning("WhatsApp : voix indisponible (%s)", e.message)

    # --- langue ------------------------------------------------------------------------------------------------------
    def choose_language(self, cmd: str) -> None:
        """Propose les trois langues, ou enregistre celle qui vient d'être touchée ou écrite, puis reprend l'accueil dans cette langue."""
        chosen = cmd.split(":", 1)[1] if cmd.startswith("lang:") else (pick(self.ib.text) if self.ib.kind == "text" else None)
        if chosen in ("fr", "wo", "en"):
            self.save(lang=chosen)
            self.lang = chosen
            self.say(self.t("lang_set"))
            if self.user is None:
                self.go("NEW")
                return self.anonymous("greet")
            if self.user.status == "ACTIVE":
                return self.pro("greet") if self.user.role != "CLIENT" else self.menu(f"{self.t('hello_user', first=self.user.first_name)} {self.t('menu_q')}")
            return
        self.say(tr("fr", "choose_lang"), [("lang:fr", "Français"), ("lang:wo", "Wolof"), ("lang:en", "English")])

    # --- état --------------------------------------------------------------------------------------------------------
    @property
    def data(self) -> dict:
        return dict(self.sess.data or {})

    def save(self, **changes) -> None:
        store.save_data(self.sess, **changes)

    def go(self, state: str, **changes) -> None:
        self.sess.state = state
        self.save(**changes)

    # --- lecture du message ------------------------------------------------------------------------------------------
    def text_or_voice(self, field: str | None) -> tuple[dict | None, str]:
        """Réponse écrite ou parlée. Renvoie ({heard, value, label}, texte) ; ({}, '') si la voix n'a pas pu être lue."""
        ib = self.ib
        if ib.kind == "audio" and self.lang == "en":
            self.say(self.t("voice_en"))
            return None, ""
        if ib.kind == "audio":
            if too_many(self.db, f"wa-voice:{ib.phone}", 40, 60):
                self.say(self.t("voice_heavy"))
                return None, ""
            self.save(voice=True)
            try:
                raw = ib.audio if ib.audio is not None else get_provider().download(ib.media_id)
                wav = to_wav(raw)
                if field is None:
                    return {}, transcribe_wav(wav)
                res = understand_wav(wav, field)
                return res, res.get("heardWo") or res.get("heard") or ""
            except VoiceError:
                self.say(self.t("voice_unavailable"))
                return None, ""
            except ProviderError:
                self.say(self.t("voice_failed"))
                return None, ""
        if ib.kind == "text":
            self.save(voice=None)
            t = ib.text.strip()
            return ({"value": interpret(field, t, t, t)} if field else {}), t
        self.say(self.t("only_text_voice"))
        return None, ""

    # ==========================================================================================================
    # Personne sans compte
    # ==========================================================================================================
    def anonymous(self, cmd: str) -> None:
        s = self.sess
        if s.state == "NAME":
            return self.name_step()
        if s.state == "CONSENT":
            yes = cmd == "accept" or (self.ib.kind == "text" and parse_confirm(self.ib.text) is True)
            no = cmd == "decline" or (self.ib.kind == "text" and parse_confirm(self.ib.text) is False)
            if yes:
                self.go("NAME")
                return self.say(self.t("ask_name"))
            if no:
                self.go("NEW")
                return self.say(self.t("declined"))
            return self.say(self.t("consent", url=app_url("/cgu")), [("accept", self.t("btn_accept")), ("decline", self.t("btn_decline"))])
        # NEW
        if cmd == "pro":
            return self.say(self.t("pro_signup", url=app_url("/inscription?profil=pro")))
        if cmd in ("client", "new"):
            self.go("CONSENT")
            return self.say(self.t("consent", url=app_url("/cgu")), [("accept", self.t("btn_accept")), ("decline", self.t("btn_decline"))])
        self.say(self.t("welcome"), [("client", self.t("btn_client")), ("pro", self.t("btn_pro"))])
        if self.t("voice_welcome"):  # voix en wolof seulement : jamais de wolof dans une discussion en français ou en anglais
            self.voice_say(self.t("voice_welcome"))

    def name_step(self) -> None:
        if self.ib.kind != "text":
            return self.say(self.t("ask_name"))
        parts = self.ib.text.replace(",", " ").split()
        if len(parts) < 2 or any(len(p) > 40 for p in parts) or sum(len(p) for p in parts) < 3 or any(c.isdigit() for c in self.ib.text):
            return self.say(self.t("name_retry"))
        first, last = parts[0], " ".join(parts[1:])
        # Le numéro WhatsApp est déjà vérifié par WhatsApp : pas d'e-mail ni de mot de passe à la création.
        # L'adresse « .invalid » ne reçoit aucun e-mail ; l'identité se prouve par WhatsApp (code de connexion sur le site).
        u = User(
            email=f"{self.ib.phone}@wa.buildsen.invalid",
            phone=self.ib.phone,
            first_name=first[:60],
            last_name=last[:60],
            role="CLIENT",
            email_verified_at=now(),
        )
        self.db.add(u)
        self.db.flush()
        audit(self.db, u.id, "Création d'un compte particulier par WhatsApp", f"conditions acceptées, message {self.ib.id}")
        notify_team(
            self.db, f"Nouveau particulier inscrit par WhatsApp : {u.first_name} {u.last_name[:1]}.", f"{u.first_name} {u.last_name}\n{format_phone(u.phone)}"
        )
        self.user = u
        self.sess.user_id = u.id
        self.say(self.t("welcome_user", first=first))
        self.start_request(first_time=True)

    # ==========================================================================================================
    # Particulier
    # ==========================================================================================================
    def menu(self, intro: str | None = None) -> None:
        self.go("MENU", conv=None)
        self.say(intro or self.t("menu_q"), self.menu_buttons)

    def client(self, cmd: str) -> None:
        s, user = self.sess, self.user
        assert user is not None
        state = s.state if s.state not in ("NEW", "CONSENT", "NAME") else "MENU"
        if state != s.state:
            s.state = state
        if cmd == "menu":
            return self.menu()
        if cmd == "help":
            return self.say(self.t("help", url=app_url()))
        if cmd == "new" and state != "ASK":
            return self.start_request()
        if cmd == "mine":
            return self.list_requests()
        if cmd == "close":
            return self.close_pick()
        if cmd.startswith("conv:"):
            return self.conv_button(cmd)
        if state == "ASK":
            return self.answer_field()
        if state == "RECAP":
            return self.recap_reply(cmd)
        if state == "PICK_EDIT":
            return self.pick_edit()
        if state == "CLOSE_PICK":
            return self.close_reply()
        if self.data.get("conv") and self.ib.kind in ("text", "audio"):
            return self.relay(self.data["conv"], is_client=True)
        self.menu(self.t("hello_user", first=user.first_name) if cmd == "greet" else self.t("not_understood"))

    # --- créer une demande -----------------------------------------------------------------------------------------
    def start_request(self, first_time: bool = False) -> None:
        self.go("ASK", answers={}, step=0, tries=0, choose=False, edit=False, conv=None)
        if not first_time:
            self.say(self.t("intro_again"))
        else:
            self.say(self.t("intro_first"))
            if self.t("voice_intro"):
                self.voice_say(self.t("voice_intro"))
        self.ask()

    def ask(self) -> None:
        d = self.data
        f = texts.FIELDS[d["step"]]
        self.say(texts.question(self.lang, f))
        if self.lang == "wo" and (d.get("voice") or d["step"] == 0):
            self.voice_prompt(f)  # la question est lue en wolof : seulement pour les personnes qui ont choisi le wolof

    def answer_field(self) -> None:
        d = self.data
        f = texts.FIELDS[d["step"]]
        res, heard = self.text_or_voice(f)
        if res is None:
            return  # message déjà envoyé (voix indisponible…)
        value = res.get("value")
        # Réponse par numéro, proposée après deux essais ratés
        opts = texts.options(self.lang, f)
        if d.get("choose") and opts and self.ib.kind == "text" and heard.strip().isdigit():
            i = int(heard.strip())
            value = opts[i - 1][0] if 1 <= i <= len(opts) else None
        if value is None or (f == "description" and len(str(value)) < 20):
            return self.not_understood(f)
        answers = {**d.get("answers", {}), f: str(value)}
        self.save(answers=answers, tries=0, choose=False)
        self.say(self.t("ack", title=texts.title(self.lang, f), value=texts.shown(self.lang, f, str(value))))
        if d.get("edit"):
            return self.recap()
        step = d["step"] + 1
        self.save(step=step)
        if step >= len(texts.FIELDS):
            return self.recap()
        self.ask()

    def not_understood(self, f: str) -> None:
        d = self.data
        tries = d.get("tries", 0) + 1
        self.save(tries=tries)
        if tries == 1:
            self.say(self.t("retry") + (f"\n{self.t('tip_' + f)}" if f in texts.TIPS else ""))
            if self.lang == "wo":
                self.voice_prompt("retry")
            return
        self.save(choose=True)
        if texts.options(self.lang, f):
            self.say(self.t("choose_number") + "\n" + texts.numbered(self.lang, f))
        else:
            self.say(self.t("tip_" + f) if f in texts.TIPS else self.t("write_answer"))

    def recap(self) -> None:
        d = self.data
        self.go("RECAP", edit=False)
        self.say(texts.recap_text(self.lang, d["answers"]), self.recap_buttons)
        if d.get("voice") and self.lang == "wo":
            self.voice_say(recap_texts(d["answers"])[1])

    @property
    def recap_buttons(self) -> list[tuple[str, str]]:
        return [("confirm", self.t("btn_confirm")), ("edit", self.t("btn_edit")), ("cancel", self.t("btn_cancel"))]

    def recap_reply(self, cmd: str) -> None:
        if self.ib.kind == "audio":
            res, _ = self.text_or_voice("confirm")
            if res is None:
                return
            cmd = {True: "confirm", False: "edit"}.get(res.get("value"), "")
        elif self.ib.kind == "text" and not cmd:
            cmd = {True: "confirm", False: "edit"}.get(parse_confirm(self.ib.text), "")
        if cmd == "confirm":
            return self.publish()
        if cmd == "edit":
            self.go("PICK_EDIT")
            lines = "\n".join(f"{i}. {texts.title(self.lang, f)}" for i, f in enumerate(texts.FIELDS, 1))
            return self.say(self.t("pick_edit", lines=lines))
        if cmd == "cancel":
            return self.menu(self.t("cancelled"))
        self.say(self.t("touch_confirm"), self.recap_buttons)

    def pick_edit(self) -> None:
        t = self.ib.text.strip() if self.ib.kind == "text" else ""
        if not (t.isdigit() and 1 <= int(t) <= len(texts.FIELDS)):
            return self.say(self.t("pick_number", n=len(texts.FIELDS)))
        self.go("ASK", step=int(t) - 1, tries=0, choose=False, edit=True)
        self.ask()

    def publish(self) -> None:
        d, user = self.data, self.user
        assert user is not None
        body = {**d.get("answers", {}), "confirm": "on"}  # la personne vient de confirmer sur WhatsApp
        try:
            r = actions.create_service_request(self.db, user, body)
        except ApiError as e:
            self.menu(f"{self.err(e)}\n{self.t('menu_q')}")
            return
        self.go("MENU", answers=None, step=None, tries=None, choose=None, edit=None)
        self.say(texts.published(self.lang, r.ref), self.menu_buttons)
        actions.after_publish(self.db, r.id)

    # --- mes demandes, clôture ----------------------------------------------------------------------------------------
    def list_requests(self) -> None:
        user = self.user
        assert user is not None
        reqs = self.db.scalars(select(ServiceRequest).where(ServiceRequest.client_id == user.id).order_by(ServiceRequest.created_at.desc()).limit(5)).all()
        if not reqs:
            return self.say(self.t("no_requests"), [("new", self.t("btn_new"))])
        counts = conversation_counts(self.db, [r.id for r in reqs])
        lines = [
            f"• {r.ref} · {notify.service_label(self.lang, r)} · {self.t('st_' + r.state)} · "
            + self.t("pro_count", n=counts.get(r.id, 0), max=MAX_PROS_PER_REQUEST)
            for r in reqs
        ]
        self.say(self.t("your_requests") + "\n" + "\n".join(lines), [("new", self.t("btn_new")), ("close", self.t("btn_close"))])

    def close_pick(self) -> None:
        user = self.user
        assert user is not None
        reqs = self.db.scalars(
            select(ServiceRequest)
            .where(ServiceRequest.client_id == user.id, ServiceRequest.state.in_(["PUBLISHED", "PENDING"]))
            .order_by(ServiceRequest.created_at.desc())
            .limit(5)
        ).all()
        if not reqs:
            return self.say(self.t("none_open"))
        self.go("CLOSE_PICK", close=[r.ref for r in reqs])
        lines = "\n".join(f"{i}. {r.ref} · {notify.service_label(self.lang, r)}" for i, r in enumerate(reqs, 1))
        self.say(self.t("close_which", lines=lines))

    def close_reply(self) -> None:
        refs = self.data.get("close", [])
        t = self.ib.text.strip() if self.ib.kind == "text" else ""
        if not (t.isdigit() and 1 <= int(t) <= len(refs)):
            return self.say(self.t("close_number"))
        try:
            actions.close_request(self.db, self.user, refs[int(t) - 1])
        except ApiError as e:
            return self.menu(self.err(e))
        self.menu(self.t("closed_ok", ref=refs[int(t) - 1]))

    # --- messages avec un professionnel ------------------------------------------------------------------------------
    def conv_button(self, cmd: str) -> None:
        _, conv_id, action = cmd.split(":", 2)
        conv = self.db.get(Conversation, conv_id)
        user = self.user
        assert user is not None
        mine = conv and ((user.role == "CLIENT" and conv.request.client_id == user.id) or (user.role == "PRO" and conv.pro_id == user.id))
        if not mine:
            return self.say(self.t("conv_gone"))
        if action == "reply":
            self.go("MENU", conv=conv_id)
            return self.say(self.t("reply_prompt"))
        if action == "share" and user.role == "CLIENT":
            try:
                c, new = actions.share_whatsapp(self.db, user, conv_id)
            except ApiError as e:
                return self.say(self.err(e))
            if new:
                actions.notify_shared(self.db, c.id)
            return self.say(self.t("shared_ok"))
        self.say(self.t("not_understood"))

    def relay(self, conv_id: str, is_client: bool) -> None:
        conv = self.db.get(Conversation, conv_id)
        user = self.user
        assert user is not None
        if not conv or (is_client and conv.request.client_id != user.id) or (not is_client and conv.pro_id != user.id):
            self.save(conv=None)
            return self.say(self.t("conv_gone_menu"))
        res, body = self.text_or_voice(None)
        if res is None:
            return
        if self.ib.kind == "audio":
            if not body:
                return self.say(self.t("heard_nothing"))
            body = f"🎤 {body}"
        try:
            actions.post_chat_message(self.db, conv, user, is_client, body)
        except ApiError as e:
            return self.say(self.err(e))
        self.say(self.t("sent"))
        actions.notify_chat(self.db, conv.id, to_client=not is_client)

    # ==========================================================================================================
    # Professionnel
    # ==========================================================================================================
    def pro(self, cmd: str) -> None:
        user = self.user
        assert user is not None
        p = user.pro
        if not p or p.state != "VERIFIED":
            return self.say(self.t("pro_pending", first=user.first_name, url=f"{settings.app_url}/pro/verification"))
        state = self.sess.state if self.sess.state in ("PRO_REPLY",) else "MENU"
        self.sess.state = state
        if cmd == "menu":
            return self.pro_menu()
        if cmd == "help":
            return self.say(self.t("help", url=app_url()))
        if cmd in ("mine", "new"):
            return self.pro_recent(p)
        if cmd == "plan":
            return self.plan_status(p)
        if cmd.startswith("paid:"):
            return self.plan_claim(cmd.split(":", 1)[1])
        if cmd.startswith("plan:"):
            plan, _, method = cmd.split(":", 1)[1].partition(":")
            return self.plan_checkout(plan, method or None)
        if cmd.startswith("req:"):
            return self.req_button(p, cmd)
        if cmd.startswith("conv:"):
            return self.conv_button(cmd)
        if state == "PRO_REPLY":
            return self.pro_first_message()
        if self.data.get("conv") and self.ib.kind in ("text", "audio"):
            return self.relay(self.data["conv"], is_client=False)
        self.pro_menu(self.t("hello_user", first=user.first_name) if cmd == "greet" else self.t("not_understood"))

    def pro_menu(self, intro: str | None = None) -> None:
        self.go("MENU", conv=None, req=None)
        self.say(intro or self.t("menu_q"), self.pro_menu_buttons)

    def pro_recent(self, p: ProProfile) -> None:
        reqs = self.db.scalars(select(ServiceRequest).where(ServiceRequest.state == "PUBLISHED").order_by(ServiceRequest.published_at.desc()).limit(30)).all()
        mine = [r for r in reqs if pro_sees(p, r) and (not p.specialties or r.category in p.specialties)][:3]
        if not mine:
            return self.say(self.t("none_for_pro"))
        counts = conversation_counts(self.db, [r.id for r in mine])
        for r in mine:
            self.say(self.request_text(r, counts.get(r.id, 0)), [(f"req:{r.id}:reply", self.t("btn_reply")), (f"req:{r.id}:skip", self.t("btn_skip"))])

    def request_text(self, r: ServiceRequest, taken: int) -> str:
        desc = r.description if len(r.description) <= 400 else r.description[:399] + "…"
        return self.t(
            "req_text", title=notify.service_label(self.lang, r), commune=r.commune, budget=fcfa(r.budget),
            deadline=value_label(self.lang, "deadline", r.deadline),
            desc=desc, cat=value_label(self.lang, "category", r.category), n=taken, max=MAX_PROS_PER_REQUEST,
        )

    def req_button(self, p: ProProfile, cmd: str) -> None:
        _, req_id, action = cmd.split(":", 2)
        r = self.db.get(ServiceRequest, req_id)
        if not r or r.state != "PUBLISHED" or not pro_sees(p, r):
            return self.pro_menu(self.t("req_gone"))
        taken = conversation_counts(self.db, [r.id]).get(r.id, 0)
        if action == "view":
            return self.say(self.request_text(r, taken), [(f"req:{r.id}:reply", self.t("btn_reply")), (f"req:{r.id}:skip", self.t("btn_skip"))])
        if action == "skip":
            return self.say(self.t("skip_ack"))
        if action == "reply":
            if taken >= MAX_PROS_PER_REQUEST and not self.db.scalar(
                select(Conversation.id).where(Conversation.request_id == r.id, Conversation.pro_id == self.user.id)
            ):
                return self.say(self.t("req_full", max=MAX_PROS_PER_REQUEST))
            if self.limit_reached(r.id):
                return
            self.go("PRO_REPLY", req=r.id)
            return self.say(self.t("pro_intro"))
        self.say(self.t("not_understood"))

    def limit_reached(self, req_id: str | None, force: bool = False) -> bool:
        """Limite de contacts de la formule atteinte : explique et propose de passer à une formule supérieure. True si la limite bloque."""
        p = self.user.pro
        q = pay.quota(self.db, p)
        already = req_id and self.db.scalar(select(Conversation.id).where(Conversation.request_id == req_id, Conversation.pro_id == self.user.id))
        if not (q["reached"] or force) or already:
            return False
        if force:  # message déjà écrit : on garde l'étape pour qu'il puisse le renvoyer après le paiement
            again = self.t("limit_resend")
        else:  # pas de conversation en cours : un message tapé ensuite ne doit pas être relayé à un ancien client
            self.go("MENU", conv=None, req=None)
            again = self.t("limit_retap")
        text = self.t("limit_msg", limit=q["limit"], plan=self.plan_name(q["plan"])) + "\n" + again
        self.say(text, self.upgrade_buttons(q["plan"]) or self.pro_menu_buttons)
        return True

    def plan_name(self, key: str) -> str:
        return PLANS[key]["name"]

    def upgrade_buttons(self, current: str) -> list[tuple[str, str]]:
        if not (PLANS_ACTIVE and settings.pay_on):
            return []
        order = list(PLANS)
        return [(f"plan:{k}", self.t("btn_upgrade", plan=self.plan_name(k))) for k in order[order.index(current) + 1:]] + [("menu", self.t("btn_menu"))]

    def plan_status(self, p: ProProfile) -> None:
        if not PLANS_ACTIVE:
            return self.say(self.t("plans_off"), self.pro_menu_buttons)
        q = pay.quota(self.db, p)
        limit = q["limit"]
        until = self.t("plan_until", date=f"{p.plan_until:%d/%m/%Y}") if q["plan"] != "FREE" and p.plan_until else ""
        cur = pay.prices(self.db)
        lines = [self.t("plan_line", plan=self.plan_name(q["plan"])) + until]
        lines.append(self.t("plan_used", used=q["used"], limit=limit) if limit is not None else self.t("plan_unlimited"))
        buttons = self.upgrade_buttons(q["plan"])
        if buttons:
            lines.append(self.t("plan_upgrade"))
            for k in PLANS:
                if cur[k] > cur[q["plan"]]:
                    n = PLAN_CONTACTS[k]
                    contacts = self.t("contacts_n", n=n) if n else self.t("contacts_all")
                    lines.append(self.t("plan_row", plan=self.plan_name(k), price=fcfa(cur[k]), contacts=contacts))
        self.say("\n".join(lines), buttons or self.pro_menu_buttons)

    def plan_checkout(self, plan: str, method: str | None = None) -> None:
        """Crée le lien de paiement. La formule ne s'active qu'une fois le paiement confirmé par le fournisseur (jamais sur la parole du client)."""
        if method is None and len(settings.pay_methods) > 1:  # le client choisit Wave ou Orange Money
            return self.say(self.t("pick_method"), [(f"plan:{plan}:{m}", pay.METHOD_NAMES[m]) for m in settings.pay_methods])
        try:
            c = pay.create_checkout(self.db, self.user, plan, 1, source="wa", method=method)
        except ApiError as e:
            return self.say(self.err(e), self.pro_menu_buttons)
        if c.provider in pay.MANUAL:
            note = self.t("note_prefilled") if c.provider == "wave" else self.t("note_enter_amount", amount=fcfa(c.amount))
            return self.say(self.t("checkout_link", plan=self.plan_name(plan), amount=fcfa(c.amount), method=pay.METHOD_NAMES[c.provider], note=note, url=c.checkout_url),
                            [(f"paid:{c.token}", self.t("btn_paid"))])
        self.say(self.t("checkout", plan=self.plan_name(plan), amount=fcfa(c.amount), url=c.checkout_url))

    def plan_claim(self, token: str) -> None:
        """Bouton « J'ai payé » (Wave) : activation immédiate, confirmation ensuite."""
        try:
            c = manual.claim(self.db, self.user, token)
        except ApiError as e:
            return self.say(self.err(e), self.pro_menu_buttons)
        self.say(self.t("claimed", plan=self.plan_name(c.plan)) if c.state in ("PROVISIONAL", "PAID") else self.t("claimed_wait"), self.pro_menu_buttons)

    def pro_first_message(self) -> None:
        req_id = self.data.get("req")
        res, body = self.text_or_voice(None)
        if res is None:
            return
        if self.ib.kind == "audio" and not body:
            return self.say(self.t("heard_nothing"))
        user = self.user
        assert user is not None
        try:
            conv_id, created = actions.start_conversation(self.db, user, req_id, body)
        except ApiError as e:
            if e.status in (404, 409):
                return self.pro_menu(self.err(e))
            if e.extra.get("code") == "PLAN_LIMIT":
                return self.limit_reached(req_id, force=True)
            return self.say(self.err(e))  # trop court, etc. : on reste dans l'étape pour qu'il puisse réécrire
        if not created:  # déjà en conversation avec ce client : le message s'ajoute à la conversation
            try:
                actions.post_chat_message(self.db, self.db.get(Conversation, conv_id), user, False, body)
            except ApiError as e:
                return self.say(self.err(e))
        conv = self.db.get(Conversation, conv_id)
        taken = conversation_counts(self.db, [conv.request_id]).get(conv.request_id, 0)
        self.go("MENU", conv=conv_id, req=None)
        self.say(
            self.t("pro_sent", n=taken, max=MAX_PROS_PER_REQUEST, s="s" if taken > 1 else "", a=self.t("a_many" if taken > 1 else "a_one")),
            self.pro_menu_buttons,
        )
        actions.notify_chat(self.db, conv_id, to_client=True, first=created)
