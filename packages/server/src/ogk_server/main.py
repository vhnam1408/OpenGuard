from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from ogk_server.api.routes import auth, enroll, events, heartbeat, policy
from ogk_server.config import Settings
from ogk_server.db.session import create_database, initialize_database
from ogk_server.security.passwords import hash_password
from ogk_server.security.tokens import new_secret
from ogk_server.realtime.ws_handler import router as commands_router, ws_router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, sessions = create_database(config.database_url)
        app.state.sessions = sessions
        app.state.engine = engine
        app.state.dummy_password_hash = hash_password(new_secret())
        try:
            initialize_database(engine)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="OpenGuard Kids Server", version="0.1.0", lifespan=lifespan)
    app.state.settings = config

    @app.middleware("http")
    async def prevent_credential_caching(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(OperationalError)
    async def database_busy(request: Request, error: OperationalError):
        # Never echo SQL parameters (which may contain authentication state).
        if getattr(error.orig, "sqlite_errorcode", None) in {5, 6, 517}:
            return JSONResponse(status_code=503, content={"detail": "Database busy; retry shortly"},
                                headers={"Retry-After": "1"})
        return JSONResponse(status_code=500, content={"detail": "Database operation failed"})

    for router in (auth.router, enroll.router, heartbeat.router, policy.router, events.router, commands_router):
        app.include_router(router, prefix="/api/v1")
    app.include_router(ws_router)
    return app
