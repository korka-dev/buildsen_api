"""Conversion des objets en JSON pour le frontend (clés en camelCase, dates ISO 8601 UTC).

Chaque fonction ne renvoie que ce que le destinataire a le droit de voir : par exemple,
le téléphone d'un client n'est donné au professionnel qu'après partage du WhatsApp.
"""

from .models import Ad, Conversation, Credential, Message, PortfolioItem, ProProfile, ServiceRequest, User
from .rules import PLANS_ACTIVE, active_plan, plan_rank
from .services.market import badge, client_name, pro_name, request_title
from .utils import iso


def user_out(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "phone": u.phone, "firstName": u.first_name, "lastName": u.last_name,
        "role": u.role, "adminRole": u.admin_role, "status": u.status,
        "emailVerifiedAt": iso(u.email_verified_at), "createdAt": iso(u.created_at),
    }


def credential_out(c: Credential) -> dict:
    return {"id": c.id, "type": c.type, "fileName": c.file_name, "mime": c.mime, "size": c.size, "state": c.state, "reason": c.reason}


def portfolio_out(it: PortfolioItem) -> dict:
    return {"id": it.id, "caption": it.caption, "mime": it.mime, "createdAt": iso(it.created_at)}


def pro_private_out(p: ProProfile, *, with_files: bool = False) -> dict:
    """Profil complet, pour le professionnel lui-même et pour l'administration."""
    out = {
        "id": p.id, "statut": p.statut, "companyName": p.company_name, "state": p.state, "school": p.school, "diploma": p.diploma,
        "experienceYears": p.experience_years, "bio": p.bio, "software": p.software, "specialties": list(p.specialties or []),
        "commune": p.commune, "whatsapp": p.whatsapp, "visible": p.visible, "plan": p.plan, "activePlan": active_plan(p.plan, p.plan_until),
        "planUntil": iso(p.plan_until), "launchOffer": p.launch_offer, "featured": p.featured, "spotlight": p.spotlight,
        "submittedAt": iso(p.submitted_at), "decidedAt": iso(p.decided_at), "decisionReason": p.decision_reason,
        "createdAt": iso(p.created_at), "updatedAt": iso(p.updated_at),
    }
    if with_files:
        out["credentials"] = [credential_out(c) for c in p.credentials]
        out["portfolio"] = [portfolio_out(it) for it in p.portfolio]
    return out


def pro_card_out(p: ProProfile, portfolio_limit: int | None = 3, rating: dict | None = None) -> dict:
    """Carte publique d'un professionnel vérifié (vitrine, fiche) : jamais de coordonnées."""
    items = p.portfolio if portfolio_limit is None else p.portfolio[:portfolio_limit]
    return {
        "id": p.id, "name": pro_name(p.user, p), "firstName": p.user.first_name, "lastName": p.user.last_name,
        "badge": badge(p.user, p), "statut": p.statut, "companyName": p.company_name, "commune": p.commune,
        "experienceYears": p.experience_years, "bio": p.bio, "software": p.software, "specialties": list(p.specialties or []),
        "featured": p.featured or (PLANS_ACTIVE and plan_rank(p) == 0), "spotlight": p.spotlight, "portfolioCount": len(p.portfolio),
        "portfolio": [portfolio_out(it) for it in items], "updatedAt": iso(p.updated_at),
        "rating": rating or {"avg": None, "count": 0},
    }


def request_out(r: ServiceRequest, *, conversations: int | None = None) -> dict:
    out = {
        "id": r.id, "ref": r.ref, "title": request_title(r), "category": r.category, "service": r.service, "buildingType": r.building_type,
        "description": r.description, "commune": r.commune, "district": r.district, "landStatus": r.land_status, "phase": r.phase,
        "levels": r.levels, "rooms": r.rooms, "surface": r.surface, "dims": r.dims, "deadline": r.deadline, "budget": r.budget,
        "state": r.state, "rejectReason": r.reject_reason, "publishedAt": iso(r.published_at), "closedAt": iso(r.closed_at),
        "createdAt": iso(r.created_at), "hasPhoto": bool(r.photo_path),
    }
    if conversations is not None:
        out["conversations"] = conversations
    return out


def message_out(m: Message) -> dict:
    return {"id": m.id, "senderId": m.sender_id, "body": m.body, "createdAt": iso(m.created_at)}


def ad_out(a: Ad) -> dict:
    return {"id": a.id, "title": a.title, "text": a.text, "linkLabel": a.link_label, "href": a.href, "active": a.active, "createdAt": iso(a.created_at)}


def conversation_summary(c: Conversation, viewer: str, last: Message | None) -> dict:
    """Ligne de messagerie : interlocuteur, demande, extrait du dernier message, non-lu."""
    read_at = c.client_read_at if viewer == "client" else c.pro_read_at
    p = c.pro.pro
    out = {
        "id": c.id, "shared": c.shared, "lastMessageAt": iso(c.last_message_at),
        "unread": not read_at or read_at < c.last_message_at,
        "lastMessage": last.body if last else "",
        "request": {"id": c.request.id, "ref": c.request.ref, "title": request_title(c.request), "commune": c.request.commune, "state": c.request.state},
    }
    if viewer == "client":
        out["other"] = {"name": pro_name(c.pro, p), "statut": p.statut if p else None, "badge": badge(c.pro, p)}
    else:
        cl = c.request.client
        out["other"] = {"name": client_name(cl, c.shared), "badge": f"{cl.first_name[:1]}{cl.last_name[:1]}".upper()}
    return out


def conversation_full(c: Conversation, viewer: str) -> dict:
    """Conversation ouverte : messages, interlocuteur, partage du WhatsApp."""
    p = c.pro.pro
    cl = c.request.client
    out = {
        "id": c.id, "shared": c.shared, "sharedAt": iso(c.shared_at),
        "messages": [message_out(m) for m in c.messages],
        "request": request_out(c.request),
    }
    if viewer == "client":
        out["other"] = {
            "name": pro_name(c.pro, p), "badge": badge(c.pro, p), "firstName": c.pro.first_name, "lastName": c.pro.last_name,
            "statut": p.statut if p else None, "state": p.state if p else None, "companyName": p.company_name if p else None,
            "commune": p.commune if p else None, "experienceYears": p.experience_years if p else None, "bio": p.bio if p else None,
            "specialties": list(p.specialties or []) if p else [], "software": p.software if p else None,
            "portfolio": [portfolio_out(it) for it in (p.portfolio[:6] if p else [])],
            # Numéro du professionnel visible par le client après partage
            "phone": ((p.whatsapp if p else None) or c.pro.phone) if c.shared else None,
        }
    else:
        out["other"] = {
            "name": client_name(cl, c.shared), "badge": f"{cl.first_name[:1]}{cl.last_name[:1]}".upper(), "commune": c.request.commune,
            "phone": cl.phone if c.shared else None,
        }
    return out
