from datetime import timedelta
import hashlib
import hmac
import secrets

import jwt
from fastapi import HTTPException

from ogk_server.config import Settings
from ogk_server.db.models import utcnow, new_id


ACCESS_SECONDS = 15 * 60
AGENT_SCOPES = ["heartbeat", "policy", "enroll", "realtime", "events"]


def unauthorized() -> HTTPException:
    return HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})


def secret_hash(settings: Settings, purpose: str, value: str) -> str:
    # Keyed hashing protects the small enrollment-code space from offline search.
    return hmac.new(settings.jwt_secret.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(48)


def encode_access(settings: Settings, *, subject: str, kind: str, **claims) -> str:
    now = utcnow()
    return jwt.encode({
        "iss": settings.issuer, "aud": settings.audience, "sub": subject,
        "iat": now, "nbf": now, "exp": now + timedelta(seconds=ACCESS_SECONDS),
        "jti": new_id(), "kind": kind, "token_type": "access", **claims,
    }, settings.jwt_secret, algorithm="HS256")


def decode_access(settings: Settings, token: str) -> dict:
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"],
            issuer=settings.issuer, audience=settings.audience,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti", "kind", "token_type"]},
        )
        if (claims["token_type"] != "access" or not isinstance(claims["kind"], str)
                or claims["kind"] not in {"parent", "agent"}):
            raise unauthorized()
        return claims
    except jwt.InvalidTokenError:
        raise unauthorized() from None
