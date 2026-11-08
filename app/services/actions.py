"""Actions métier communes au site et à l'assistant WhatsApp : mêmes règles, mêmes limites, un seul endroit.

Chaque fonction lève ApiError avec un message en français ; l'appelant (route HTTP ou assistant WhatsApp) décide de l'affichage.
"""

import re
import time

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..audit import audit
from ..emails import request_published_mail, shared_mail
from ..errors import ApiError
from ..mailer import app_url, mail_ctx, notify_team, send_mail
from ..models import Conversation, Message, ProProfile, ServiceRequest, User
from ..rules import CLIENTS_OPEN, MAX_PROS_PER_REQUEST, UPLOADS, pro_sees
from ..storage import object_info, peek
from ..throttle import too_many
from ..utils import fcfa, now
from ..validators import clean, service_request, text
from .alerts import alert_pros
from .market import client_name, new_request_ref, notify_message, request_title
from .pay import service as pay

# --- Demandes -----------------------------------------------------------------------------------------------------------

def _checked_photo(user: User, body: dict) -> dict:
    """Photo du terrain ou croquis (facultatif) : le fichier doit avoir été envoyé par ce client, être une image réelle et rester léger."""
    path = str(body.get("photo") or "").strip()
    if not path:
        return {}
    from ..routers.files import sniff_ok  # import tardif : le routeur importe ce module

    rule = UPLOADS["request"]
    if not re.fullmatch(rf"{re.escape(user.id)}/[0-9a-f-]{{36}}\.(jpg|png|webp)", path):
        raise ApiError("Photo invalide.")
    info = object_info(rule["bucket"], path)
    if not info or info["size"] > rule["max_bytes"] or info["mime"] not in rule["mimes"] or not sniff_ok(info["mime"], peek(rule["bucket"], path)):
        raise ApiError("Photo refusée (format ou taille). Réessayez avec une autre image.")
    return {"photo_path": path, "photo_mime": info["mime"]}


def create_service_request(db: Db, user: User, body: dict) -> ServiceRequest:
    """Publie une demande du particulier (publication immédiate : l'équipe peut la retirer après coup)."""
    if not CLIENTS_OPEN:
        raise ApiError("Le dépôt de demandes n'est pas encore ouvert.", 403)
    if not user.email_verified_at:
        raise ApiError("Confirmez d'abord votre adresse e-mail (lien reçu par e-mail).", 403)
    data = service_request(body)
    if too_many(db, f"request:{user.id}", 5, 24 * 60):
        raise ApiError("Vous avez déposé beaucoup de demandes aujourd'hui. Réessayez demain ou contactez-nous.", 429)
    photo = _checked_photo(user, body)
    r = ServiceRequest(**data, ref=new_request_ref(db), client_id=user.id, state="PUBLISHED", published_at=now(), **photo)
    db.add(r)
    db.flush()
    audit(db, user.id, "Demande publiée", r.ref)
    db.commit()
    return r


def after_publish(db: Db, request_id: str) -> None:
    """Après la publication : alertes aux professionnels (e-mail et WhatsApp) et information de l'équipe."""
    r = db.get(ServiceRequest, request_id)
    alert_pros(db, r, "paid")
    c = r.client
    send_mail(c.email, request_published_mail(mail_ctx(), c.first_name, request_title(r), r.ref, app_url(f"/espace/demandes/{r.ref}"), MAX_PROS_PER_REQUEST))
    notify_team(db, f"Nouvelle demande publiée : {r.ref}",
                f"{request_title(r)}\n{r.commune} · budget {fcfa(r.budget)}\nDéposée par {c.first_name} {c.last_name[:1]}.\n\n{app_url('/admin/demandes')}",
                ("MODER",))


def close_request(db: Db, user: User, ref: str) -> ServiceRequest:
    """Le client clôture sa demande : plus aucun professionnel ne peut lui écrire à son sujet."""
    r = db.scalar(select(ServiceRequest).where(ServiceRequest.ref == ref))
    if not r or r.client_id != user.id:
        raise ApiError("Demande introuvable.", 404)
    if r.state != "CLOSED":
        r.state, r.closed_at = "CLOSED", now()
        audit(db, user.id, "Demande clôturée", ref)
        db.commit()
    return r


# --- Messagerie ---------------------------------------------------------------------------------------------------------

def post_chat_message(db: Db, conv: Conversation, user: User, is_client: bool, body: str) -> Message:
    """Ajoute un message du client ou du professionnel dans une conversation ouverte."""
    if conv.request.state == "CLOSED":
        raise ApiError("Cette demande est clôturée : la conversation est en lecture seule.", 409)
    msg = clean(body or "").strip()
    if not msg:
        raise ApiError("Écrivez un message.")
    if len(msg) > 2000:
        raise ApiError("Message : 2 000 caractères maximum.")
    if too_many(db, f"msg:{user.id}", 60, 60):
        raise ApiError("Vous envoyez beaucoup de messages. Patientez un peu.", 429)
    t = now()
    m = Message(conversation_id=conv.id, sender_id=user.id, body=msg, created_at=t)
    db.add(m)
    conv.last_message_at = t
    if is_client:
        conv.client_read_at = t
    else:
        conv.pro_read_at = t
    db.commit()
    return m


