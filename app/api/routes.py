import json

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from app.acp.message import ACPMessage
from app.agents.base import AgentInfo
from app.domain.schemas import InvestmentPlanRequest, InvestmentPlanResponse, QuoteSnapshot
from app.services.market_data import MarketDataError

router = APIRouter()
api = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {"status": "ok", "market_data_provider": settings.market_data_provider}


@api.get("/agents", response_model=list[AgentInfo])
async def list_agents(request: Request) -> list[AgentInfo]:
    return [agent.info for agent in request.app.state.agents]


@api.get("/market/quotes", response_model=list[QuoteSnapshot])
async def get_quotes(
    request: Request,
    symbols: str = Query(..., description="逗号分隔的证券代码，例如 AAPL,MSFT,SPY。"),
) -> list[QuoteSnapshot]:
    symbol_list = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    try:
        return await request.app.state.market_service.get_quotes(symbol_list)
    except (MarketDataError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.post("/advice/plans", response_model=InvestmentPlanResponse)
async def create_plan(
    request: Request,
    plan_request: InvestmentPlanRequest,
) -> InvestmentPlanResponse:
    try:
        return await request.app.state.coordinator.create_plan(plan_request)
    except (MarketDataError, httpx.HTTPError) as exc:
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
                await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)


router.include_router(api)
