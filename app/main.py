from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import Settings, get_settings, set_settings_override
from app.runtime import close_runtime, configure_runtime

# 静态前端与后端放在同一个 FastAPI 应用里提供服务。
# 这样本地启动时只需要一个 uvicorn 进程，浏览器访问根路径即可打开工作台。
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """管理 FastAPI 生命周期：启动时装配运行时，退出时释放外部连接。"""

    # get_settings() 会合并环境变量、.env、本地配置文件和测试注入的 override。
    # 所有服务实例都从同一份 settings 构建，避免前端状态与后端依赖不一致。
    settings = get_settings()
    await configure_runtime(app, settings)

    try:
        yield
    finally:
        # 关闭 httpx client、行情服务和 AI 服务，防止测试或热重载后连接泄漏。
        await close_runtime(app)


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用实例。

    settings 主要给测试使用：测试可以传入隔离配置，避免读取开发者本机的 .env。
    生产/本地运行时通常传 None，让 get_settings() 自行读取真实配置。
    """

    # Pydantic Settings 默认会缓存；每次创建 app 前设置 override 并清缓存，
    # 能保证测试用例之间不会串配置。
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
        # 根路径不暴露在 OpenAPI 里，只作为浏览器工作台入口。
        return FileResponse(STATIC_DIR / "index.html")

    return app


# uvicorn app.main:app 会读取这个模块级实例。
app = create_app()
