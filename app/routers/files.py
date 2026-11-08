"""Fichiers privés : envoi direct vers Supabase (lien signé) puis contrôle, et lecture par liens signés courts."""

import re
import uuid

from fastapi import APIRouter, Body, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session as Db

from ..audit import audit
from ..db import get_db
from ..deps import current_session, require_member
from ..errors import ApiError, not_found
from ..models import Credential, PortfolioItem, ProProfile, ServiceRequest, Session, User
from ..rules import DOC_TYPES, EXT, MAX_PORTFOLIO, UPLOADS, docs_for, dossier_editable
from ..storage import object_info, peek, remove_object, sign_upload, signed_url
from ..throttle import too_many
from ..validators import text

router = APIRouter(prefix="/api", tags=["Fichiers"])

# Premiers octets attendus selon le type annoncé : un fichier qui n'est pas ce qu'il prétend être est refusé
_MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


def sniff_ok(mime: str, head: bytes | None) -> bool:
    if not head or mime not in _MAGIC:
        return False
    if mime == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(m) for m in _MAGIC[mime])


def _pro(db: Db, user: User) -> ProProfile:
    p = db.scalar(select(ProProfile).where(ProProfile.user_id == user.id))
    if not p:
        raise not_found("Profil introuvable.")
    return p


@router.post("/uploads/sign")
def upload_sign(body: dict = Body(...), user: User = Depends(require_member), db: Db = Depends(get_db)):
    """Étape 1 : vérifie les droits et le fichier annoncé, puis renvoie un lien d'envoi direct vers Supabase."""
    kind = text(body, "kind")
    if kind not in UPLOADS:
        raise ApiError("Requête invalide.")
    rule = UPLOADS[kind]
    mime, size = text(body, "mime"), body.get("size")
    if mime not in rule["mimes"]:
        raise ApiError("Format non accepté.")
    if not isinstance(size, int) or size <= 0 or size > rule["max_bytes"]:
        raise ApiError(f"Fichier trop lourd ({rule['max_bytes'] // 1048576} Mo maximum).")
    if too_many(db, f"upload:{user.id}", 60, 60):
        raise ApiError("Trop d'envois de fichiers. Réessayez dans une heure.", 429)
    if (kind == "request") != (user.role == "CLIENT"):
        raise ApiError("Envoi non autorisé pour votre compte.", 403)
    if kind == "request":  # photo du terrain : pas de profil professionnel à contrôler
        path = f"{user.id}/{uuid.uuid4()}.{EXT[mime]}"
        return {"uploadUrl": sign_upload(rule["bucket"], path), "path": path}
    p = _pro(db, user)
    if kind == "credential":
        if not dossier_editable(p.state):
            raise ApiError("Dossier non modifiable.", 409)
        if text(body, "type") not in docs_for(p.statut):
            raise ApiError("Type de justificatif invalide.")
    elif db.scalar(select(func.count()).select_from(PortfolioItem).where(PortfolioItem.pro_id == p.id)) >= MAX_PORTFOLIO:
        raise ApiError(f"{MAX_PORTFOLIO} réalisations maximum.", 409)
    path = f"{user.id}/{uuid.uuid4()}.{EXT[mime]}"
    return {"uploadUrl": sign_upload(rule["bucket"], path), "path": path}


