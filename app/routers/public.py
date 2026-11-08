"""Pages publiques : vitrine de l'accueil, fiches des professionnels vérifiés, plan du site."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import joinedload, selectinload

from ..db import get_db
from ..deps import current_session
from ..errors import not_found
from ..models import Ad, ProProfile, ServiceRequest, Session, User
from ..rules import BIG_BUDGET, GRACE_DAYS, PLANS_ACTIVE, plan_rank
from ..serializers import ad_out, pro_card_out, request_out
from ..services.queries import conversation_counts
from ..services.reviews import public_reviews, ratings
from ..utils import iso, now

router = APIRouter(prefix="/api/public", tags=["Public"])


def _visible():
    return (ProProfile.state == "VERIFIED", ProProfile.visible.is_(True), User.status == "ACTIVE")


def _pros(db: Db, *where, order_by, limit: int) -> list[ProProfile]:
    return list(db.scalars(
        select(ProProfile).join(User).where(*_visible(), *where).order_by(*order_by).limit(limit)
        .options(joinedload(ProProfile.user), selectinload(ProProfile.portfolio))
    ).all())


@router.get("/home")
def home(db: Db = Depends(get_db)):
    """Vitrine commune à l'accueil public et à l'accueil des utilisateurs connectés."""
    shown = [ServiceRequest.state == "PUBLISHED"]
    if PLANS_ACTIVE:  # les gros budgets sont réservés aux abonnés : jamais sur la vitrine
        shown.append(ServiceRequest.budget < BIG_BUDGET)
    reqs = db.scalars(select(ServiceRequest).where(*shown).order_by(ServiceRequest.published_at.desc()).limit(6)).all()
    counts = conversation_counts(db, [r.id for r in reqs])
    spotlight = _pros(db, ProProfile.spotlight.is_(True), order_by=(ProProfile.updated_at.desc(),), limit=1)
    premium = and_(ProProfile.plan == "PREMIUM", ProProfile.plan_until > now() - timedelta(days=GRACE_DAYS)) if PLANS_ACTIVE else ProProfile.featured.is_(True)
    featured = _pros(db, or_(ProProfile.featured.is_(True), premium), order_by=(ProProfile.updated_at.desc(),), limit=8)
    recent = _pros(db, ProProfile.featured.is_(False), order_by=(ProProfile.decided_at.desc().nulls_last(),), limit=8)
    companies = [p for p in _pros(db, ProProfile.statut == "ENT", order_by=(ProProfile.featured.desc(), ProProfile.decided_at.desc().nulls_last()), limit=12) if p.portfolio][:4]
    ads = db.scalars(select(Ad).where(Ad.active.is_(True)).order_by(Ad.created_at.desc()).limit(5)).all()
    # « En vedette » d'abord, complété par les derniers profils vérifiés (avec réalisations en priorité)
    fill = sorted(recent, key=lambda p: 0 if p.portfolio else 1)
    seen, pros = set(), []
    for p in [*featured, *fill]:
        if p.id not in seen:
            seen.add(p.id)
            pros.append(p)
    rt = ratings(db, list({p.user_id for p in [*spotlight, *pros[:8], *companies]}))
    # Une annonce différente selon l'heure, parmi celles qui sont actives
    pros.sort(key=plan_rank)  # Premium, puis Pro, puis gratuits (ordre stable)
    ad = ads[datetime.now().hour % len(ads)] if ads else None
    return {
        "requests": [request_out(r, conversations=counts.get(r.id, 0)) for r in reqs],
        "spotlight": pro_card_out(spotlight[0], rating=rt.get(spotlight[0].user_id)) if spotlight else None,
        "pros": [pro_card_out(p, rating=rt.get(p.user_id)) for p in pros[:8]],
        "companies": [pro_card_out(p, rating=rt.get(p.user_id)) for p in companies],
        "ad": ad_out(ad) if ad else None,
        # Toutes les annonces actives (carrousel de l'espace connecté)
        "ads": [ad_out(a) for a in ads],
    }


@router.get("/pros/{pro_id}")
def pro_profile(pro_id: str, s: Session | None = Depends(current_session), db: Db = Depends(get_db)):
    """Fiche d'un professionnel vérifié : complète pour un particulier ou un pro connecté, aperçu pour un visiteur."""
    p = db.scalar(
        select(ProProfile).join(User).where(ProProfile.id == pro_id, *_visible())
        .options(joinedload(ProProfile.user), selectinload(ProProfile.portfolio))
    )
    if not p:
        raise not_found("Profil introuvable.")
    member = bool(s and s.user.role in ("CLIENT", "PRO"))
    rt = ratings(db, [p.user_id])
    card = pro_card_out(p, portfolio_limit=None if member else 1, rating=rt.get(p.user_id))
    if not member:
        # Aperçu : nom, statut, commune et une photo ; le reste est réservé aux membres
        card.update(bio=None, software=None, specialties=[], experienceYears=None)
    return {"pro": card, "full": member, "reviews": public_reviews(db, p.user_id)}


@router.get("/sitemap")
def sitemap(db: Db = Depends(get_db)):
    rows = db.execute(select(ProProfile.id, ProProfile.updated_at).join(User).where(*_visible())).all()
    return {"pros": [{"id": i, "updatedAt": iso(u)} for i, u in rows]}
