import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.domain.schemas import QuoteSnapshot


class MarketDataError(RuntimeError):
    """行情层统一异常，路由层会转换成 503/400 等 HTTP 错误。"""

    pass


class MarketDataProvider(Protocol):
    """所有行情 provider 的最小接口。

    使用 Protocol 让真实 provider、混合 provider、未配置 provider 都能被同一个
    MarketDataService 接收，测试里也可以注入轻量 fake。
    """

    name: str

    async def get_quote(self, symbol: str) -> QuoteSnapshot: ...

    async def close(self) -> None: ...


class FinnhubMarketDataProvider:
    """Finnhub 行情 provider，主要用于美股实时快照。"""

    name = "finnhub"

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        # 测试注入 client 时不应由 provider 关闭；自己创建的 client 才负责关闭。
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://finnhub.io/api/v1",
            timeout=timeout_seconds,
        )

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        if not self.api_key:
            raise MarketDataError("FINNHUB_API_KEY is required for real-time Finnhub quotes.")
        normalized = _normalize_symbol(symbol)
        # Finnhub /quote 字段较短：c=current、o=open、pc=previous close、t=timestamp。
        response = await self.client.get(
            "/quote",
            params={"symbol": normalized, "token": self.api_key},
        )
        response.raise_for_status()
        payload = response.json()
        current_price = _required_positive_float(payload.get("c"), normalized)
        # 上游有时返回 0/空时间戳，缺失时用当前时间兜底。
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
    """Polygon 股票快照 provider。"""

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
        # Polygon snapshot 是嵌套结构，当前价优先使用 lastTrade，
        # 没有逐笔成交时再退到分钟或日线收盘字段。
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


class AlphaVantageMarketDataProvider:
    """Alpha Vantage GLOBAL_QUOTE provider。

    该接口一般不是实时流式行情，因此 is_realtime=False。
    """

    name = "alphavantage"

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # replace-me 这种文档占位符视为未配置，避免误发无效请求。
        self.api_key = _usable_api_key(api_key)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://www.alphavantage.co",
            timeout=timeout_seconds,
        )

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        if not self.api_key:
            raise MarketDataError(
                "ALPHA_VANTAGE_API_KEY is required for Alpha Vantage quotes."
            )
        # Alpha Vantage 对 A 股代码的后缀和常见展示后缀不同，需要先转换查询代码。
        query_symbol, display_symbol, currency = _alpha_vantage_symbol(symbol)
        response = await self.client.get(
            "/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": query_symbol,
                "apikey": self.api_key,
            },
        )
        response.raise_for_status()
        payload = response.json()
        # Alpha Vantage 在限频、错误、提示时也返回 200，因此需要检查业务错误字段。
        _raise_alpha_vantage_error(payload, display_symbol)
        quote = payload.get("Global Quote")
        if not isinstance(quote, dict) or not quote:
            raise MarketDataError(f"Alpha Vantage returned no quote data for {display_symbol}.")

        raw_symbol = str(quote.get("01. symbol") or query_symbol)
        # 返回代码再转成前端习惯展示的 600519.SH / 000001.SZ。
        parsed_symbol = _alpha_vantage_display_symbol(raw_symbol) or display_symbol
        current_price = _required_positive_float(quote.get("05. price"), parsed_symbol)

        return QuoteSnapshot(
            symbol=parsed_symbol,
            current_price=current_price,
            open_price=_optional_float(quote.get("02. open")),
            previous_close=_optional_float(quote.get("08. previous close")),
            high_price=_optional_float(quote.get("03. high")),
            low_price=_optional_float(quote.get("04. low")),
            change=_optional_float(quote.get("09. change")),
            change_percent=_optional_percent(quote.get("10. change percent")),
            currency=currency,
            updated_at=_alpha_vantage_quote_date(quote.get("07. latest trading day")),
            source=self.name,
            is_realtime=False,
            data_delay_seconds=None,
            raw=payload,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class EastmoneyAshareMarketDataProvider:
    """东方财富 A 股公开接口 provider。

    这是未授权公开接口，只适合本地原型演示；README 中也明确说明了限制。
    """

    name = "eastmoney-unofficial"
    # f43 当前价、f44 最高、f45 最低、f46 开盘、f57 代码、f58 名称、
    # f60 昨收、f86 时间、f169 涨跌额、f170 涨跌幅。
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
            # 东方财富价格字段通常放大 100 倍，需缩放回真实价格。
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
    """混合行情 provider。

    A 股走东方财富，美股/其它标的走可用的美国市场 provider。
    """

    name = "hybrid"

    def __init__(
        self,
        ashare_provider: MarketDataProvider,
        us_provider: MarketDataProvider,
    ) -> None:
        self.ashare_provider = ashare_provider
        self.us_provider = us_provider

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        # 能解析为 A 股代码的标的优先使用 A 股 provider。
        if _parse_ashare_symbol(symbol) is not None:
            return await self.ashare_provider.get_quote(symbol)
        return await self.us_provider.get_quote(symbol)

    async def close(self) -> None:
        await self.ashare_provider.close()
        await self.us_provider.close()


class UnconfiguredMarketDataProvider:
    """占位 provider。

    hybrid 模式下如果没有任何美股 provider 的 Key，仍允许服务启动；
    真正请求美股标的时再返回清晰配置错误。
    """

    name = "unconfigured"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        raise MarketDataError(self.reason)

    async def close(self) -> None:
        return None


class MarketDataService:
    """行情服务门面，负责缓存、去重和并发请求。"""

    def __init__(
        self,
        provider: MarketDataProvider,
        cache_ttl_seconds: int = 2,
    ) -> None:
        self.provider = provider
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, QuoteSnapshot]] = {}

    async def get_quote(self, symbol: str) -> QuoteSnapshot:
        normalized = _normalize_symbol(symbol)
        now = time.monotonic()
        cached = self._cache.get(normalized)
        if cached and now - cached[0] <= self.cache_ttl_seconds:
            # 短 TTL 避免用户连续点击刷新时打爆免费接口。
            return cached[1]
        quote = await self.provider.get_quote(normalized)
        self._cache[normalized] = (now, quote)
        return quote

    async def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        normalized_symbols = _dedupe_symbols(symbols)
        if not normalized_symbols:
            return []
        # 多标的行情互相独立，可以并发请求，保持结果顺序与去重后的输入一致。
        return await asyncio.gather(*(self.get_quote(symbol) for symbol in normalized_symbols))

    async def close(self) -> None:
        await self.provider.close()