@router.post("/uploads/confirm")
def upload_confirm(body: dict = Body(...), user: User = Depends(require_member), db: Db = Depends(get_db)):
    """Étape 2 : contrôle le fichier réellement reçu par Supabase (taille, type), puis l'enregistre."""
    kind, path, doc_type = text(body, "kind"), text(body, "path"), text(body, "type")
    name, caption = text(body, "name")[:160], text(body, "caption")[:120]
    if kind not in UPLOADS or not name:
        raise ApiError("Requête invalide.")
    rule = UPLOADS[kind]
    if not re.fullmatch(rf"{re.escape(user.id)}/[0-9a-f-]{{36}}\.(pdf|jpg|png|webp)", path):
        raise ApiError("Chemin invalide.")
    info = object_info(rule["bucket"], path)
    if not info:
        raise ApiError("Fichier non reçu. Réessayez.")
    if info["size"] > rule["max_bytes"] or info["mime"] not in rule["mimes"] or not sniff_ok(info["mime"], peek(rule["bucket"], path)):
        remove_object(rule["bucket"], path)
        raise ApiError("Fichier refusé (format ou taille).")
    if (kind == "request") != (user.role == "CLIENT"):
        remove_object(rule["bucket"], path)
        raise ApiError("Envoi non autorisé pour votre compte.", 403)
    if kind == "request":  # le fichier est rattaché à la demande quand elle est déposée (create_service_request)
        return {"ok": True, "path": path}
    p = _pro(db, user)
    if kind == "credential":
        if not dossier_editable(p.state) or doc_type not in docs_for(p.statut):
            remove_object(rule["bucket"], path)
            raise ApiError("Dossier non modifiable.", 409)
        # Un seul fichier par type : le nouveau remplace l'ancien
        for old in [c for c in p.credentials if c.type == doc_type]:
            remove_object(rule["bucket"], old.path)
            db.delete(old)
        db.flush()
        db.add(Credential(pro_id=p.id, type=doc_type, path=path, file_name=name, mime=info["mime"], size=info["size"]))
    else:
        db.add(PortfolioItem(pro_id=p.id, path=path, caption=caption or re.sub(r"\.[a-z0-9]+$", "", name, flags=re.I), mime=info["mime"], size=info["size"]))
    db.commit()
    return {"ok": True}


@router.get("/fichiers/{kind}/{file_id}")
def open_file(kind: str, file_id: str, s: Session | None = Depends(current_session), db: Db = Depends(get_db)):
    """Redirige vers un lien signé, après contrôle des droits.

    Justificatifs : le professionnel concerné, ou un administrateur vérificateur (consultation journalisée).
    Portfolio : le professionnel, l'administration, et tout le monde si le profil est vérifié et visible.
    """
    is_admin = bool(s and s.user.role == "ADMIN" and s.mfa)
    if kind == "justificatif":
        if not s:
            raise ApiError("Non autorisé", 401)
        c = db.get(Credential, file_id)
        if not c:
            raise not_found()
        owner = c.pro.user_id == s.user.id
        can_verify = is_admin and s.user.admin_role in ("SUPER", "VERIF")
        if not owner and not can_verify:
            raise ApiError("Non autorisé", 403)
        if not owner:
            audit(db, s.user.id, f"Consultation : {DOC_TYPES.get(c.type, c.type)}", c.pro.user.full_name)
            db.commit()
        return RedirectResponse(signed_url("justificatifs", c.path), status_code=307)
    if kind == "portfolio":
        it = db.get(PortfolioItem, file_id)
        if not it:
            raise not_found()
        showcased = it.pro.state == "VERIFIED" and it.pro.visible
        if not showcased and not is_admin and (not s or it.pro.user_id != s.user.id):
            raise ApiError("Non autorisé", 403 if s else 401)
        return RedirectResponse(signed_url("portfolios", it.path, 600), status_code=307)
    if kind == "demande":
        r = db.get(ServiceRequest, file_id)
        if not r or not r.photo_path:
            raise not_found()
        if not s:
            raise ApiError("Non autorisé", 401)
        owner = r.client_id == s.user.id
        pro_ok = s.user.role == "PRO" and r.state == "PUBLISHED" and bool(s.user.pro) and s.user.pro.state == "VERIFIED"
        if not owner and not pro_ok and not is_admin:
            raise ApiError("Non autorisé", 403)
        return RedirectResponse(signed_url("demandes", r.photo_path, 600), status_code=307)
    raise not_found()
