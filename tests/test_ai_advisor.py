import httpx
import pytest

from app.core.config import Settings
from app.services.ai_advisor import (
    AnthropicMessagesAdvisorProvider,
    ChatCompletionsAdvisorProvider,
    GeminiGenerateContentAdvisorProvider,
    MockAIAdvisorProvider,
    OpenAIResponsesAdvisorProvider,
    build_ai_advisor_service,
)

MODEL_JSON = (
    '{"summary":"模型解读","key_insights":["风险偏高"],'
    '"action_items":["人工复核"],"limitations":["不构成投资建议"]}'
)


@pytest.mark.asyncio
async def test_openai_responses_provider_parses_structured_review() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = request.read().decode()
        assert "gpt-test" in payload
        return httpx.Response(200, json={"output_text": MODEL_JSON})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIResponsesAdvisorProvider(
        api_key="test-key",
        model="gpt-test",
        base_url="https://api.openai.com",
        timeout_seconds=3,
        client=client,
    )

    review = await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert review.provider == "gpt"
    assert review.model == "gpt-test"
    assert review.is_model_generated is True
    assert review.summary == "模型解读"
    await provider.close()


@pytest.mark.asyncio
async def test_chat_completions_provider_uses_openai_compatible_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = request.read().decode()
        assert "chat-test" in payload
        assert "response_format" in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": MODEL_JSON}}]})

    provider = ChatCompletionsAdvisorProvider(
        name="openai-compatible",
        api_key="test-key",
        model="chat-test",
        base_url="https://models.example.com",
        endpoint="/v1/chat/completions",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    review = await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert review.provider == "openai-compatible"
    assert review.summary == "模型解读"
    await provider.close()


@pytest.mark.asyncio
async def test_anthropic_provider_uses_messages_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"content": [{"type": "text", "text": MODEL_JSON}]})

    provider = AnthropicMessagesAdvisorProvider(
        api_key="test-key",
        model="claude-test",
        base_url="https://api.anthropic.com",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    review = await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert review.provider == "claude"
    assert review.model == "claude-test"
    await provider.close()


@pytest.mark.asyncio
async def test_gemini_provider_uses_generate_content_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1beta/models/gemini-test:generateContent"
        assert request.headers["x-goog-api-key"] == "test-key"
        payload = request.read().decode()
        assert "systemInstruction" in payload
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": MODEL_JSON}]}}]},
        )

    provider = GeminiGenerateContentAdvisorProvider(
        api_key="test-key",
        model="gemini-test",
        base_url="https://generativelanguage.googleapis.com",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    review = await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert review.provider == "gemini"
    assert review.model == "gemini-test"
    await provider.close()


def test_build_provider_selects_deepseek_chat_endpoint() -> None:
    service = build_ai_advisor_service(
        Settings(
            ai_advisor_provider="openai",
            ai_model_family="deepseek",
            openai_api_key="test-key",
        )
    )

    assert isinstance(service.provider, ChatCompletionsAdvisorProvider)
    assert service.provider.name == "deepseek"
    assert service.provider.endpoint == "/chat/completions"


def test_build_provider_keeps_user_supplied_url_for_model_family() -> None:
    service = build_ai_advisor_service(
        Settings(
            ai_advisor_provider="openai",
            ai_model_family="gemini",
            openai_base_url="https://my-gemini-proxy.example.com",
            openai_api_key="test-key",
        )
    )

    assert isinstance(service.provider, GeminiGenerateContentAdvisorProvider)
    assert service.provider.base_url == "https://my-gemini-proxy.example.com"


def test_auto_provider_ignores_placeholder_openai_key() -> None:
    service = build_ai_advisor_service(
        Settings(ai_advisor_provider="auto", openai_api_key="replace-me")
    )

    assert isinstance(service.provider, MockAIAdvisorProvider)
