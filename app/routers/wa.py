"""WhatsApp : adresse de réception de Meta (webhook), simulateur de développement, lien public et supervision.

- GET/POST /api/wa/webhook            Meta (vérification de l'adresse, puis chaque message reçu, signé)
- /api/wa/sim/*                       simulateur local : actif seulement si WHATSAPP_PROVIDER=sim et hors production
- GET /api/wa/info, /api/wa/qr.svg    lien et QR code « Écrire sur WhatsApp » du site
- GET/POST /api/admin/whatsapp        supervision et interrupteur d'urgence (administration)
"""

import hmac
import io
import logging
from datetime import timedelta

import segno
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import require_admin
from ..errors import ApiError, not_found
from ..mailer import app_url
from ..models import User, WaMessage, WaSession
from ..security import create_session
from ..services.wa import bot, meta_payload, scenarios, store
from ..services.wa.types import Inbound
from ..tasks import with_db
from ..throttle import clear_throttle
from ..utils import cuid, now, phone_local
from ..validators import clean

log = logging.getLogger("buildsen.wa")
router = APIRouter(prefix="/api", tags=["WhatsApp"])


# --- Meta -------------------------------------------------------------------------------------------------------------

@router.get("/wa/webhook")
def webhook_verify(
    mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"), challenge: str = Query("", alias="hub.challenge"),
):
    """Meta vérifie l'adresse en renvoyant le jeton que nous avons choisi : on lui renvoie son « challenge »."""
    expected = settings.whatsapp_verify_token
    if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
        return PlainTextResponse(challenge)
    raise ApiError("Jeton de vérification invalide.", 403)


def _update_statuses(db: Db, statuses: list[dict]) -> None:
    for st in statuses:
        m = db.get(WaMessage, st["id"])
        if not m:
            continue
        # sent -> delivered -> read ; un échec n'est jamais écrasé par un état antérieur
        if st["status"] in ("sent", "delivered", "read", "failed") and m.status != "failed":
            m.status = st["status"]
        if st["status"] == "failed":
            err = (st["errors"] or [{}])[0]
            m.payload = {**(m.payload or {}), "error": str(err.get("title") or err.get("message") or "")[:200], "code": err.get("code")}
            log.warning("WhatsApp : message non remis, code=%s", err.get("code"))
    db.commit()


