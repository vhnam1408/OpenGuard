from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ogk_server.config import Settings
from ogk_server.db.models import Binding, Child, Device, Parent, ParentSession, RefreshToken, new_id, utcnow
from ogk_server.security.passwords import password_hasher, verify_password
from ogk_server.security.tokens import (
    ACCESS_SECONDS, AGENT_SCOPES, encode_access, new_secret, secret_hash, unauthorized,
)
from ogk_server.services.rate_limit import client_ip, limit


def login(db: Session, settings: Settings, request: Request, email: str, password: str) -> dict:
    now = utcnow()
    limit(db, settings, f"login-ip:{client_ip(request)}", 20, 900)
    limit(db, settings, f"login-account:{email}", 20, 900)
    parent = db.scalar(select(Parent).where(Parent.email == email))
    if parent and parent.locked_until and parent.locked_until <= now:
        parent.failed_login_attempts = 0
        parent.locked_until = None
    candidate = parent.password_hash if parent else request.app.state.dummy_password_hash
    valid = verify_password(password, candidate)
    if (not parent or not parent.is_active or (parent.locked_until and parent.locked_until > now) or not valid):
        if parent and parent.is_active and not parent.locked_until:
            parent.failed_login_attempts += 1
            if parent.failed_login_attempts >= 5:
                parent.locked_until = now + timedelta(minutes=15)
        db.commit()  # failed-attempt counters must survive HTTP 401
        raise unauthorized()
    parent.failed_login_attempts = 0
    parent.locked_until = None
    if password_hasher.check_needs_rehash(parent.password_hash):
        parent.password_hash = password_hasher.hash(password)
    db.execute(delete(ParentSession).where(ParentSession.expires_at <= now))
    session = ParentSession(id=new_id(), parent_id=parent.id, expires_at=now + timedelta(seconds=ACCESS_SECONDS))
    db.add(session)
    token = encode_access(settings, subject=parent.id, kind="parent", jti=session.id)
    db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": ACCESS_SECONDS}


def token_pair(db: Session, settings: Settings, binding: Binding, *, family_id: str | None = None,
               expires_at=None) -> dict:
    refresh = new_secret()
    expiry = expires_at or utcnow() + timedelta(days=settings.refresh_days)
    db.add(RefreshToken(binding_id=binding.id, token_hash=secret_hash(settings, "refresh", refresh),
                        family_id=family_id or new_id(), expires_at=expiry))
    access = encode_access(
        settings, subject=binding.id, kind="agent", binding_id=binding.id,
        device_id=binding.device_id, child_id=binding.child_id,
        token_version=binding.token_version, scope=AGENT_SCOPES,
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "expires_in": ACCESS_SECONDS, "refresh_expires_at": expiry}


def refresh(db: Session, settings: Settings, request: Request, raw_token: str) -> dict:
    now = utcnow()
    limit(db, settings, f"refresh-ip:{client_ip(request)}", 30, 60)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == secret_hash(settings, "refresh", raw_token)))
    if record is None:
        db.commit()
        raise unauthorized()
    binding = db.get(Binding, record.binding_id)
    if record.used_at is not None and binding is not None:
        # Reuse invalidates even the access tokens issued by the newer rotation.
        binding.token_version += 1
        db.execute(update(RefreshToken).where(RefreshToken.binding_id == binding.id).values(revoked_at=now))
        db.commit()
        raise unauthorized()
    child = db.get(Child, binding.child_id) if binding else None
    device = db.get(Device, binding.device_id) if binding else None
    parent = db.get(Parent, child.parent_id) if child else None
    if (record.revoked_at is not None or record.expires_at <= now or binding is None
            or binding.status != "active" or not child or not child.is_active
            or not device or not device.is_active or not parent or not parent.is_active):
        db.commit()
        raise unauthorized()
    record.used_at = now
    pair = token_pair(db, settings, binding, family_id=record.family_id, expires_at=record.expires_at)
    db.commit()
    return pair
