from datetime import timedelta
import secrets

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ogk_server.api.dependencies.auth import AgentContext, ParentContext
from ogk_server.api.schemas import CompleteEnrollmentRequest
from ogk_server.config import Settings
from ogk_server.db.models import Binding, Child, Device, EnrollmentCode, Parent, new_id, utcnow
from ogk_server.security.policy import export_key, initial_policy, signed_policy
from ogk_server.security.tokens import new_secret, secret_hash
from ogk_server.services.auth import token_pair
from ogk_server.services.rate_limit import client_ip, limit


CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def issue_code(db: Session, settings: Settings, request: Request, parent: ParentContext, child_id: str) -> dict:
    now = utcnow()
    limit(db, settings, f"enroll-create:{parent.parent.id}", 5, 600)
    limit(db, settings, f"enroll-create-ip:{client_ip(request)}", 20, 600)
    child = db.scalar(select(Child).where(Child.id == child_id, Child.parent_id == parent.parent.id, Child.is_active.is_(True)))
    if child is None:
        db.commit()
        raise HTTPException(404, "Child profile not found")
    db.execute(delete(EnrollmentCode).where(EnrollmentCode.expires_at <= now))
    # A new code replaces the previous outstanding code for this child.
    db.execute(update(EnrollmentCode).where(EnrollmentCode.child_id == child_id,
               EnrollmentCode.consumed_at.is_(None)).values(consumed_at=now))
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        digest = secret_hash(settings, "enrollment", code)
        if db.scalar(select(EnrollmentCode.id).where(EnrollmentCode.code_hash == digest)) is None:
            break
    expiry = now + timedelta(minutes=10)
    db.add(EnrollmentCode(child_id=child.id, code_hash=digest, expires_at=expiry, consented_at=now))
    db.commit()
    return {"code": code, "expires_at": expiry, "expires_in": 600}


def complete(db: Session, settings: Settings, request: Request, payload: CompleteEnrollmentRequest,
             agent: AgentContext | None) -> dict:
    now = utcnow()
    limit(db, settings, f"enroll-complete-ip:{client_ip(request)}", 20, 600)
    limit(db, settings, f"enroll-complete-code:{payload.code}", 5, 600)
    record = db.scalar(select(EnrollmentCode).where(
        EnrollmentCode.code_hash == secret_hash(settings, "enrollment", payload.code),
        EnrollmentCode.consumed_at.is_(None), EnrollmentCode.expires_at > now,
    ))
    child = db.get(Child, record.child_id) if record else None
    parent = db.get(Parent, child.parent_id) if child else None
    if record is None or child is None or not child.is_active or parent is None or not parent.is_active:
        db.commit()
        raise HTTPException(400, "Invalid, expired or consumed enrollment code")
    installation_hash = secret_hash(settings, "installation", payload.installation_ref)
    device = db.scalar(select(Device).where(Device.installation_hash == installation_hash))
    if device is not None:
        # An installation fingerprint is an identifier, never authentication.
        if agent is None or agent.device.id != device.id or not device.is_active:
            db.commit()
            raise HTTPException(409, "Existing installation requires its active Agent token")
        previous = db.scalars(select(Binding).where(Binding.device_id == device.id, Binding.sid_ref == payload.sid_ref)).all()
        if any(b.status == "active" or b.child_id != child.id for b in previous):
            db.commit()
            raise HTTPException(409, "SID reference is already bound; explicit unpair/transfer is required")
    else:
        if agent is not None:
            db.commit()
            raise HTTPException(409, "Installation reference does not match the authenticated device")
        device = Device(id=new_id(), installation_hash=installation_hash)
        db.add(device)
        db.flush()
    # Conditional consume plus BEGIN IMMEDIATE makes redemption single-use even
    # across processes. Everything below commits or rolls back as one unit.
    result = db.execute(update(EnrollmentCode).where(
        EnrollmentCode.id == record.id, EnrollmentCode.consumed_at.is_(None), EnrollmentCode.expires_at > now,
    ).values(consumed_at=now))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "Invalid, expired or consumed enrollment code")
    cleanup_token = new_secret()
    binding = Binding(
        id=new_id(), device_id=device.id, child_id=child.id, sid_ref=payload.sid_ref,
        token_version=1, cleanup_token_hash=secret_hash(settings, "cleanup", cleanup_token),
    )
    db.add(binding)
    db.flush()
    policy = initial_policy(binding)
    db.add(policy)
    credentials = token_pair(db, settings, binding)
    response = {
        **credentials, "device_id": device.id, "binding_id": binding.id,
        "child_id": child.id, "data_epoch": child.data_epoch,
        "policy": signed_policy(settings, policy),
        "policy_verification_key": export_key(settings, binding.id),
        "cleanup_token": cleanup_token, "heartbeat_interval_sec": 60,
    }
    db.commit()
    return response