@router.post("/wa/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    """Messages reçus. On répond 200 tout de suite (Meta réessaie sinon) et le traitement se fait en arrière-plan."""
    if not settings.wa_meta:
        raise not_found()
    raw = await request.body()
    if not meta_payload.valid_signature(raw, request.headers.get("x-hub-signature-256")):
        log.warning("WhatsApp : signature invalide, message rejeté")
        raise ApiError("Signature invalide.", 403)
    try:
        payload = await request.json()
    except ValueError:
        raise ApiError("Requête invalide.", 400) from None
    inbound, statuses = meta_payload.parse(payload)
    for ib in inbound:
        background.add_task(with_db, bot.handle_safe, ib)
    if statuses:
        background.add_task(with_db, _update_statuses, statuses)
    return {"ok": True}


# --- Lien public ------------------------------------------------------------------------------------------------------

def _public_link() -> str | None:
    if settings.wa_sim:
        return app_url("/simulateur")
    number = "".join(c for c in settings.whatsapp_bot_number if c.isdigit())
    return f"https://wa.me/221{number}?text=Bonjour" if settings.wa_meta and len(number) == 9 else None


@router.get("/wa/info")
def info():
    return {"enabled": settings.wa_on, "simulator": settings.wa_sim, "link": _public_link(), "lanUrl": settings.lan_url.rstrip("/") if settings.wa_sim else ""}


@router.get("/wa/qr.svg")
def qr(target: str = ""):
    """QR code du lien public ; ?target=lan : adresse du simulateur sur le réseau local (pour l'ouvrir depuis un téléphone)."""
    link = f"{settings.lan_url.rstrip('/')}/simulateur" if target == "lan" and settings.wa_sim and settings.lan_url else _public_link()
    if not link:
        raise not_found()
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="svg", scale=6, border=2, dark="#14803f", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


# --- Simulateur (développement) ---------------------------------------------------------------------------------------

def _sim() -> None:
    if not settings.wa_sim:
        raise not_found()


def _phone(value: str) -> str:
    p = phone_local(value)
    if not p:
        raise ApiError("Numéro sénégalais attendu, par exemple 77 123 45 67.")
    return p


@router.get("/wa/sim/contacts")
def sim_contacts(db: Db = Depends(get_db)):
    """Comptes existants, pour choisir un numéro dans le simulateur."""
    _sim()
    users = db.scalars(select(User).where(User.role.in_(["CLIENT", "PRO"]), User.phone.is_not(None)).order_by(User.role, User.created_at).limit(50)).all()
    return {"contacts": [{"phone": phone_local(u.phone), "name": f"{u.first_name} {u.last_name}", "role": u.role} for u in users if phone_local(u.phone)]}


@router.post("/wa/sim/send")
def sim_send(body: dict = Body(...), db: Db = Depends(get_db)):
    """Un message envoyé depuis le téléphone simulé : texte, bouton touché ou note vocale (WAV en base64)."""
    _sim()
    import base64

    phone = _phone(str(body.get("phone") or ""))
    kind = str(body.get("kind") or "text")
    ib = Inbound(id="sim_in_" + cuid(), phone=phone, kind=kind)
    if kind == "text":
        ib.text = clean(str(body.get("text") or ""))[:4000]
        if not ib.text.strip():
            raise ApiError("Écrivez un message.")
    elif kind == "button":
        ib.button_id = str(body.get("buttonId") or "")[:256]
        ib.text = str(body.get("title") or "")[:40]
    elif kind == "audio":
        try:
            ib.audio = base64.b64decode(str(body.get("audio") or ""), validate=True)
        except ValueError:
            raise ApiError("Audio invalide.") from None
        if not ib.audio:
            raise ApiError("Aucun son reçu.")
    else:
        raise ApiError("Type de message inconnu.")
    bot.handle_safe(db, ib)
    return {"ok": True}


def _row(m: WaMessage) -> dict:
    p = dict(m.payload or {})
    has_audio = bool(p.pop("audio", None))
    return {
        "id": m.id, "direction": m.direction, "kind": m.kind, "body": m.body, "status": m.status, "hasAudio": has_audio,
        "buttons": p.get("buttons") or [], "template": p.get("template"), "error": p.get("error"), "at": m.created_at.isoformat() + "Z",
    }


@router.get("/wa/sim/messages")
def sim_messages(phone: str, db: Db = Depends(get_db)):
    _sim()
    p = _phone(phone)
    rows = db.scalars(select(WaMessage).where(WaMessage.phone == p).order_by(WaMessage.created_at.desc()).limit(120)).all()
    s = db.get(WaSession, p)
    return {"messages": [_row(m) for m in reversed(rows)], "state": s.state if s else "NEW", "optedOut": bool(s and s.opted_out)}


@router.get("/wa/sim/media/{message_id}")
def sim_media(message_id: str, db: Db = Depends(get_db)):
    _sim()
    import base64

    m = db.get(WaMessage, message_id)
    audio = (m.payload or {}).get("audio") if m else None
    if not audio:
        raise not_found()
    return Response(base64.b64decode(audio), media_type="audio/mpeg")


@router.get("/wa/sim/scenarios")
def sim_scenarios():
    _sim()
    return {"scenarios": [{"id": k, "title": v} for k, v in scenarios.SCENARIOS.items()]}


@router.post("/wa/sim/scenario")
def sim_scenario(body: dict = Body(...), db: Db = Depends(get_db)):
    """Prépare une situation précise à tester à la main (réservé au simulateur)."""
    _sim()
    out = scenarios.play(db, str(body.get("name") or ""))
    if out.get("error"):
        raise ApiError(out["error"])
    return out


@router.post("/wa/sim/login")
def sim_login(response: Response, body: dict = Body(...), db: Db = Depends(get_db)):
    """Connexion en un clic à un compte de test (jamais en production, jamais un administrateur ni un vrai compte)."""
    _sim()
    p = _phone(str(body.get("phone") or ""))
    u = next((x for x in db.scalars(select(User).where(User.role.in_(["CLIENT", "PRO"]), User.phone.is_not(None))).all() if phone_local(x.phone) == p), None)
    if not u or not (u.email.endswith(".test") or u.email.endswith(".invalid")) or u.status != "ACTIVE":
        raise ApiError("Seuls les comptes de test peuvent se connecter ainsi.", 403)
    create_session(db, response, u.id)
    db.commit()
    return {"redirect": "/espace" if u.role == "CLIENT" else "/pro", "name": f"{u.first_name} {u.last_name}"}


@router.post("/wa/sim/reset")
def sim_reset(body: dict = Body(...), db: Db = Depends(get_db)):
    """Repart de zéro pour ce numéro : efface la conversation ; avec deleteAccount, supprime aussi un compte créé par WhatsApp."""
    _sim()
    p = _phone(str(body.get("phone") or ""))
    for m in db.scalars(select(WaMessage).where(WaMessage.phone == p)).all():
        db.delete(m)
    s = db.get(WaSession, p)
    if s:
        db.delete(s)
    deleted = False
    if body.get("deleteAccount"):
        # Seuls les comptes créés par l'assistant (adresse factice) peuvent être supprimés ici
        for u in db.scalars(select(User).where(User.email == f"{p}@wa.buildsen.invalid")).all():
            db.delete(u)
            deleted = True
    db.flush()  # la session supprimée doit disparaître avant d'en recréer une (sinon SQLAlchemy conserve l'ancienne ligne)
    if not deleted and store.find_user(db, p):  # un compte existant garde le français (« langue » pour en changer) ; un numéro sans compte choisit à nouveau
        db.add(WaSession(phone=p, state="NEW", data={"lang": "fr"}))
    db.commit()
    clear_throttle(db, f"wa-in:{p}")  # repartir de zéro inclut la limite de messages par heure
    return {"ok": True, "accountDeleted": deleted}


# --- Administration ---------------------------------------------------------------------------------------------------

def _masked(phone: str) -> str:
    return f"{phone[:2]} {phone[2]}•• •• {phone[-2:]}" if len(phone) == 9 else "—"


@router.get("/admin/whatsapp")
def admin_overview(admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    since = now() - timedelta(hours=24)

    def count(*cond) -> int:
        return db.scalar(select(func.count()).select_from(WaMessage).where(WaMessage.created_at >= since, *cond)) or 0

    rows = db.scalars(select(WaMessage).order_by(WaMessage.created_at.desc()).limit(100)).all()
    return {
        "provider": "meta" if settings.wa_meta else "sim" if settings.wa_sim else "off",
        "enabled": store.enabled(db),
        "sessions": db.scalar(select(func.count()).select_from(WaSession)) or 0,
        "last24h": {"in": count(WaMessage.direction == "IN"), "out": count(WaMessage.direction == "OUT"), "failed": count(WaMessage.status == "failed")},
        "messages": [{**_row(m), "phone": _masked(m.phone), "body": m.body[:300], "audio": None} for m in rows],
        "note": "Les numéros sont masqués. Les messages sont supprimés après 90 jours.",
    }


@router.post("/admin/whatsapp/enabled")
def admin_toggle(body: dict = Body(...), admin: User = Depends(require_admin("SUPPORT", "MODER")), db: Db = Depends(get_db)):
    """Interrupteur d'urgence : coupe les réponses et les alertes WhatsApp (le site continue de fonctionner)."""
    on = bool(body.get("enabled"))
    store.set_enabled(db, on)
    audit(db, admin.id, "Assistant WhatsApp " + ("activé" if on else "coupé"))
    db.commit()
    return {"ok": True, "enabled": on}