def notify_chat(db: Db, conversation_id: str, to_client: bool, first: bool = False) -> None:
    """Prévient le destinataire d'un nouveau message (e-mail et WhatsApp). À lancer en tâche d'arrière-plan."""
    c = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).options(
            joinedload(Conversation.request).joinedload(ServiceRequest.client),
            joinedload(Conversation.pro).joinedload(User.pro),
        )
    )
    if c:
        notify_message(db, c, to_client, first)


# --- Premier message d'un professionnel ----------------------------------------------------------------------------------

class _Full(Exception):
    pass


def _open_conversation(db: Db, request_id: str, pro_user_id: str, body: str) -> tuple[str, bool]:
    """Transaction sérialisable : deux professionnels qui écrivent en même temps ne peuvent pas dépasser la limite."""
    with db.begin():
        db.connection(execution_options={"isolation_level": "SERIALIZABLE"})
        existing = db.scalar(select(Conversation.id).where(Conversation.request_id == request_id, Conversation.pro_id == pro_user_id))
        if existing:
            return existing, False
        if db.scalar(select(func.count()).select_from(Conversation).where(Conversation.request_id == request_id)) >= MAX_PROS_PER_REQUEST:
            raise _Full()
        t = now()
        conv = Conversation(request_id=request_id, pro_id=pro_user_id, pro_read_at=t, last_message_at=t)
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, sender_id=pro_user_id, body=body, created_at=t))
        return conv.id, True


def verified_pro(db: Db, user: User) -> ProProfile:
    if not CLIENTS_OPEN:
        raise ApiError("Les demandes des particuliers ne sont pas encore ouvertes.", 403)
    p = db.scalar(select(ProProfile).where(ProProfile.user_id == user.id))
    if not p:
        raise ApiError("Profil introuvable.", 404)
    if p.state != "VERIFIED":
        raise ApiError("Votre profil doit être vérifié pour consulter les demandes et écrire aux clients.", 403, code="NOT_VERIFIED")
    return p


def start_conversation(db: Db, user: User, request_id: str, raw_body: str) -> tuple[str, bool]:
    """Premier message d'un professionnel vérifié : ouvre la conversation (5 professionnels au maximum par demande).

    Renvoie (identifiant de la conversation, créée maintenant ?). Un pro déjà en conversation retrouve la sienne.
    """
    p = verified_pro(db, user)
    msg = text({"b": raw_body}, "b")
    if len(msg) < 20:
        raise ApiError("Écrivez un premier message (20 caractères minimum) : présentez-vous, ce que vous proposez et votre délai.")
    if len(msg) > 2000:
        raise ApiError("Message : 2 000 caractères maximum.")
    r = db.get(ServiceRequest, request_id)
    if not r or r.state != "PUBLISHED" or not pro_sees(p, r):
        raise ApiError("Cette demande n'est plus disponible.", 404)
    if too_many(db, f"start:{user.id}", 30, 24 * 60):
        raise ApiError("Vous avez ouvert beaucoup de conversations aujourd'hui. Réessayez demain.", 429)
    # Limite de contacts de la formule : une conversation déjà ouverte ne compte pas une seconde fois
    if not db.scalar(select(Conversation.id).where(Conversation.request_id == request_id, Conversation.pro_id == user.id)):
        q = pay.quota(db, p)
        if q["reached"]:
            raise ApiError(pay.limit_message(q), 402, code="PLAN_LIMIT", plan=q["plan"], limit=q["limit"])
    ref = r.ref
    db.commit()  # termine la transaction implicite avant la transaction sérialisable
    for attempt in range(1, 4):
        try:
            conv_id, created = _open_conversation(db, request_id, user.id, msg)
            break
        except _Full:
            db.rollback()
            raise ApiError(f"Cette demande est complète : {MAX_PROS_PER_REQUEST} professionnels ont déjà écrit au client.", 409) from None
        except (IntegrityError, DBAPIError):
            # Conflit de sérialisation ou base lente au réveil : nouvel essai
            db.rollback()
            if attempt == 3:
                raise ApiError("L'envoi n'a pas abouti, la connexion est lente. Votre message est conservé : réessayez.", 503) from None
            time.sleep(0.3 * attempt)
    if created:
        audit(db, user.id, "Conversation ouverte avec un client", ref)
        db.commit()
    return conv_id, created


def share_whatsapp(db: Db, user: User, conversation_id: str) -> tuple[Conversation, bool]:
    """Le client partage son numéro avec le professionnel de cette conversation. Renvoie (conversation, partagé à l'instant ?)."""
    c = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id)
        .options(joinedload(Conversation.request), joinedload(Conversation.pro).joinedload(User.pro))
    )
    if not c or c.request.client_id != user.id:
        raise ApiError("Conversation introuvable.", 404)
    if c.request.state == "CLOSED":
        raise ApiError("Cette demande est clôturée.", 409)
    new = not c.shared
    if new:
        c.shared, c.shared_at = True, now()
        audit(db, user.id, "WhatsApp partagé avec un professionnel", c.id)
        db.commit()
    return c, new


def notify_shared(db: Db, conversation_id: str) -> None:
    """Prévient le professionnel que le client a partagé son numéro (e-mail et WhatsApp)."""
    from .wa import notify as wa_notify

    c = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id)
        .options(joinedload(Conversation.request).joinedload(ServiceRequest.client), joinedload(Conversation.pro).joinedload(User.pro))
    )
    if not c:
        return
    client = c.request.client
    send_mail(c.pro.email, shared_mail(mail_ctx(), c.pro.first_name, client_name(client, True), request_title(c.request), app_url(f"/pro/messages/{c.id}")))
    wa_notify.number_shared(db, c)
