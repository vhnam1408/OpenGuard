"""Domain tables plus server-side enrollment/authentication state.

Event's seven activity fields remain unchanged; id/binding_id/data_epoch and
idempotency_key are storage/envelope metadata, never activity payload fields.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator):
    """SQLite stores naive UTC; Python always receives aware UTC."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("A timezone-aware datetime is required")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    children: Mapped[list["Child"]] = relationship(back_populates="parent", passive_deletes=True)


class Child(Base):
    __tablename__ = "children"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), index=True)
    # A local pseudonymous label is sufficient; do not collect DOB or real name.
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    data_epoch: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    parent: Mapped[Parent] = relationship(back_populates="children")
    bindings: Mapped[list["Binding"]] = relationship(back_populates="child", passive_deletes=True)
    __table_args__ = (CheckConstraint("data_epoch >= 1"),)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    installation_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    bindings: Mapped[list["Binding"]] = relationship(back_populates="device", passive_deletes=True)


class Binding(Base):
    __tablename__ = "bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    sid_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    cleanup_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reported_policy_version: Mapped[int] = mapped_column(Integer, default=0)
    quota_used_sec: Mapped[int] = mapped_column(Integer, default=0)
    agent_status: Mapped[str] = mapped_column(String(16), default="online")
    child: Mapped[Child] = relationship(back_populates="bindings")
    device: Mapped[Device] = relationship(back_populates="bindings")
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')"),
        CheckConstraint("quota_used_sec >= 0 AND reported_policy_version >= 0"),
        UniqueConstraint("id", "device_id", "child_id"),
        Index("uq_active_device_sid", "device_id", "sid_ref", unique=True,
              sqlite_where=text("status = 'active'")),
    )


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    binding_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str] = mapped_column(String(36))
    child_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["binding_id", "device_id", "child_id"],
                             ["bindings.id", "bindings.device_id", "bindings.child_id"], ondelete="CASCADE"),
        UniqueConstraint("binding_id", "version"),
        UniqueConstraint("id", "binding_id", "device_id", "child_id"),
        CheckConstraint("version >= 1"),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    binding_id: Mapped[str] = mapped_column(String(36), index=True)
    data_epoch: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    device_id: Mapped[str] = mapped_column(String(36))
    child_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(253))
    duration_sec: Mapped[float] = mapped_column(Float)
    policy_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = (
        ForeignKeyConstraint(["policy_id", "binding_id", "device_id", "child_id"],
                             ["policies.id", "policies.binding_id", "policies.device_id", "policies.child_id"],
                             ondelete="CASCADE"),
        UniqueConstraint("binding_id", "data_epoch", "idempotency_key"),
        CheckConstraint("duration_sec >= 0 AND data_epoch >= 1"),
        CheckConstraint("type IN ('app_start','app_stop','domain_query','blocked_app',"
                        "'blocked_domain','quota_warning','locked','unlock_request','override_granted')"),
    )


class Command(Base):
    """Durable outbox. A successful socket write is not an Applied ACK."""
    __tablename__ = "commands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    binding_id: Mapped[str] = mapped_column(ForeignKey("bindings.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(16))
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    ttl_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="Pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ack_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        UniqueConstraint("binding_id", "idempotency_key"),
        CheckConstraint("type IN ('lock_now', 'add_time')"),
        CheckConstraint("status IN ('Pending', 'Applied', 'Rejected', 'Expired')"),
        CheckConstraint("seconds >= 0 AND seconds <= 86400 AND ttl_seconds >= 5 AND ttl_seconds <= 300"),
        Index("ix_commands_delivery", "binding_id", "status", "expires_at"),
    )


class EnrollmentCode(Base):
    __tablename__ = "enrollment_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    consented_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    binding_id: Mapped[str] = mapped_column(ForeignKey("bindings.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Retain used hashes until family expiry to detect reuse after rotation.
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ParentSession(Base):
    __tablename__ = "parent_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
