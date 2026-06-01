import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.domain.schemas import QuoteSnapshot


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(Protocol):
    name: str

    async def get_quote(self, symbol: str) -> QuoteSnapshot: ...

    async def close(self) -> None: ...


class FinnhubMarketDataProvider:
    name = "finnhub"

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://finnhub.io/api/v1",
            timeout=timeout_seconds,
        )

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        if not self.api_key:
            raise MarketDataError("FINNHUB_API_KEY is required for real-time Finnhub quotes.")
        normalized = _normalize_symbol(symbol)
        response = await self.client.get(
            "/quote",
            params={"symbol": normalized, "token": self.api_key},
        )
        response.raise_for_status()
        payload = response.json()
        current_price = _required_positive_float(payload.get("c"), normalized)
        timestamp = int(payload.get("t") or time.time())
        updated_at = datetime.fromtimestamp(timestamp, tz=UTC)

        return QuoteSnapshot(
            symbol=normalized,
            current_price=current_price,
            open_price=_optional_float(payload.get("o")),
            previous_close=_optional_float(payload.get("pc")),
            high_price=_optional_float(payload.get("h")),
            low_price=_optional_float(payload.get("l")),
            change=_optional_float(payload.get("d")),
            change_percent=_optional_float(payload.get("dp")),
            updated_at=updated_at,
            source=self.name,
            is_realtime=True,
            data_delay_seconds=None,
            raw=payload,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class PolygonMarketDataProvider:
    name = "polygon"

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://api.polygon.io",
            timeout=timeout_seconds,
        )

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        if not self.api_key:
            raise MarketDataError("POLYGON_API_KEY is required for Polygon snapshot quotes.")
        normalized = _normalize_symbol(symbol)
        response = await self.client.get(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{normalized}",
            params={"apiKey": self.api_key},
        )
        response.raise_for_status()
        payload = response.json()
        ticker = payload.get("ticker") or {}
        last_trade = ticker.get("lastTrade") or {}
        day = ticker.get("day") or {}
        prev_day = ticker.get("prevDay") or {}
        minute = ticker.get("min") or {}
        current_price = _required_positive_float(
            last_trade.get("p") or minute.get("c") or day.get("c"),
            normalized,
        )
        updated_at = _polygon_timestamp(last_trade.get("t") or ticker.get("updated"))

        return QuoteSnapshot(
            symbol=normalized,
            current_price=current_price,
            open_price=_optional_float(day.get("o")),
            previous_close=_optional_float(prev_day.get("c")),
            high_price=_optional_float(day.get("h")),
            low_price=_optional_float(day.get("l")),
            change=_optional_float(ticker.get("todaysChange")),
            change_percent=_optional_float(ticker.get("todaysChangePerc")),
            updated_at=updated_at,
            source=self.name,
            is_realtime=True,
            data_delay_seconds=None,
            raw=payload,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class EastmoneyAshareMarketDataProvider:
    name = "eastmoney-unofficial"
    fields = "f43,f44,f45,f46,f57,f58,f60,f86,f169,f170"

    def __init__(
        self,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://push2.eastmoney.com",
            timeout=timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                )
            },
        )

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        parsed = _parse_ashare_symbol(symbol)
        if parsed is None:
            raise MarketDataError(f"{symbol} is not a supported A-share symbol.")

        response = await self.client.get(
            "/api/qt/stock/get",
            params={"secid": parsed.secid, "fields": self.fields},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        if not data:
            raise MarketDataError(f"Eastmoney returned no quote data for {symbol}.")

        current_price = _required_positive_float(
            _eastmoney_scaled_value(data.get("f43")),
            parsed.display_symbol,
        )
        timestamp = int(data.get("f86") or time.time())
        updated_at = datetime.fromtimestamp(timestamp, tz=UTC)

        return QuoteSnapshot(
            symbol=parsed.display_symbol,
            current_price=current_price,
            open_price=_eastmoney_scaled_value(data.get("f46")),
            previous_close=_eastmoney_scaled_value(data.get("f60")),
            high_price=_eastmoney_scaled_value(data.get("f44")),
            low_price=_eastmoney_scaled_value(data.get("f45")),
            change=_eastmoney_scaled_value(data.get("f169")),
            change_percent=_eastmoney_scaled_value(data.get("f170")),
            currency="CNY",
            updated_at=updated_at,
            source=self.name,
            is_realtime=False,
            data_delay_seconds=None,
            raw=payload,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class HybridMarketDataProvider:
    name = "hybrid"

    def __init__(
        self,
        ashare_provider: MarketDataProvider,
        us_provider: MarketDataProvider,
    ) -> None:
        self.ashare_provider = ashare_provider
        self.us_provider = us_provider

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        if _parse_ashare_symbol(symbol) is not None:
            return await self.ashare_provider.get_quote(symbol)
        return await self.us_provider.get_quote(symbol)

    async def close(self) -> None:
        await self.ashare_provider.close()
        await self.us_provider.close()


class MockMarketDataProvider:
    name = "mock"

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        normalized = _normalize_symbol(symbol)
        seed = sum(ord(char) for char in normalized)
        price = round(20 + (seed % 400) + (seed % 17) / 10, 2)
        previous_close = round(price * 0.992, 2)
        return QuoteSnapshot(
            symbol=normalized,
            current_price=price,
            open_price=round(price * 0.997, 2),
            previous_close=previous_close,
            high_price=round(price * 1.012, 2),
            low_price=round(price * 0.985, 2),
            change=round(price - previous_close, 2),
            change_percent=round((price - previous_close) / previous_close * 100, 2),
            updated_at=datetime.now(UTC),
            source=self.name,
            is_realtime=False,
            data_delay_seconds=None,
            raw={"mock": True},
        )

    async def close(self) -> None:
        return None


class MarketDataService:
    def __init__(self, provider: MarketDataProvider, cache_ttl_seconds: int = 2) -> None:
        self.provider = provider
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, QuoteSnapshot]] = {}

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        normalized = _normalize_symbol(symbol)
        now = time.monotonic()
        cached = self._cache.get(normalized)
        if cached and now - cached[0] <= self.cache_ttl_seconds:
            return cached[1]
        quote = await self.provider.get_quote(normalized)
        self._cache[normalized] = (now, quote)
        return quote

    async def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        normalized_symbols = _dedupe_symbols(symbols)
        if not normalized_symbols:
            return []
        return await asyncio.gather(*(self.get_quote(symbol) for symbol in normalized_symbols))

    async def close(self) -> None:
        await self.provider.close()


