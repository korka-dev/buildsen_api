"""Modèles SQLAlchemy calqués sur le schéma existant (créé par Prisma) : mêmes tables, mêmes colonnes, mêmes types.

Les tables et colonnes gardent leurs noms d'origine (« User », « firstName »…) ; les attributs Python sont en snake_case.
Ainsi la version FastAPI peut travailler sur la base actuelle sans migration de données.
"""

from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .utils import cuid, now


def pg_enum(name: str, *values: str) -> ENUM:
    # Types énumérés déjà présents dans la base (créés par les migrations SQL d'origine) : jamais recréés par SQLAlchemy
    return ENUM(*values, name=name, create_type=False)


ROLE = pg_enum("Role", "CLIENT", "PRO", "ADMIN")
ADMIN_ROLE = pg_enum("AdminRole", "SUPER", "VERIF", "SUPPORT", "MODER")
ACCOUNT_STATUS = pg_enum("AccountStatus", "ACTIVE", "SUSPENDED")
PRO_STATUT = pg_enum("ProStatut", "ING", "TECH", "ENT", "ARCH")
DOSSIER_STATE = pg_enum("DossierState", "DRAFT", "PENDING", "MORE", "VERIFIED", "REFUSED")
PLAN = pg_enum("Plan", "FREE", "PRO", "PREMIUM")
DOC_STATE = pg_enum("DocState", "RECEIVED", "ACCEPTED", "REFUSED")
REQUEST_STATE = pg_enum("RequestState", "PENDING", "PUBLISHED", "REJECTED", "CLOSED")
REPORT_STATE = pg_enum("ReportState", "OPEN", "CLOSED")

TS = DateTime(timezone=False)


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[str]:
    return mapped_column("id", Text, primary_key=True, default=cuid)


def created() -> Mapped[datetime]:
    return mapped_column("createdAt", TS, default=now, nullable=False)


