# AlphaEngine

面向个人用户的多 AI 协作智能投顾系统雏形。当前版本提供一个可运行的 Python/FastAPI 服务，用 ACP 风格消息在多个 Agent 之间传递上下文，完成风险评估、资产配置、实时行情获取、收益分析、合规复核和中文解读。

> 重要：本项目输出仅用于教育和产品原型演示，不构成投资建议。真实投顾业务需要持牌资质、适当性流程、审计留痕、数据授权和本地监管合规。

## 架构

- `RiskAssessmentAgent`：用规则基线和模型复核协作评估风险画像。
- `AssetAllocationAgent`：用规则模板和模型复核协作生成资产配置。
- `MarketDataAgent`：通过 provider 获取当前行情，不依赖已收盘价格。
- `ReturnAnalysisAgent`：用量化假设和模型复核协作分析收益情景。
- `ComplianceAgent`：用规则红线和模型复核协作输出适当性与合规护栏。
- `ACPMessage` + `InMemoryACPBus`：用统一 envelope 串联 agent 输入、输出和 trace。

## 实时行情

默认 provider 是 `hybrid`：

- A 股：`600519`、`600519.SH`、`000001.SZ` 这类代码会走东方财富公开行情接口，返回源标记为 `eastmoney-unofficial`。
- 美股：`AAPL`、`MSFT`、`SPY` 这类代码会优先走 Finnhub；如果没有 Finnhub key 但有 Polygon key，会走 Polygon；都没有时只对美股回退到 `mock`。

Finnhub REST quote 接口用于美股当前快照，`/api/v1/market/ws/{symbol}` 可通过 Finnhub WebSocket 转发实时 tick 数据。也可以配置 `ALPHA_MARKET_DATA_PROVIDER=polygon` 使用 Polygon/Massive snapshot。注意 Polygon 的实时性取决于你的订阅和数据权限。

东方财富这类免费源适合个人和演示场景，稳定性和授权边界不能等同于交易所授权数据。正式投顾或交易系统应接入持牌券商、交易所授权行情或合规数据供应商。

本地无 key 试跑时可以设置 `ALPHA_MARKET_DATA_PROVIDER=mock`，但它只返回模拟行情，不能用于真实投资规划。

## AI 解读

项目核心是“多 AI Agent 协作”，不是“规则多 Agent + 最后 AI 总结”。风险、配置、收益、合规 Agent 都会先生成可审计规则基线；启用真实模型后，每个专业 Agent 会用自己的角色提示调用模型复核和调整，规则只作为约束、审计基线和失败兜底。`AIAdvisorAgent` 会在这些 Agent 完成后，汇总生成结构化中文解读、关键洞察、执行事项和限制说明。

默认配置是 `ALPHA_AI_ADVISOR_PROVIDER=auto`：

- 配置 `OPENAI_API_KEY` 时，后端会通过 OpenAI Responses API 调用大模型，默认模型为 `gpt-5.4-mini`。
- 前端“配置源”窗口可以分别配置风险、配置、收益、合规、总结 5 个 AI Agent 的模型接口类型、模型 API URL、模型名称和 API Key。
- 5 个 AI Agent 可以同时使用不同模型系，例如风险 Agent 用 Gemini、配置 Agent 用 Claude、收益 Agent 用 DeepSeek、总结 Agent 用 GPT。
- 模型接口类型支持 GPT/OpenAI Responses、OpenAI Chat 兼容、Gemini、Claude/Anthropic、DeepSeek。不同类型会使用不同的 endpoint、鉴权头和请求体。
- 没有配置 key 时，会回退到本地规则/模拟结果，并在返回结果里标记 `is_model_generated=false`。
- 如需关闭 AI 解读，可设置 `ALPHA_AI_ADVISOR_PROVIDER=disabled`。
- 本地配置保存在 `.alphaengine.local.json`，该文件已加入 `.gitignore`，不要提交密钥。

## 运行

一键启动：

```powershell
python start.py
```

启动器会自动寻找可用端口并打开前端工作台。如果系统环境变量和 `.env` 都没有指定行情源，会默认使用 `mock` 行情，方便本地直接体验。

```powershell
uv sync --extra dev
copy .env.example .env
uv run uvicorn app.main:app --reload
```

服务启动后访问：

- `GET /`：前端工作台
- `GET /health`
- `GET /api/v1/agents`
- `GET /api/v1/market/quotes?symbols=600519.SH,000001.SZ,AAPL,MSFT`
- `POST /api/v1/advice/plans`
- `GET /api/v1/acp/traces/{trace_id}`
- `WS /api/v1/market/ws/AAPL`

前端工作台可以录入投资人画像、风险问卷、关注标的和已有持仓，并展示风险评分、配置建议、收益情景、行情快照、合规提示和 ACP trace。本地没有行情 key 时，建议在 `.env` 中设置 `ALPHA_MARKET_DATA_PROVIDER=mock` 先体验完整流程。

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