def build_market_data_service(settings: Settings) -> MarketDataService:
    if settings.market_data_provider == "finnhub":
        provider: MarketDataProvider = FinnhubMarketDataProvider(
            api_key=settings.finnhub_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    elif settings.market_data_provider == "polygon":
        provider = PolygonMarketDataProvider(
            api_key=settings.polygon_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    elif settings.market_data_provider == "eastmoney":
        provider = EastmoneyAshareMarketDataProvider(
            timeout_seconds=settings.request_timeout_seconds,
        )
    elif settings.market_data_provider == "hybrid":
        provider = HybridMarketDataProvider(
            ashare_provider=EastmoneyAshareMarketDataProvider(
                timeout_seconds=settings.request_timeout_seconds,
            ),
            us_provider=_build_us_market_data_provider(settings),
        )
    else:
        provider = MockMarketDataProvider()
    return MarketDataService(provider, cache_ttl_seconds=settings.quote_cache_ttl_seconds)


def _build_us_market_data_provider(settings: Settings) -> MarketDataProvider:
    if settings.finnhub_api_key:
        return FinnhubMarketDataProvider(
            api_key=settings.finnhub_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if settings.polygon_api_key and settings.polygon_api_key != "replace-me":
        return PolygonMarketDataProvider(
            api_key=settings.polygon_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    return MockMarketDataProvider()


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise MarketDataError("symbol cannot be empty")
    return normalized


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in symbols:
        item = _normalize_symbol(symbol)
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized


def _required_positive_float(value: Any, symbol: str) -> float:
    parsed = _optional_float(value)
    if parsed is None or parsed <= 0:
        raise MarketDataError(f"provider returned no current price for {symbol}")
    return parsed


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AShareSymbol:
    def __init__(self, code: str, exchange: str) -> None:
        self.code = code
        self.exchange = exchange

    @property
    def display_symbol(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def secid(self) -> str:
        market_id = {"SZ": "0", "SH": "1", "BJ": "2"}[self.exchange]
        return f"{market_id}.{self.code}"


def _parse_ashare_symbol(symbol: str) -> AShareSymbol | None:
    normalized = symbol.strip().upper()
    if not normalized:
        return None

    suffix_match = re.fullmatch(r"(\d{6})\.(SH|SS|SZ|BJ|BSE)", normalized)
    if suffix_match:
        code = suffix_match.group(1)
        exchange = _normalize_ashare_exchange(suffix_match.group(2))
        return AShareSymbol(code=code, exchange=exchange)

    prefix_match = re.fullmatch(r"(SH|SS|SZ|BJ|BSE)(\d{6})", normalized)
    if prefix_match:
        exchange = _normalize_ashare_exchange(prefix_match.group(1))
        return AShareSymbol(code=prefix_match.group(2), exchange=exchange)

    if re.fullmatch(r"\d{6}", normalized):
        exchange = _infer_ashare_exchange(normalized)
        if exchange is None:
            return None
        return AShareSymbol(code=normalized, exchange=exchange)

    return None


def _normalize_ashare_exchange(exchange: str) -> str:
    if exchange in {"SH", "SS"}:
        return "SH"
    if exchange == "SZ":
        return "SZ"
    return "BJ"


def _infer_ashare_exchange(code: str) -> str | None:
    if code.startswith(("5", "6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return None


def _eastmoney_scaled_value(value: Any) -> float | None:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    return round(parsed / 100, 4)


def _polygon_timestamp(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    timestamp = int(value)
    if timestamp > 10_000_000_000_000_000:
        return datetime.fromtimestamp(timestamp / 1_000_000_000, tz=UTC)
    if timestamp > 10_000_000_000:
        return datetime.fromtimestamp(timestamp / 1_000, tz=UTC)
    return datetime.fromtimestamp(timestamp, tz=UTC)
