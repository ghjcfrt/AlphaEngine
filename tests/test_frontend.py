from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_frontend_index_is_served() -> None:
    app = create_app(Settings(market_data_provider="mock"))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "AlphaEngine 工作台" in response.text
    assert "配置源" in response.text
    assert "模型接口类型" in response.text
    assert "多 AI Agent 协作" in response.text
    assert "非投资建议" in response.text
    assert "/static/app.js" in response.text


def test_frontend_static_assets_are_served() -> None:
    app = create_app(Settings(market_data_provider="mock"))

    with TestClient(app) as client:
        response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "submitPlan" in response.text
    assert "规则基线 Agent" in response.text
