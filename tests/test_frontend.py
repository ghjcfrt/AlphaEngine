from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_frontend_index_is_served() -> None:
    app = create_app(Settings(market_data_provider="hybrid"))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "AlphaEngine 工作台" in response.text
    assert "配置源" in response.text
    assert "可混合模型系" in response.text
    assert "多 AI Agent 协作" in response.text
    assert "非投资建议" in response.text
    assert "导出报告" in response.text
    assert "金额单位" in response.text
    assert '<option value="CNY" selected>RMB</option>' in response.text
    assert "/static/app.js?v=20260603-report-polish" in response.text


def test_frontend_static_assets_are_served() -> None:
    app = create_app(Settings(market_data_provider="hybrid"))

    with TestClient(app) as client:
        response = client.get("/static/app.js")
        css_response = client.get("/static/styles.css")

    assert response.status_code == 200
    assert "submitPlan" in response.text
    assert "exportPlanResult" in response.text
    assert "buildPlanReportHtml" in response.text
    assert "AlphaEngine 投资计划报告" in response.text
    assert "alphaengine-investment-report" in response.text
    assert "模型接口类型" in response.text
    assert "AI 提供方" not in response.text
    assert 'data-ai-field="model_interface"' in response.text
    assert 'data-ai-field="ai_advisor_provider" type="hidden"' in response.text
    assert 'data-ai-field="ai_model_family" type="hidden"' in response.text
    assert "Openai Compatible" in response.text
    assert "规则/模拟（不调用模型）" not in response.text
    assert '["mock",' not in response.text
    assert "规则基线 Agent" in response.text
    assert "个模型 Agent" in response.text
    assert "collectAiAgentSettings" in response.text
    assert "应用到全部" in response.text
    assert "applyAiConfigToAll" in response.text
    assert "https://your-openai-compatible-api.example.com" in response.text
    assert "displayAiBaseUrl" in response.text
    assert "data-secret-masked" in response.text
    assert "savedSecretMask" in response.text
    assert "localStorage" in response.text
    assert "alphaengine.planDraft.v1" in response.text
    assert "alphaengine.settingsDraft.v1" in response.text
    assert "amountCurrency" in response.text
    assert "normalizeAmountCurrency" in response.text
    assert "ai-report-summary" in response.text
    assert "report-list-insight" in response.text
    assert "report-list-action" in response.text
    assert "report-list-limitation" in response.text
    assert "table-frame" in response.text
    assert 'type: "insight"' in response.text
    assert 'type: "action"' in response.text
    assert 'type: "limitation"' in response.text
    assert css_response.status_code == 200
    assert "detail-insight" in css_response.text
    assert "detail-action" in css_response.text
    assert "detail-limitation" in css_response.text