def updated() -> Mapped[datetime]:
    return mapped_column("updatedAt", TS, default=now, onupdate=now, nullable=False)


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    phone: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column("firstName", Text)
    last_name: Mapped[str] = mapped_column("lastName", Text)
    password_hash: Mapped[str | None] = mapped_column("passwordHash", Text)
    role: Mapped[str] = mapped_column(ROLE)
    admin_role: Mapped[str | None] = mapped_column("adminRole", ADMIN_ROLE)
    status: Mapped[str] = mapped_column(ACCOUNT_STATUS, default="ACTIVE")
    totp_secret: Mapped[str | None] = mapped_column("totpSecret", Text)
    totp_enabled: Mapped[bool] = mapped_column("totpEnabled", Boolean, default=False)
    token_hash: Mapped[str | None] = mapped_column("tokenHash", Text, unique=True)
    token_purpose: Mapped[str | None] = mapped_column("tokenPurpose", Text)
    token_expires_at: Mapped[datetime | None] = mapped_column("tokenExpiresAt", TS)
    email_verified_at: Mapped[datetime | None] = mapped_column("emailVerifiedAt", TS)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    pro: Mapped["ProProfile | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Session(Base):
    __tablename__ = "Session"

    id: Mapped[str] = pk()
    token_hash: Mapped[str] = mapped_column("tokenHash", Text, unique=True)
    user_id: Mapped[str] = mapped_column("userId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", TS)
    created_at: Mapped[datetime] = created()

    user: Mapped[User] = relationship(lazy="joined")


class ProProfile(Base):
    __tablename__ = "ProProfile"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column("userId", Text, ForeignKey("User.id", ondelete="CASCADE"), unique=True)
    statut: Mapped[str | None] = mapped_column(PRO_STATUT)
    company_name: Mapped[str | None] = mapped_column("companyName", Text)
    state: Mapped[str] = mapped_column(DOSSIER_STATE, default="DRAFT")
    school: Mapped[str | None] = mapped_column(Text)
    diploma: Mapped[str | None] = mapped_column(Text)
    experience_years: Mapped[int | None] = mapped_column("experienceYears", Integer)
    bio: Mapped[str | None] = mapped_column(Text)
    software: Mapped[str | None] = mapped_column(Text)
    specialties: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    commune: Mapped[str | None] = mapped_column(Text)
    whatsapp: Mapped[str | None] = mapped_column(Text)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    welcome_granted: Mapped[bool] = mapped_column("welcomeGranted", Boolean, default=False)
    plan: Mapped[str] = mapped_column(PLAN, default="FREE")
    plan_until: Mapped[datetime | None] = mapped_column("planUntil", TS)
    launch_offer: Mapped[bool] = mapped_column("launchOffer", Boolean, default=False)
    requests_seen_at: Mapped[datetime | None] = mapped_column("requestsSeenAt", TS)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    spotlight: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column("submittedAt", TS)
    decided_at: Mapped[datetime | None] = mapped_column("decidedAt", TS)
    decision_reason: Mapped[str | None] = mapped_column("decisionReason", Text)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    user: Mapped[User] = relationship(back_populates="pro")
    credentials: Mapped[list["Credential"]] = relationship(back_populates="pro", cascade="all, delete-orphan", order_by="Credential.created_at")
    portfolio: Mapped[list["PortfolioItem"]] = relationship(back_populates="pro", cascade="all, delete-orphan", order_by="PortfolioItem.created_at.desc()")


class Credential(Base):
    """Justificatif : fichier privé dans le dossier Supabase « justificatifs »."""

    __tablename__ = "Credential"

    id: Mapped[str] = pk()
    pro_id: Mapped[str] = mapped_column("proId", Text, ForeignKey("ProProfile.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, unique=True)
    file_name: Mapped[str] = mapped_column("fileName", Text)
    mime: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(DOC_STATE, default="RECEIVED")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()

    pro: Mapped[ProProfile] = relationship(back_populates="credentials")


class PortfolioItem(Base):
    """Réalisation du portfolio : fichier privé dans le dossier Supabase « portfolios »."""

    __tablename__ = "PortfolioItem"

    id: Mapped[str] = pk()
    pro_id: Mapped[str] = mapped_column("proId", Text, ForeignKey("ProProfile.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text, unique=True)
    caption: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created()

    pro: Mapped[ProProfile] = relationship(back_populates="portfolio")


class CreditLedger(Base):
    """Ancien modèle de crédits, conservé pour la compatibilité du schéma (non utilisé)."""

    __tablename__ = "CreditLedger"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column("userId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column("actorId", Text)
    expires_at: Mapped[datetime | None] = mapped_column("expiresAt", TS)
    created_at: Mapped[datetime] = created()


class AuditLog(Base):
    """Journal d'audit en écriture seule."""

    __tablename__ = "AuditLog"

    id: Mapped[str] = pk()
    actor_id: Mapped[str | None] = mapped_column("actorId", Text, ForeignKey("User.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()

    actor: Mapped[User | None] = relationship()


class ServiceRequest(Base):
    """Demande déposée par un particulier (budget obligatoire)."""

    __tablename__ = "ServiceRequest"

    id: Mapped[str] = pk()
    ref: Mapped[str] = mapped_column(Text, unique=True)
    client_id: Mapped[str] = mapped_column("clientId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(Text)
    service: Mapped[str] = mapped_column(Text)
    building_type: Mapped[str] = mapped_column("buildingType", Text)
    description: Mapped[str] = mapped_column(Text)
    commune: Mapped[str] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    land_status: Mapped[str] = mapped_column("landStatus", Text)
    phase: Mapped[str] = mapped_column(Text)
    levels: Mapped[str | None] = mapped_column(Text)
    rooms: Mapped[int | None] = mapped_column(Integer)
    surface: Mapped[int | None] = mapped_column(Integer)
    dims: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[str] = mapped_column(Text)
    budget: Mapped[int] = mapped_column(Integer)
    photo_path: Mapped[str | None] = mapped_column("photoPath", Text)  # photo du terrain ou croquis (facultatif), dossier privé « demandes »
    photo_mime: Mapped[str | None] = mapped_column("photoMime", Text)
    state: Mapped[str] = mapped_column(REQUEST_STATE, default="PENDING")
    reject_reason: Mapped[str | None] = mapped_column("rejectReason", Text)
    published_at: Mapped[datetime | None] = mapped_column("publishedAt", TS)
    free_notified_at: Mapped[datetime | None] = mapped_column("freeNotifiedAt", TS)
    closed_at: Mapped[datetime | None] = mapped_column("closedAt", TS)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()

    client: Mapped[User] = relationship()
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="request", order_by="Conversation.last_message_at.desc()")
    reports: Mapped[list["Report"]] = relationship(back_populates="request")


class Conversation(Base):
    """Conversation entre un professionnel et le client d'une demande (5 au maximum par demande)."""

    __tablename__ = "Conversation"

    id: Mapped[str] = pk()
    request_id: Mapped[str] = mapped_column("requestId", Text, ForeignKey("ServiceRequest.id", ondelete="CASCADE"))
    pro_id: Mapped[str] = mapped_column("proId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    shared_at: Mapped[datetime | None] = mapped_column("sharedAt", TS)
    client_read_at: Mapped[datetime | None] = mapped_column("clientReadAt", TS)
    pro_read_at: Mapped[datetime | None] = mapped_column("proReadAt", TS)
    client_mailed_at: Mapped[datetime | None] = mapped_column("clientMailedAt", TS)
    pro_mailed_at: Mapped[datetime | None] = mapped_column("proMailedAt", TS)
    last_message_at: Mapped[datetime] = mapped_column("lastMessageAt", TS, default=now)
    created_at: Mapped[datetime] = created()

    request: Mapped[ServiceRequest] = relationship(back_populates="conversations")
    pro: Mapped[User] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "Message"

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column("conversationId", Text, ForeignKey("Conversation.id", ondelete="CASCADE"))
    sender_id: Mapped[str] = mapped_column("senderId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Report(Base):
    """Signalement d'une conversation ou d'une demande par un utilisateur."""

    __tablename__ = "Report"

    id: Mapped[str] = pk()
    reporter_id: Mapped[str] = mapped_column("reporterId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    conversation_id: Mapped[str | None] = mapped_column("conversationId", Text, ForeignKey("Conversation.id", ondelete="SET NULL"))
    request_id: Mapped[str | None] = mapped_column("requestId", Text, ForeignKey("ServiceRequest.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(REPORT_STATE, default="OPEN")
    handled_at: Mapped[datetime | None] = mapped_column("handledAt", TS)
    created_at: Mapped[datetime] = created()

    reporter: Mapped[User] = relationship()
    conversation: Mapped[Conversation | None] = relationship()
    request: Mapped[ServiceRequest | None] = relationship(back_populates="reports")


class Ad(Base):
    """Annonce d'un partenaire sur l'accueil (toujours signalée « Partenaire »)."""

    __tablename__ = "Ad"

    id: Mapped[str] = pk()
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    link_label: Mapped[str | None] = mapped_column("linkLabel", Text)
    href: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()


class Throttle(Base):
    """Limitation des tentatives (connexion, inscription, messages…)."""

    __tablename__ = "Throttle"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    reset_at: Mapped[datetime] = mapped_column("resetAt", TS)


__all__ = [
    "Ad", "AuditLog", "Base", "Conversation", "Credential", "CreditLedger", "Message", "PortfolioItem",
    "ProProfile", "Report", "ServiceRequest", "Session", "Throttle", "User",
]


class WaSession(Base):
    """Conversation WhatsApp avec un numéro : étape où en est l'assistant, réponses déjà données, consentement."""

    __tablename__ = "WaSession"

    phone: Mapped[str] = mapped_column(Text, primary_key=True)  # 9 chiffres, sans indicatif
    state: Mapped[str] = mapped_column(Text, default="NEW")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    user_id: Mapped[str | None] = mapped_column("userId", Text, ForeignKey("User.id", ondelete="SET NULL"))
    opted_out: Mapped[bool] = mapped_column("optedOut", Boolean, default=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column("lastInboundAt", TS)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class WaMessage(Base):
    """Journal des messages WhatsApp (reçus et envoyés) : anti-doublon, suivi des envois, supervision. Purgé après 90 jours."""

    __tablename__ = "WaMessage"
    __table_args__ = (Index("WaMessage_phone_createdAt_idx", "phone", "createdAt"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # identifiant WhatsApp (ou généré pour le simulateur)
    phone: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text)  # IN | OUT
    kind: Mapped[str] = mapped_column(Text)  # text | audio | buttons | template | other
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="sent")  # sent | delivered | read | failed
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()


class WaCode(Base):
    """Code à usage unique envoyé sur WhatsApp pour se connecter au site (valable 10 minutes, 5 essais)."""

    __tablename__ = "WaCode"

    phone: Mapped[str] = mapped_column(Text, primary_key=True)
    code_hash: Mapped[str] = mapped_column("codeHash", Text)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", TS)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    """Réglages modifiables par l'administration (par exemple l'interrupteur de l'assistant WhatsApp)."""

    __tablename__ = "AppSetting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Payment(Base):
    """Paiement d'une formule par un professionnel (Wave, Orange Money…). La formule n'est activée qu'après confirmation du fournisseur."""

    __tablename__ = "Payment"
    __table_args__ = (Index("Payment_userId_createdAt_idx", "userId", "createdAt"),)

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column("userId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    plan: Mapped[str] = mapped_column(Text)
    months: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(Text)
    token: Mapped[str] = mapped_column(Text, unique=True)  # référence chez le fournisseur ; sert aussi de lien secret
    state: Mapped[str] = mapped_column(Text, default="PENDING")  # PENDING, PAID, FAILED, EXPIRED
    source: Mapped[str] = mapped_column(Text, default="web")  # web ou wa
    checkout_url: Mapped[str] = mapped_column("checkoutUrl", Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    paid_at: Mapped[datetime | None] = mapped_column("paidAt", TS)
    created_at: Mapped[datetime] = created()


class Review(Base):
    """Avis d'un particulier sur un professionnel, après une conversation où le professionnel a répondu (un seul avis par conversation)."""

    __tablename__ = "Review"
    __table_args__ = (Index("Review_proId_createdAt_idx", "proId", "createdAt"),)

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column("conversationId", Text, ForeignKey("Conversation.id", ondelete="CASCADE"), unique=True)
    pro_id: Mapped[str] = mapped_column("proId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column("clientId", Text, ForeignKey("User.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="VISIBLE")  # VISIBLE ou HIDDEN (masqué par la modération)
    created_at: Mapped[datetime] = created()

    client: Mapped[User] = relationship(foreign_keys=[client_id])
