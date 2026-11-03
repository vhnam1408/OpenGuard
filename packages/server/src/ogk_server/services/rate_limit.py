from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ogk_server.config import Settings
from ogk_server.db.models import RateLimit, utcnow
from ogk_server.security.tokens import secret_hash


def client_ip(request: Request) -> str:
    # Trust the ASGI peer only; proxy header trust must be configured in Uvicorn.
    return request.client.host if request.client else "unknown"


def limit(db: Session, settings: Settings, identity: str, maximum: int, seconds: int) -> None:
    """Persistent fixed window, serialized with the enclosing write transaction."""
    now = utcnow()
    db.execute(delete(RateLimit).where(RateLimit.expires_at <= now))
    key = secret_hash(settings, "rate-limit", identity)
    bucket = db.get(RateLimit, key)
    if bucket is None:
        bucket = RateLimit(key=key, hits=1, expires_at=now + timedelta(seconds=seconds))
        db.add(bucket)
    else:
        bucket.hits += 1
    if bucket.hits > maximum:
        retry = max(1, int((bucket.expires_at - now).total_seconds()))
        db.commit()
        raise HTTPException(429, "Too many attempts", headers={"Retry-After": str(retry)})
