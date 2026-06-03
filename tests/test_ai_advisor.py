import httpx
import pytest

import app.services.ai_advisor as ai_advisor_module
from app.core.config import Settings
from app.services.ai_advisor import (
    AIAdvisorError,
    AIAdvisorService,
    AnthropicMessagesAdvisorProvider,
    ChatCompletionsAdvisorProvider,
    GeminiGenerateContentAdvisorProvider,
    OpenAIResponsesAdvisorProvider,
    UnconfiguredAIAdvisorProvider,
    build_ai_advisor_service,
    clear_ai_failure_cache,
)

MODEL_JSON = (
    '{"summary":"模型解读","key_insights":["风险偏高"],'
    '"action_items":["人工复核"],"limitations":["不构成投资建议"]}'
)


def test_openai_compatible_requires_custom_base_url() -> None:
    settings = Settings(
        ai_advisor_provider="openai_compatible",
        ai_model_family="openai_compatible",
        openai_api_key="test-key",
    )

    with pytest.raises(AIAdvisorError, match="模型 API URL 不能为空"):
        build_ai_advisor_service(settings)


@pytest.mark.asyncio
async def test_openai_compatible_uses_custom_base_url() -> None:
    settings = Settings(
        ai_advisor_provider="openai_compatible",
        ai_model_family="openai_compatible",
        openai_base_url="https://models.example.com",
        openai_model="chat-test",
        openai_api_key="test-key",
    )

    service = build_ai_advisor_service(settings)

    assert isinstance(service.provider, ChatCompletionsAdvisorProvider)
    assert service.provider.base_url == "https://models.example.com"
    await service.close()


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

    assert review.provider == "OpenAI"
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
        name="OpenAI Compatible",
        api_key="test-key",
        model="chat-test",
        base_url="https://models.example.com",
        endpoint="/v1/chat/completions",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    review = await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert review.provider == "OpenAI Compatible"
    assert review.summary == "模型解读"
    await provider.close()


@pytest.mark.asyncio
async def test_chat_completions_provider_retries_transient_status(monkeypatch) -> None:
    calls = 0

    async def no_sleep(delay: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, request=request, json={"error": "temporary"})
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": MODEL_JSON}}]},
        )

    monkeypatch.setattr(ai_advisor_module.asyncio, "sleep", no_sleep)
    provider = ChatCompletionsAdvisorProvider(
        name="OpenAI Compatible",
        api_key="test-key",
        model="chat-test",
        base_url="https://models.example.com",
        endpoint="/v1/chat/completions",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    review = await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert calls == 2
    assert review.summary == "模型解读"
    await provider.close()


@pytest.mark.asyncio
async def test_chat_completions_provider_does_not_retry_auth_errors(monkeypatch) -> None:
    calls = 0

    async def no_sleep(delay: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, request=request, json={"error": "unauthorized"})

    monkeypatch.setattr(ai_advisor_module.asyncio, "sleep", no_sleep)
    provider = ChatCompletionsAdvisorProvider(
        name="OpenAI Compatible",
        api_key="bad-key",
        model="chat-test",
        base_url="https://models.example.com",
        endpoint="/v1/chat/completions",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider.create_review({"risk_assessment": {"risk_score": 80}})

    assert calls == 1
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

    assert review.provider == "Anthropic"
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

    assert review.provider == "Gemini"
    assert review.model == "gemini-test"
    await provider.close()


def test_build_provider_selects_deepseek_chat_endpoint() -> None:
    service = build_ai_advisor_service(
        Settings.model_validate(
            {
                "ai_advisor_provider": "DeepSeek",
                "ai_model_family": "deepseek",
                "openai_api_key": "test-key",
            }
        )
    )

    assert isinstance(service.provider, ChatCompletionsAdvisorProvider)
    assert service.provider.name == "DeepSeek"
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


def test_legacy_auto_provider_uses_unconfigured_openai_without_key() -> None:
    service = build_ai_advisor_service(
        Settings(ai_advisor_provider="auto", openai_api_key="replace-me")
    )

    assert isinstance(service.provider, UnconfiguredAIAdvisorProvider)
    assert service.provider.name == "OpenAI"
    assert service.provider.is_model_generated is True


@pytest.mark.asyncio
async def test_unconfigured_provider_requires_model_key_on_use() -> None:
    service = build_ai_advisor_service(Settings(openai_api_key=None))

    with pytest.raises(AIAdvisorError, match="必须配置模型 API Key"):
        await service.create_review({})


def test_build_provider_supports_per_agent_model_families() -> None:
    settings = Settings(
        ai_advisor_provider="openai",
        ai_agents={
            "risk_assessment": {
                "ai_advisor_provider": "Gemini",
                "ai_model_family": "gemini",
                "openai_base_url": "https://gemini.example.com",
                "openai_model": "gemini-risk",
                "openai_api_key": "risk-key",
            },
            "asset_allocation": {
                "ai_advisor_provider": "Anthropic",
                "ai_model_family": "claude",
                "openai_base_url": "https://claude.example.com",
                "openai_model": "claude-allocation",
                "openai_api_key": "allocation-key",
            },
        },
    )

    risk_service = build_ai_advisor_service(settings, "risk_assessment")
    allocation_service = build_ai_advisor_service(settings, "asset_allocation")

    assert isinstance(risk_service.provider, GeminiGenerateContentAdvisorProvider)
    assert risk_service.provider.base_url == "https://gemini.example.com"
    assert isinstance(allocation_service.provider, AnthropicMessagesAdvisorProvider)
    assert allocation_service.provider.base_url == "https://claude.example.com"


@pytest.mark.asyncio
async def test_ai_service_skips_retries_after_provider_http_failure() -> None:
    clear_ai_failure_cache()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, request=request)

    transport = httpx.MockTransport(handler)
    first_provider = ChatCompletionsAdvisorProvider(
        name="OpenAI Compatible",
        api_key="bad-key",
        model="chat-test",
        base_url="https://models.example.com",
        endpoint="/v1/chat/completions",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=transport),
    )
    second_provider = ChatCompletionsAdvisorProvider(
        name="OpenAI Compatible",
        api_key="bad-key",
        model="chat-test",
        base_url="https://models.example.com",
        endpoint="/v1/chat/completions",
        timeout_seconds=3,
        client=httpx.AsyncClient(transport=transport),
    )
    first_service = AIAdvisorService(first_provider)
    second_service = AIAdvisorService(second_provider)

    with pytest.raises(AIAdvisorError, match="401"):
        await first_service.generate_json("task", "", "", {}, {})
    with pytest.raises(AIAdvisorError, match="已跳过重试"):
        await second_service.generate_json("task", "", "", {}, {})

    assert calls == 1
    await first_service.close()
    await second_service.close()
    clear_ai_failure_cache()
