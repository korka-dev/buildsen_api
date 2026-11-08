"""Centre de notifications (cloche et pop-up) : messages non lus et nouvelles demandes."""

from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload

from ..models import Conversation, ProProfile, ServiceRequest, User
from ..rules import FREE_ALERT_DELAY_H, MAX_PROS_PER_REQUEST, PLANS_ACTIVE, STATUTS, active_plan, pro_sees
from ..utils import fcfa, now
from .market import badge, client_name, initials, pro_name
from .queries import conversation_counts, last_messages, my_conversations


def _excerpt(s: str) -> str:
    return f"« {s[:88] + '…' if len(s) > 90 else s} »"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {many if n > 1 else one}"


def _ms(d) -> int:

    return int(d.replace(tzinfo=UTC).timestamp() * 1000)


def _unread(convs: list[Conversation], last: dict, user_id: str, read_attr: str) -> list[Conversation]:
    out = []
    for c in convs:
        read_at = getattr(c, read_attr)
        m = last.get(c.id)
        if (not read_at or read_at < c.last_message_at) and m and m.sender_id != user_id:
            out.append(c)
    return out


def client_notifs(db: Db, user: User) -> dict:
    """Particulier : professionnels dont un message n'a pas encore été lu."""
    convs = db.scalars(
        select(Conversation).join(ServiceRequest).where(ServiceRequest.client_id == user.id)
        .order_by(Conversation.last_message_at.desc())
        .options(joinedload(Conversation.pro).joinedload(User.pro))
    ).all()
    last = last_messages(db, [c.id for c in convs])
    unread = _unread(list(convs), last, user.id, "client_read_at")
    items = []
    for c in unread[:5]:
        p = c.pro.pro
        items.append({
            "id": f"m-{c.id}-{_ms(c.last_message_at)}",
            "href": f"/espace/messages/{c.id}",
            "title": pro_name(c.pro, p) + (f" · {STATUTS[p.statut]['name']}" if p and p.statut else ""),
            "sub": _excerpt(last[c.id].body),
            "badge": badge(c.pro, p),
            "ts": _ms(c.last_message_at),
            "action": "Lire",
        })
    return {
        "title": _plural(len(unread), "professionnel vous a écrit", "professionnels vous ont écrit"),
        "items": items, "footer": ["Voir la messagerie", "/espace/messages"], "unread": len(unread), "newRequests": 0,
    }


def pro_notifs(db: Db, user: User, pro: ProProfile) -> dict:
    """Professionnel : nouvelles demandes (tout de suite, ou 48 h après en formule gratuite) et messages non lus."""
    convs = db.scalars(
        select(Conversation).where(Conversation.pro_id == user.id).order_by(Conversation.last_message_at.desc())
        .options(joinedload(Conversation.request).joinedload(ServiceRequest.client))
    ).all()
    last = last_messages(db, [c.id for c in convs])
    unread = _unread(list(convs), last, user.id, "pro_read_at")

    fresh = []
    if pro.state == "VERIFIED":
        delay = timedelta(hours=FREE_ALERT_DELAY_H) if PLANS_ACTIVE and active_plan(pro.plan, pro.plan_until) == "FREE" else timedelta(0)
        since = pro.requests_seen_at or pro.decided_at or pro.created_at
        t = now()
        reqs = db.scalars(
            select(ServiceRequest).where(ServiceRequest.state == "PUBLISHED", ServiceRequest.published_at > since - delay)
            .order_by(ServiceRequest.published_at.desc()).limit(50)
        ).all()
        counts = conversation_counts(db, [r.id for r in reqs])
        mine = my_conversations(db, user.id, [r.id for r in reqs])
        for r in reqs:
            ts = r.published_at + delay
            if (pro_sees(pro, r) and (not pro.specialties or r.category in pro.specialties)
                    and r.id not in mine and since < ts <= t):
                fresh.append((r, counts.get(r.id, 0), ts))

    items = []
    for r, taken, ts in fresh:
        left = MAX_PROS_PER_REQUEST - taken
        places = "complète" if left <= 0 else f"{left} place{'s' if left > 1 else ''} sur {MAX_PROS_PER_REQUEST}"
        items.append({
            "id": f"r-{r.id}", "href": f"/pro/demandes/{r.id}", "title": r.service,
            "sub": f"{r.commune} · budget {fcfa(r.budget)} · {places}", "badge": r.category, "icon": True, "ts": _ms(ts), "action": "Voir",
        })
    for c in unread:
        cl = c.request.client
        items.append({
            "id": f"m-{c.id}-{_ms(c.last_message_at)}", "href": f"/pro/messages/{c.id}",
            "title": f"{client_name(cl, c.shared)} vous a répondu", "sub": _excerpt(last[c.id].body),
            "badge": initials(cl.first_name, cl.last_name), "ts": _ms(c.last_message_at), "action": "Lire",
        })
    parts = [x for x in (
        fresh and _plural(len(fresh), "nouvelle demande", "nouvelles demandes"),
        unread and _plural(len(unread), "message", "messages"),
    ) if x]
    return {
        "title": " et ".join(parts),
        "items": items[:6],
        "footer": ["Voir les demandes", "/pro/demandes"] if fresh else ["Voir la messagerie", "/pro/messages"],
        "unread": len(unread),
        "newRequests": len(fresh),
    }
