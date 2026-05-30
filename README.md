# AlphaEngine

面向个人用户的 AI 智能投顾后端雏形。当前版本提供一个可运行的 Python/FastAPI 服务，用 ACP 风格消息在多个 agent 之间传递上下文，完成风险评估、资产配置、实时行情获取、收益分析和合规提示。

> 重要：本项目输出仅用于教育和产品原型演示，不构成投资建议。真实投顾业务需要持牌资质、适当性流程、审计留痕、数据授权和本地监管合规。

## 架构

- `RiskAssessmentAgent`：根据年龄、期限、流动性、风险问卷和目标计算风险等级。
- `AssetAllocationAgent`：将风险等级映射为多资产配置建议。
- `MarketDataAgent`：通过 provider 获取当前行情，不依赖已收盘价格。
- `ReturnAnalysisAgent`：基于配置权重估算预期收益、波动率和多期限情景。
- `ComplianceAgent`：输出适当性提示、风险警示和人工复核条件。
- `ACPMessage` + `InMemoryACPBus`：用统一 envelope 串联 agent 输入、输出和 trace。

## 实时行情

默认 provider 是 `hybrid`：

- A 股：`600519`、`600519.SH`、`000001.SZ` 这类代码会走东方财富公开行情接口，返回源标记为 `eastmoney-unofficial`。
- 美股：`AAPL`、`MSFT`、`SPY` 这类代码会优先走 Finnhub；如果没有 Finnhub key 但有 Polygon key，会走 Polygon；都没有时只对美股回退到 `mock`。

Finnhub REST quote 接口用于美股当前快照，`/api/v1/market/ws/{symbol}` 可通过 Finnhub WebSocket 转发实时 tick 数据。也可以配置 `ALPHA_MARKET_DATA_PROVIDER=polygon` 使用 Polygon/Massive snapshot。注意 Polygon 的实时性取决于你的订阅和数据权限。

东方财富这类免费源适合个人和演示场景，稳定性和授权边界不能等同于交易所授权数据。正式投顾或交易系统应接入持牌券商、交易所授权行情或合规数据供应商。

本地无 key 试跑时可以设置 `ALPHA_MARKET_DATA_PROVIDER=mock`，但它只返回模拟行情，不能用于真实投资规划。

## 运行

```powershell
uv sync --extra dev
copy .env.example .env
uv run uvicorn app.main:app --reload
```

服务启动后访问：

- `GET /health`
- `GET /api/v1/agents`
- `GET /api/v1/market/quotes?symbols=600519.SH,000001.SZ,AAPL,MSFT`
- `POST /api/v1/advice/plans`
- `GET /api/v1/acp/traces/{trace_id}`
- `WS /api/v1/market/ws/AAPL`

## 示例请求

```json
{
  "user_id": "demo-user",
  "profile": {
    "age": 32,
    "annual_income": 300000,
    "net_worth": 800000,
    "initial_capital": 200000,
    "investment_horizon_years": 8,
    "liquidity_need": "medium",
    "investment_objective": "growth",
    "risk_answers": [4, 4, 3, 5, 4]
  },
  "symbols": ["600519.SH", "000001.SZ", "AAPL", "MSFT", "SPY"],
  "include_acp_trace": true
}
```

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/advice/plans `
  -H "Content-Type: application/json" `
  -d "@request.json"
```

## 测试

```powershell
uv run pytest
```
