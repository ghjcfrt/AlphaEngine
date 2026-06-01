from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import Settings, get_settings, set_settings_override
from app.runtime import close_runtime, configure_runtime

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await configure_runtime(app, settings)

    try:
        yield
    finally:
        await close_runtime(app)


def create_app(settings: Settings | None = None) -> FastAPI:
    set_settings_override(settings)

    app = FastAPI(
        title="AlphaEngine 多 AI 协作智能投顾",
        version="0.1.0",
        description="使用 ACP 风格 trace 的多 AI Agent 协作投资规划后端。",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def frontend() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
