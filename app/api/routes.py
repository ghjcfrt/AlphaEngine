import json

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from app.acp.message import ACPMessage
from app.agents.base import AgentInfo
from app.core.config import AI_AGENT_LABELS, get_settings, resolve_ai_model_settings
from app.core.local_config import LOCAL_CONFIG_PATH, load_local_config, save_local_config
from app.domain.schemas import (
    AgentAISettingsResponse,
    InvestmentPlanRequest,
    InvestmentPlanResponse,
    QuoteSnapshot,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdate,
)
from app.runtime import configure_runtime
from app.services.ai_advisor import AIAdvisorError
from app.services.market_data import MarketDataError

router = APIRouter()
api = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    ai_service = request.app.state.ai_advisor_service
    return {
        "status": "ok",
        "market_data_provider": settings.market_data_provider,
        "ai_advisor_provider": settings.ai_advisor_provider,
        "ai_runtime_provider": ai_service.provider_name,
        "ai_runtime_model": ai_service.provider_model,
        "ai_is_model_generated": ai_service.is_model_generated,
        "ai_agents": _ai_agent_responses(request),
    }


@api.get("/agents", response_model=list[AgentInfo])
async def list_agents(request: Request) -> list[AgentInfo]:
    return [agent.info for agent in request.app.state.agents]


@api.get("/settings", response_model=RuntimeSettingsResponse)
async def get_runtime_settings(request: Request) -> RuntimeSettingsResponse:
    return _settings_response(request)


@api.put("/settings", response_model=RuntimeSettingsResponse)
async def update_runtime_settings(
    request: Request,
    settings_update: RuntimeSettingsUpdate,
) -> RuntimeSettingsResponse:
    config = load_local_config()
    payload = settings_update.model_dump(exclude_unset=True)
    for key in [
        "market_data_provider",
        "ai_advisor_provider",
        "ai_model_family",
        "openai_base_url",
        "openai_model",
        "request_timeout_seconds",
        "quote_cache_ttl_seconds",
    ]:
        value = payload.get(key)
        if value is not None:
            config[key] = value

    try:
        _merge_secret(config, payload, "openai_api_key")
        _merge_secret(config, payload, "finnhub_api_key")
        _merge_secret(config, payload, "polygon_api_key")
        _merge_secret(config, payload, "alpha_vantage_api_key")
        _merge_ai_agents(config, payload.get("ai_agents"))
        save_local_config(config)
        get_settings.cache_clear()
        settings = get_settings()
        await configure_runtime(request.app, settings, close_existing=True)
    except (AIAdvisorError, MarketDataError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _settings_response(request)


@api.get("/market/quotes", response_model=list[QuoteSnapshot])
async def get_quotes(
    request: Request,
    symbols: str = Query(..., description="逗号分隔的证券代码，例如 AAPL,MSFT,SPY。"),
) -> list[QuoteSnapshot]:
    symbol_list = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    try:
        return await request.app.state.market_service.get_quotes(symbol_list)
    except (AIAdvisorError, MarketDataError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.post("/advice/plans", response_model=InvestmentPlanResponse)
async def create_plan(
    request: Request,
    plan_request: InvestmentPlanRequest,
) -> InvestmentPlanResponse:
    try:
        return await request.app.state.coordinator.create_plan(plan_request)
    except (AIAdvisorError, MarketDataError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.get("/acp/traces/{trace_id}", response_model=list[ACPMessage])
async def get_trace(request: Request, trace_id: str) -> list[ACPMessage]:
    return await request.app.state.acp_bus.list_trace(trace_id)


@api.websocket("/market/ws/{symbol}")
async def stream_symbol(websocket: WebSocket, symbol: str) -> None:
    await websocket.accept()
    settings = websocket.app.state.settings
    normalized = symbol.strip().upper()
    if not settings.finnhub_api_key:
        await websocket.send_json({"type": "error", "message": "FINNHUB_API_KEY is required."})
        await websocket.close(code=1008)
        return

    upstream_url = f"wss://ws.finnhub.io?token={settings.finnhub_api_key}"
    try:
        async with websockets.connect(upstream_url) as upstream:
            await upstream.send(json.dumps({"type": "subscribe", "symbol": normalized}))
            while True:
                message = await upstream.recv()
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)


router.include_router(api)


def _settings_response(request: Request) -> RuntimeSettingsResponse:
    settings = request.app.state.settings
    ai_service = request.app.state.ai_advisor_service
    return RuntimeSettingsResponse(
        market_data_provider=settings.market_data_provider,
        ai_advisor_provider=settings.ai_advisor_provider,
        ai_model_family=settings.ai_model_family,
        ai_runtime_provider=ai_service.provider_name,
        ai_runtime_model=ai_service.provider_model,
        ai_is_model_generated=ai_service.is_model_generated,
        openai_base_url=settings.openai_base_url,
        openai_model=settings.openai_model,
        request_timeout_seconds=settings.request_timeout_seconds,
        quote_cache_ttl_seconds=settings.quote_cache_ttl_seconds,
        has_openai_api_key=_has_secret(settings.openai_api_key),
        has_finnhub_api_key=_has_secret(settings.finnhub_api_key),
        has_polygon_api_key=_has_secret(settings.polygon_api_key),
        has_alpha_vantage_api_key=_has_secret(settings.alpha_vantage_api_key),
        local_config_path=str(LOCAL_CONFIG_PATH),
        ai_agents=_ai_agent_responses(request),
    )


def _ai_agent_responses(request: Request) -> dict[str, AgentAISettingsResponse]:
    settings = request.app.state.settings
    services = request.app.state.ai_advisor_services
    result: dict[str, AgentAISettingsResponse] = {}
    for agent_key, label in AI_AGENT_LABELS.items():
        model_settings = resolve_ai_model_settings(settings, agent_key)
        service = services[agent_key]
        result[agent_key] = AgentAISettingsResponse(
            label=label,
            ai_advisor_provider=model_settings.ai_advisor_provider,
            ai_model_family=model_settings.ai_model_family,
            ai_runtime_provider=service.provider_name,
            ai_runtime_model=service.provider_model,
            ai_is_model_generated=service.is_model_generated,
            openai_base_url=model_settings.openai_base_url,
            openai_model=model_settings.openai_model,
            has_openai_api_key=_has_secret(model_settings.openai_api_key),
        )
    return result


def _merge_ai_agents(config: dict[str, object], payload: object) -> None:
    if not isinstance(payload, dict):
        return
    existing = config.get("ai_agents")
    ai_agents = existing if isinstance(existing, dict) else {}
    for agent_key, raw_update in payload.items():
        if agent_key not in AI_AGENT_LABELS:
            raise ValueError(f"未知 AI Agent 配置：{agent_key}")
        if not isinstance(raw_update, dict):
            continue
        current = ai_agents.get(agent_key)
        agent_config = current if isinstance(current, dict) else {}
        for key in [
            "ai_advisor_provider",
            "ai_model_family",
            "openai_base_url",
            "openai_model",
        ]:
            value = raw_update.get(key)
            if value is not None:
                agent_config[key] = value
        _merge_secret(agent_config, raw_update, "openai_api_key")
        ai_agents[agent_key] = agent_config
    config["ai_agents"] = ai_agents


def _merge_secret(config: dict[str, object], payload: dict[str, object], key: str) -> None:
    if payload.get(f"clear_{key}"):
        config.pop(key, None)
        return
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        config[key] = value.strip()


def _has_secret(value: str | None) -> bool:
    return bool(value and value.strip() and value.strip() != "replace-me")
