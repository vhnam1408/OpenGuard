from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ogk_server.config import Settings
from ogk_server.db.models import Binding, Child, Device, Parent, ParentSession, utcnow
from ogk_server.db.session import get_db
from ogk_server.security.tokens import decode_access, unauthorized, AGENT_SCOPES


Db = Annotated[Session, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)
Credential = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


Config = Annotated[Settings, Depends(get_settings)]


@dataclass
class ParentContext:
    parent: Parent
    session: ParentSession


@dataclass
class AgentContext:
    binding: Binding
    device: Device
    child: Child


def authenticate_agent(db: Session, settings: Settings, token: str) -> AgentContext:
    claims = decode_access(settings, token)
    if claims["kind"] != "agent" or not isinstance(claims.get("binding_id"), str):
        raise unauthorized()
    row = db.execute(
        select(Binding, Device, Child, Parent)
        .join(Device, Device.id == Binding.device_id)
        .join(Child, Child.id == Binding.child_id)
        .join(Parent, Parent.id == Child.parent_id)
        .where(Binding.id == claims["binding_id"])
    ).one_or_none()
    if row is None:
        raise unauthorized()
    binding, device, child, parent = row
    if (
        not all((device.is_active, child.is_active, parent.is_active))
        or binding.status != "active" or claims["sub"] != binding.id
        or claims.get("device_id") != device.id or claims.get("child_id") != child.id
        or type(claims.get("token_version")) is not int
        or claims["token_version"] != binding.token_version
        or claims.get("scope") != AGENT_SCOPES
    ):
        raise unauthorized()
    return AgentContext(binding, device, child)


def get_agent(db: Db, settings: Config, credentials: Credential) -> AgentContext:
    if credentials is None:
        raise unauthorized()
    return authenticate_agent(db, settings, credentials.credentials)


def get_optional_agent(db: Db, settings: Config, credentials: Credential) -> AgentContext | None:
    if credentials is None:
        return None
    return authenticate_agent(db, settings, credentials.credentials)


def get_parent(db: Db, settings: Config, credentials: Credential) -> ParentContext:
    if credentials is None:
        raise unauthorized()
    claims = decode_access(settings, credentials.credentials)
    if claims["kind"] != "parent":
        raise unauthorized()
    parent = db.get(Parent, claims["sub"])
    session = db.get(ParentSession, claims["jti"])
    if (parent is None or not parent.is_active or session is None
            or session.parent_id != parent.id or session.expires_at <= utcnow()):
        raise unauthorized()
    return ParentContext(parent, session)


CurrentParent = Annotated[ParentContext, Depends(get_parent)]
CurrentAgent = Annotated[AgentContext, Depends(get_agent)]
OptionalAgent = Annotated[AgentContext | None, Depends(get_optional_agent)]
