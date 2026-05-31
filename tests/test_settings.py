import json

from fastapi.testclient import TestClient

import app.api.routes as routes
import app.core.local_config as local_config
from app.main import create_app


def test_runtime_settings_are_saved_locally(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / ".alphaengine.local.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ALPHA_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(local_config, "LOCAL_CONFIG_PATH", config_path)
    monkeypatch.setattr(routes, "LOCAL_CONFIG_PATH", config_path)

    app = create_app()

    with TestClient(app) as client:
        response = client.put(
            "/api/v1/settings",
            json={
                "market_data_provider": "mock",
                "ai_advisor_provider": "mock",
                "ai_model_family": "gemini",
                "openai_base_url": "https://models.example.com",
                "openai_model": "custom-model",
                "openai_api_key": "test-openai-key",
                "finnhub_api_key": "test-finnhub-key",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["market_data_provider"] == "mock"
        assert payload["ai_advisor_provider"] == "mock"
        assert payload["ai_model_family"] == "gemini"
        assert payload["openai_base_url"] == "https://models.example.com"
        assert payload["openai_model"] == "custom-model"
        assert payload["ai_runtime_provider"] == "mock-ai"
        assert payload["ai_runtime_model"] is None
        assert payload["ai_is_model_generated"] is False
        assert payload["has_openai_api_key"] is True
        assert payload["has_finnhub_api_key"] is True

        health = client.get("/health").json()
        assert health["market_data_provider"] == "mock"
        assert health["ai_advisor_provider"] == "mock"
        assert health["ai_runtime_provider"] == "mock-ai"
        assert health["ai_is_model_generated"] is False

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["openai_api_key"] == "test-openai-key"
    assert saved["finnhub_api_key"] == "test-finnhub-key"


def test_runtime_settings_can_clear_saved_key(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / ".alphaengine.local.json"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ALPHA_OPENAI_API_KEY", raising=False)
    config_path.write_text(
        json.dumps(
            {
                "market_data_provider": "mock",
                "ai_advisor_provider": "mock",
                "openai_api_key": "test-openai-key",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(local_config, "LOCAL_CONFIG_PATH", config_path)
    monkeypatch.setattr(routes, "LOCAL_CONFIG_PATH", config_path)

    app = create_app()

    with TestClient(app) as client:
        response = client.put("/api/v1/settings", json={"clear_openai_api_key": True})

    assert response.status_code == 200
    assert response.json()["has_openai_api_key"] is False
    assert "openai_api_key" not in json.loads(config_path.read_text(encoding="utf-8"))
