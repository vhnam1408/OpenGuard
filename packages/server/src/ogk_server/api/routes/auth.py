from fastapi import APIRouter, Request, Response

from ogk_server.api.dependencies.auth import Config, CurrentParent, Db
from ogk_server.api.schemas import AccessResponse, LoginRequest, RefreshRequest, TokenPairResponse
from ogk_server.services import auth


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AccessResponse)
def login(payload: LoginRequest, request: Request, db: Db, settings: Config):
    return auth.login(db, settings, request, payload.email, payload.password)


@router.post("/refresh", response_model=TokenPairResponse)
def refresh(payload: RefreshRequest, request: Request, db: Db, settings: Config):
    # An expired access token is allowed; the one-use refresh secret authenticates.
    return auth.refresh(db, settings, request, payload.refresh_token)


@router.post("/logout", status_code=204)
def logout(db: Db, parent: CurrentParent):
    db.delete(parent.session)
    db.commit()
    return Response(status_code=204)