def build_market_data_service(settings: Settings) -> MarketDataService:
    """根据 Settings 创建行情服务。"""

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
    elif settings.market_data_provider == "alphavantage":
        provider = AlphaVantageMarketDataProvider(
            api_key=settings.alpha_vantage_api_key,
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
        raise MarketDataError(f"Unsupported market data provider: {settings.market_data_provider}")
    return MarketDataService(provider, cache_ttl_seconds=settings.quote_cache_ttl_seconds)


def _build_us_market_data_provider(settings: Settings) -> MarketDataProvider:
    """为 hybrid 模式选择美股 provider。

    优先级：Finnhub -> Polygon -> Alpha Vantage。这样实时性优先，同时保留
    Alpha Vantage 作为最后兜底。
    """

    finnhub_api_key = _usable_api_key(settings.finnhub_api_key)
    polygon_api_key = _usable_api_key(settings.polygon_api_key)
    alpha_vantage_api_key = _usable_api_key(settings.alpha_vantage_api_key)

    if finnhub_api_key:
        return FinnhubMarketDataProvider(
            api_key=finnhub_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if polygon_api_key:
        return PolygonMarketDataProvider(
            api_key=polygon_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if alpha_vantage_api_key:
        return AlphaVantageMarketDataProvider(
            api_key=alpha_vantage_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
    return UnconfiguredMarketDataProvider(
        "Hybrid market data requires FINNHUB_API_KEY, POLYGON_API_KEY, "
        "or ALPHA_VANTAGE_API_KEY for US symbols."
    )


def _normalize_symbol(symbol: str) -> str:
    """统一证券代码格式，并拒绝空字符串。"""

    normalized = symbol.strip().upper()
    if not normalized:
        raise MarketDataError("symbol cannot be empty")
    return normalized


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    """按首次出现顺序去重。"""

    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in symbols:
        item = _normalize_symbol(symbol)
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized


def _required_positive_float(value: Any, symbol: str) -> float:
    """解析必须为正数的价格字段。"""

    parsed = _optional_float(value)
    if parsed is None or parsed <= 0:
        raise MarketDataError(f"provider returned no current price for {symbol}")
    return parsed


def _optional_float(value: Any) -> float | None:
    """尽量把 provider 字段转换为 float，转换失败时返回 None。"""

    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_percent(value: Any) -> float | None:
    """解析带百分号或纯数字的涨跌幅字段。"""

    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    return _optional_float(value)


def _usable_api_key(value: str | None) -> str | None:
    """过滤空值和示例占位符，返回真正可用的 API Key。"""

    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped == "replace-me":
        return None
    return stripped


class AShareSymbol:
    """标准化后的 A 股代码。"""

    def __init__(self, code: str, exchange: str) -> None:
        self.code = code
        self.exchange = exchange

    @property
    def display_symbol(self) -> str:
        # 前端和响应统一展示为 600519.SH / 000001.SZ。
        return f"{self.code}.{self.exchange}"

    @property
    def secid(self) -> str:
        # 东方财富 secid 需要市场编号：0 深市、1 沪市、2 北交所。
        market_id = {"SZ": "0", "SH": "1", "BJ": "2"}[self.exchange]
        return f"{market_id}.{self.code}"


def _parse_ashare_symbol(symbol: str) -> AShareSymbol | None:
    """解析常见 A 股输入格式。

    支持 600519.SH、SH600519、000001.SZ、000001 等多种写法。
    """

    normalized = symbol.strip().upper()
    if not normalized:
        return None

    suffix_match = re.fullmatch(r"(\d{6})\.(SH|SS|SHH|SZ|SHZ|BJ|BSE)", normalized)
    if suffix_match:
        # SS/SHH/SHZ 等写法来自不同数据源约定，内部统一成 SH/SZ/BJ。
        code = suffix_match.group(1)
        exchange = _normalize_ashare_exchange(suffix_match.group(2))
        return AShareSymbol(code=code, exchange=exchange)

    prefix_match = re.fullmatch(r"(SH|SS|SZ|BJ|BSE)(\d{6})", normalized)
    if prefix_match:
        exchange = _normalize_ashare_exchange(prefix_match.group(1))
        return AShareSymbol(code=prefix_match.group(2), exchange=exchange)

    if re.fullmatch(r"\d{6}", normalized):
        # 纯 6 位代码根据首位数字推断交易所。
        exchange = _infer_ashare_exchange(normalized)
        if exchange is None:
            return None
        return AShareSymbol(code=normalized, exchange=exchange)

    return None


def _normalize_ashare_exchange(exchange: str) -> str:
    """把不同数据源里的交易所别名统一成 SH/SZ/BJ。"""

    if exchange in {"SH", "SS", "SHH"}:
        return "SH"
    if exchange in {"SZ", "SHZ"}:
        return "SZ"
    return "BJ"


def _infer_ashare_exchange(code: str) -> str | None:
    """根据 A 股代码段推断交易所。"""

    if code.startswith(("5", "6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return None


def _eastmoney_scaled_value(value: Any) -> float | None:
    """东方财富价格字段通常扩大 100 倍，这里缩回真实价格。"""

    parsed = _optional_float(value)
    if parsed is None:
        return None
    return round(parsed / 100, 4)


def _polygon_timestamp(value: Any) -> datetime:
    """兼容 Polygon 可能返回的秒、毫秒或纳秒时间戳。"""

    if not value:
        return datetime.now(UTC)
    timestamp = int(value)
    if timestamp > 10_000_000_000_000_000:
        return datetime.fromtimestamp(timestamp / 1_000_000_000, tz=UTC)
    if timestamp > 10_000_000_000:
        return datetime.fromtimestamp(timestamp / 1_000, tz=UTC)
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _alpha_vantage_symbol(symbol: str) -> tuple[str, str, str]:
    """把展示代码转换为 Alpha Vantage 查询代码，并返回币种。"""

    parsed = _parse_ashare_symbol(symbol)
    if parsed is None:
        normalized = _normalize_symbol(symbol)
        return normalized, normalized, "USD"
    exchange_suffix = {"SH": "SHH", "SZ": "SHZ"}.get(parsed.exchange, parsed.exchange)
    return f"{parsed.code}.{exchange_suffix}", parsed.display_symbol, "CNY"


def _alpha_vantage_display_symbol(symbol: str) -> str | None:
    """把 Alpha Vantage 返回代码转换回统一展示代码。"""

    normalized = symbol.strip().upper()
    if not normalized:
        return None
    parsed = _parse_ashare_symbol(normalized)
    if parsed is not None:
        return parsed.display_symbol
    return normalized


def _alpha_vantage_quote_date(value: Any) -> datetime:
    """Alpha Vantage 只给交易日日期，没有精确时间。"""

    if isinstance(value, str) and value.strip():
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)
    return datetime.now(UTC)


def _raise_alpha_vantage_error(payload: dict[str, Any], symbol: str) -> None:
    """识别 Alpha Vantage 的业务错误/限频提示。"""

    for key in ("Error Message", "Information", "Note"):
        message = payload.get(key)
        if isinstance(message, str) and message.strip():
            raise MarketDataError(f"Alpha Vantage quote error for {symbol}: {message.strip()}")
