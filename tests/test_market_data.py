from datetime import UTC, datetime

import httpx
import pytest

from app.domain.schemas import QuoteSnapshot
from app.services.market_data import (
    EastmoneyAshareMarketDataProvider,
    FinnhubMarketDataProvider,
    HybridMarketDataProvider,
    MarketDataService,
    MockMarketDataProvider,
)


@pytest.mark.asyncio
async def test_finnhub_provider_parses_realtime_quote() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/quote"
        return httpx.Response(
            200,
            json={
                "c": 189.98,
                "d": 1.23,
                "dp": 0.65,
                "h": 191.2,
                "l": 187.8,
                "o": 188.1,
                "pc": 188.75,
                "t": 1_717_171_717,
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://finnhub.io/api/v1", transport=transport)
    provider = FinnhubMarketDataProvider(api_key="test", timeout_seconds=3, client=client)

    quote = await provider.get_quote("aapl")

    assert quote.symbol == "AAPL"
    assert quote.current_price == 189.98
    assert quote.previous_close == 188.75
    assert quote.source == "finnhub"
    assert quote.is_realtime is True
    assert quote.updated_at == datetime.fromtimestamp(1_717_171_717, tz=UTC)
    await provider.close()


@pytest.mark.asyncio
async def test_market_data_service_caches_quotes() -> None:
    service = MarketDataService(MockMarketDataProvider(), cache_ttl_seconds=30)

    first = await service.get_quote("MSFT")
    second = await service.get_quote("msft")

    assert first is second
    assert first.symbol == "MSFT"
    assert first.is_realtime is False


@pytest.mark.asyncio
async def test_eastmoney_provider_parses_ashare_quote() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/qt/stock/get"
        assert request.url.params["secid"] == "1.600519"
        return httpx.Response(
            200,
            json={
                "rc": 0,
                "data": {
                    "f43": 127598,
                    "f44": 130400,
                    "f45": 127100,
                    "f46": 129000,
                    "f57": "600519",
                    "f58": "贵州茅台",
                    "f60": 130300,
                    "f86": 1_779_951_905,
                    "f169": -2702,
                    "f170": -207,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://push2.eastmoney.com", transport=transport)
    provider = EastmoneyAshareMarketDataProvider(timeout_seconds=3, client=client)

    quote = await provider.get_quote("600519.SH")

    assert quote.symbol == "600519.SH"
    assert quote.current_price == 1275.98
    assert quote.previous_close == 1303
    assert quote.change_percent == -2.07
    assert quote.currency == "CNY"
    assert quote.source == "eastmoney-unofficial"
    assert quote.is_realtime is False
    await provider.close()


@pytest.mark.asyncio
async def test_hybrid_provider_routes_a_share_and_us_symbols() -> None:
    class StaticProvider:
        def __init__(self, quote: QuoteSnapshot) -> None:
            self.quote = quote
            self.calls: list[str] = []

        async def get_quote(self, symbol: str) -> QuoteSnapshot:
            self.calls.append(symbol)
            return self.quote

        async def close(self) -> None:
            return None

    ashare_quote = QuoteSnapshot(
        symbol="000001.SZ",
        current_price=10,
        updated_at=datetime.now(UTC),
        source="a",
        is_realtime=False,
    )
    us_quote = QuoteSnapshot(
        symbol="AAPL",
        current_price=200,
        updated_at=datetime.now(UTC),
        source="u",
        is_realtime=True,
    )
    ashare_provider = StaticProvider(ashare_quote)
    us_provider = StaticProvider(us_quote)
    provider = HybridMarketDataProvider(ashare_provider, us_provider)

    assert await provider.get_quote("000001.SZ") == ashare_quote
    assert await provider.get_quote("AAPL") == us_quote
    assert ashare_provider.calls == ["000001.SZ"]
    assert us_provider.calls == ["AAPL"]
