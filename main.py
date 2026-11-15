"""Customer Manager 앱 생성, 인증 설정 및 라우터 등록."""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI

from authentication import (
    AuthenticationRequired, authentication_required_response,
    load_signing_key,
)
from database import DEFAULT_DB_PATH, initialize_database, make_engine
from routers import auth, customers, dashboard, health, home


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize_database(db_path)
        app.state.jwt_secret = load_signing_key(db_path)
        app.state.engine = make_engine(db_path)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(title="Customer Manager", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_exception_handler(AuthenticationRequired, authentication_required_response)

    app.include_router(home.router)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(customers.router)
    app.include_router(dashboard.router)

    return app


app = create_app()
