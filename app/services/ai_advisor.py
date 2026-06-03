import asyncio
import json
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.core.config import (
    AI_PROVIDER_LABELS,
    DEFAULT_OPENAI_MODEL,
    AIModelSettings,
    Settings,
    resolve_ai_model_settings,
)
from app.domain.schemas import AIAdvisorReview

SYSTEM_INSTRUCTIONS = (
    "你是持牌投顾团队内部使用的投资方案解释助手。"
    "只能基于输入 JSON 总结，不得编造行情、收益或监管结论。"
    "必须使用中文，语气专业克制，并反复强调输出不构成投资建议。"
)
USER_PROMPT_PREFIX = (
    "请根据以下投资计划上下文生成结构化中文解读。"
    "只输出 JSON，不要输出 Markdown。JSON 字段必须包含 summary、key_insights、"
    "action_items、limitations。"
)

# 不同模型族在用户没有指定模型时使用的默认模型。
DEFAULT_FAMILY_MODELS = {
    "gpt": DEFAULT_OPENAI_MODEL,
    "openai_compatible": "",
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-5",
    "deepseek": "deepseek-v4.1",
}
# 对同一个 provider/model/base_url 的连续失败做短暂冷却，避免一次配置错误触发
# 风险、配置、收益、合规、总结五个 Agent 连续打同一个不可用接口。
AI_FAILURE_COOLDOWN_SECONDS = 60
AI_REQUEST_MAX_ATTEMPTS = 3
AI_TIMEOUT_MAX_ATTEMPTS = 2
AI_RETRY_BACKOFF_SECONDS = (0.8, 2.0)
_AI_PROVIDER_FAILURES: dict[str, tuple[float, str]] = {}


class AIAdvisorError(RuntimeError):
    """AI 服务层统一异常。"""

    pass


class AIAdvisorProvider(Protocol):
    """所有模型 provider 的统一接口。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    @property
    def is_model_generated(self) -> bool: ...

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview: ...

    async def close(self) -> None: ...


class AIAdvisorJSONService(Protocol):
    """专业 Agent 只需要 JSON 生成能力，因此用更窄的协议暴露依赖。"""

    @property
    def is_model_generated(self) -> bool: ...

    @property
    def provider_name(self) -> str: ...

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]: ...


class UnconfiguredAIAdvisorProvider:
    """未配置密钥时的占位 provider。

    应用仍可启动和打开配置页；真正调用模型时返回清晰的配置错误。
    """

    is_model_generated: bool = True

    def __init__(self, provider_name: str, model: str | None, reason: str) -> None:
        self.name = provider_name
        self.model = model
        self.reason = reason

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        raise AIAdvisorError(self.reason)

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        raise AIAdvisorError(self.reason)

    async def close(self) -> None:
        return None


class DisabledAIAdvisorProvider:
    """AI 显式关闭时的 provider。

    专业 Agent 会拿到规则基线；总结 Agent 会返回固定说明，表示未调用大模型。
    """

    name: str = "Disabled"
    model: str | None = None
    is_model_generated: bool = False

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # 专业 Agent 的上下文里都会带 baseline，关闭 AI 时直接返回它。
        baseline = context.get("baseline")
        if isinstance(baseline, dict):
            return baseline
        raise AIAdvisorError("AI 已关闭，且没有可用的规则基线。")

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        return AIAdvisorReview(
            provider=self.name,
            model=self.model,
            is_model_generated=self.is_model_generated,
            summary="AI 解读已关闭。",
            key_insights=["当前仅返回规则型风险评估、资产配置、收益情景和合规提示。"],
            action_items=[
                "设置 ALPHA_AI_ADVISOR_PROVIDER=OpenAI、Gemini、Anthropic 或 DeepSeek "
                "后可启用 AI 解读。"
            ],
            limitations=["未调用大模型。"],
        )

    async def close(self) -> None:
        return None


class OpenAIResponsesAdvisorProvider:
    """OpenAI Responses API provider。"""

    name = "OpenAI"
    is_model_generated = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # Responses API 支持 json_schema 格式约束，因此这里传入严格 schema。
        response = await _post_json_with_retries(
            self.client,
            _join_url(self.base_url, "/v1/responses"),
            headers=_bearer_headers(self.api_key),
            payload={
                "model": self.model,
                "instructions": system_instructions,
                "input": _task_prompt(user_prompt, schema, context),
                "text": {"format": _json_schema_format(task_name, schema)},
                "max_output_tokens": 1200,
            },
        )
        return _parse_json_payload(_extract_openai_response_text(response.json()))

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        payload = await self.generate_json(
            task_name="ai_advisor_review",
            system_instructions=SYSTEM_INSTRUCTIONS,
            user_prompt=USER_PROMPT_PREFIX,
            schema=_review_schema(),
            context=context,
        )
        return _review(self.name, self.model, payload)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class ChatCompletionsAdvisorProvider:
    """OpenAI 兼容和 DeepSeek 的 Chat Completions provider。"""

    is_model_generated = True

    def __init__(
        self,
        name: str,
        api_key: str,
        model: str,
        base_url: str,
        endpoint: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.endpoint = endpoint
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # 兼容接口一般支持 response_format=json_object，但未必支持严格 schema。
        # 因此 schema 放进 prompt，返回后再由 Pydantic 模型二次校验。
        response = await _post_json_with_retries(
            self.client,
            _join_url(self.base_url, self.endpoint),
            headers=_bearer_headers(self.api_key),
            payload={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": _task_prompt(user_prompt, schema, context)},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 1200,
            },
        )
        return _parse_json_payload(_extract_chat_completion_text(response.json()))

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        payload = await self.generate_json(
            task_name="ai_advisor_review",
            system_instructions=SYSTEM_INSTRUCTIONS,
            user_prompt=USER_PROMPT_PREFIX,
            schema=_review_schema(),
            context=context,
        )
        return _review(self.name, self.model, payload)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class AnthropicMessagesAdvisorProvider:
    """Anthropic Messages API provider。"""

    name = "Anthropic"
    is_model_generated = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # Claude 的 system 字段独立于 messages，用户 prompt 中包含 schema 和上下文。
        prompt = _task_prompt(user_prompt, schema, context)
        response = await _post_json_with_retries(
            self.client,
            _join_url(self.base_url, "/v1/messages"),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload={
                "model": self.model,
                "max_tokens": 1200,
                "system": system_instructions,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        return _parse_json_payload(_extract_anthropic_text(response.json()))

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        payload = await self.generate_json(
            task_name="ai_advisor_review",
            system_instructions=SYSTEM_INSTRUCTIONS,
            user_prompt=USER_PROMPT_PREFIX,
            schema=_review_schema(),
            context=context,
        )
        return _review(self.name, self.model, payload)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class GeminiGenerateContentAdvisorProvider:
    """Gemini GenerateContent provider。"""

    name = "Gemini"
    is_model_generated = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # Gemini 通过 responseMimeType 请求 JSON 输出，schema 仍放在 prompt 中约束字段。
        endpoint = f"/v1beta/models/{self.model}:generateContent"
        prompt = _task_prompt(user_prompt, schema, context)
        response = await _post_json_with_retries(
            self.client,
            _join_url(self.base_url, endpoint),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            payload={
                "systemInstruction": {"parts": [{"text": system_instructions}]},
                "contents": [
                    {"role": "user", "parts": [{"text": prompt}]},
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "maxOutputTokens": 1200,
                },
            },
        )
        return _parse_json_payload(_extract_gemini_text(response.json()))

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        payload = await self.generate_json(
            task_name="ai_advisor_review",
            system_instructions=SYSTEM_INSTRUCTIONS,
            user_prompt=USER_PROMPT_PREFIX,
            schema=_review_schema(),
            context=context,
        )
        return _review(self.name, self.model, payload)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class AIAdvisorService:
    """AI 服务门面，负责冷却、重试错误封装和 provider 状态暴露。"""

    def __init__(self, provider: AIAdvisorProvider) -> None:
        self.provider = provider

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        # 总结 Agent 调用前先检查冷却，避免已知失败接口被重复请求。
        self._raise_if_provider_is_cooling_down()
        try:
            return await self.provider.create_review(_jsonable(context))
        except httpx.HTTPError as exc:
            message = describe_ai_error(exc)
            self._remember_provider_failure(message)
            raise AIAdvisorError(message) from exc

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # 专业 Agent 的 JSON 复核也共享同一套冷却和错误封装逻辑。
        self._raise_if_provider_is_cooling_down()
        try:
            return await self.provider.generate_json(
                task_name=task_name,
                system_instructions=system_instructions,
                user_prompt=user_prompt,
                schema=schema,
                context=_jsonable(context),
            )
        except httpx.HTTPError as exc:
            message = describe_ai_error(exc)
            self._remember_provider_failure(message)
            raise AIAdvisorError(message) from exc

    @property
    def is_model_generated(self) -> bool:
        return self.provider.is_model_generated

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def provider_model(self) -> str | None:
        return self.provider.model

    async def close(self) -> None:
        await self.provider.close()

    def _provider_cache_key(self) -> str:
        # endpoint/base_url 也参与 key，避免不同兼容接口之间互相影响。
        return "|".join(
            [
                self.provider.name,
                self.provider.model or "",
                str(getattr(self.provider, "base_url", "")),
                str(getattr(self.provider, "endpoint", "")),
            ]
        )

    def _raise_if_provider_is_cooling_down(self) -> None:
        """如果同一 provider 最近失败过，短时间内直接跳过重试。"""

        cached = _AI_PROVIDER_FAILURES.get(self._provider_cache_key())
        if not cached:
            return
        expires_at, message = cached
        if time.monotonic() >= expires_at:
            # 冷却到期后清除失败记录，让下一次请求重新尝试真实接口。
            _AI_PROVIDER_FAILURES.pop(self._provider_cache_key(), None)
            return
        raise AIAdvisorError(f"模型接口临时不可用，已跳过重试：{message}")

    def _remember_provider_failure(self, message: str) -> None:
        """记录失败原因和冷却截止时间。"""

        _AI_PROVIDER_FAILURES[self._provider_cache_key()] = (
            time.monotonic() + AI_FAILURE_COOLDOWN_SECONDS,
            message,
        )


def clear_ai_failure_cache() -> None:
    """配置热更新后清空 AI 失败冷却。"""

    _AI_PROVIDER_FAILURES.clear()


def build_ai_advisor_service(settings: Settings, agent_key: str | None = None) -> AIAdvisorService:
    """按全局/单 Agent 配置创建 AI 服务。"""

    model_settings = resolve_ai_model_settings(settings, agent_key)
    provider_name = model_settings.ai_advisor_provider
    model_api_key = _usable_api_key(model_settings.openai_api_key)
    if provider_name == "disabled":
        provider: AIAdvisorProvider = DisabledAIAdvisorProvider()
    else:
        if not model_api_key:
            # 缺 Key 不在启动时抛错，避免配置页打不开。
            provider_label = AI_PROVIDER_LABELS.get(provider_name, provider_name)
            provider = UnconfiguredAIAdvisorProvider(
                provider_name=provider_label,
                model=model_settings.openai_model,
                reason=f"AI 提供方为 {provider_label} 时必须配置模型 API Key。",
            )
        else:
            provider = _build_model_provider(
                model_settings,
                api_key=model_api_key,
                timeout_seconds=settings.request_timeout_seconds,
            )
    return AIAdvisorService(provider)


def _build_model_provider(
    settings: AIModelSettings,
    api_key: str,
    timeout_seconds: float,
) -> AIAdvisorProvider:
    """根据模型族选择具体请求适配器。"""

    family = settings.ai_model_family
    base_url = _required_base_url(settings.openai_base_url)
    model = _model_for_family(settings)
    if family == "gpt":
        return OpenAIResponsesAdvisorProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    if family == "gemini":
        return GeminiGenerateContentAdvisorProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    if family == "claude":
        return AnthropicMessagesAdvisorProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    endpoint = "/chat/completions" if family == "deepseek" else "/v1/chat/completions"
    provider_name = "DeepSeek" if family == "deepseek" else "OpenAI Compatible"
    return ChatCompletionsAdvisorProvider(
        name=provider_name,
        api_key=api_key,
        model=model,
        base_url=base_url,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
    )


def _model_for_family(settings: AIModelSettings) -> str:
    """解析最终模型名。"""

    family = settings.ai_model_family
    model = settings.openai_model.strip()
    if family == "gpt":
        return model
    if family == "openai_compatible":
        # 兼容接口没有安全默认模型，必须由用户明确填写。
        if not model:
            raise AIAdvisorError("OpenAI Compatible 模型名称不能为空。")
        return model
    if model and model != DEFAULT_FAMILY_MODELS["gpt"]:
        # 用户填了非 OpenAI 默认模型时，尊重该模型名。
        return model
    return DEFAULT_FAMILY_MODELS[family]


def _required_base_url(base_url: str) -> str:
    """校验模型 API URL。"""

    value = base_url.strip()
    if not value:
        raise AIAdvisorError("模型 API URL 不能为空。")
    return value


def _task_prompt(user_prompt: str, schema: dict[str, Any], context: dict[str, Any]) -> str:
    """把任务说明、JSON Schema 和上下文拼成模型输入。"""

    return (
        f"{user_prompt}\n\n"
        "必须严格输出符合以下 JSON Schema 的 JSON 对象：\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "输入上下文：\n"
        f"{json.dumps(context, ensure_ascii=False)}"
    )


def _review_schema() -> dict[str, Any]:
    """总结 Agent 需要的最小 JSON Schema。"""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "key_insights": {"type": "array", "items": {"type": "string"}},
            "action_items": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "key_insights", "action_items", "limitations"],
    }


def _json_schema_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI Responses API 的 structured output 格式。"""

    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": schema,
    }


def _extract_openai_response_text(payload: dict[str, Any]) -> str:
    """从 Responses API 返回中提取文本。

    新版可能给 output_text；某些场景只给 output/content 结构，所以两种都支持。
    """

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "".join(chunks)
    raise AIAdvisorError("模型返回中没有可解析的文本内容。")


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    """从 Chat Completions 返回中提取 assistant content。"""

    choices = payload.get("choices") or []
    if choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content
    raise AIAdvisorError("Chat Completions 返回中没有可解析的文本内容。")


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    """从 Claude Messages 返回中拼接 text content。"""

    chunks: list[str] = []
    for item in payload.get("content", []):
        text = item.get("text")
        if isinstance(text, str):
            chunks.append(text)
    if chunks:
        return "".join(chunks)
    raise AIAdvisorError("Claude Messages 返回中没有可解析的文本内容。")


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    """从 Gemini candidates[0].content.parts 中拼接文本。"""

    candidates = payload.get("candidates") or []
    if not candidates:
        raise AIAdvisorError("Gemini 返回中没有候选内容。")
    parts = candidates[0].get("content", {}).get("parts", [])
    chunks = [part.get("text") for part in parts if isinstance(part.get("text"), str)]
    if chunks:
        return "".join(chunks)
    raise AIAdvisorError("Gemini 返回中没有可解析的文本内容。")


def _parse_json_payload(raw_text: str) -> dict[str, Any]:
    """解析模型返回的 JSON，并兼容被 Markdown 代码块包住的情况。"""

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIAdvisorError("模型返回内容不是有效 JSON。") from exc
    return payload


def _review(provider: str, model: str, payload: dict[str, Any]) -> AIAdvisorReview:
    """把模型 JSON 转成领域模型。"""

    return AIAdvisorReview(
        provider=provider,
        model=model,
        is_model_generated=True,
        summary=payload["summary"],
        key_insights=payload["key_insights"],
        action_items=payload["action_items"],
        limitations=payload["limitations"],
    )


def _join_url(base_url: str, endpoint: str) -> str:
    """安全拼接 base_url 和 endpoint，避免重复或缺失斜杠。"""

    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _bearer_headers(api_key: str) -> dict[str, str]:
    """Bearer Token 请求头。"""

    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def _post_json_with_retries(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> httpx.Response:
    """带有限重试的 JSON POST。

    只重试临时性错误：超时、网络错误、429、408 和 5xx。
    """

    attempt = 1
    while True:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            if not _should_retry_ai_request(exc, attempt):
                raise
            # 先等待再递增 attempt，让第 1 次失败使用第一个 backoff。
            await asyncio.sleep(_retry_delay_seconds(attempt))
            attempt += 1


def _should_retry_ai_request(exc: httpx.HTTPError, attempt: int) -> bool:
    """判断当前异常是否值得重试。"""

    max_attempts = AI_TIMEOUT_MAX_ATTEMPTS if isinstance(exc, httpx.TimeoutException) else (
        AI_REQUEST_MAX_ATTEMPTS
    )
    if attempt >= max_attempts:
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        # 4xx 中只有 408/429 通常可能短时间恢复；其它多半是配置或权限问题。
        return status_code in {408, 429} or 500 <= status_code <= 599
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError, httpx.PoolTimeout)):
        return True
    return False


def _retry_delay_seconds(attempt: int) -> float:
    """按尝试次数获取退避时间，超过列表长度后使用最后一个值。"""

    return AI_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(AI_RETRY_BACKOFF_SECONDS) - 1)]


def describe_ai_error(exc: Exception) -> str:
    """把底层异常转换为适合前端和 Agent rationale 展示的中文短句。"""

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        reason = exc.response.reason_phrase
        return f"模型接口返回 {status} {reason}。"
    if isinstance(exc, httpx.TimeoutException):
        return "模型接口请求超时。"
    if isinstance(exc, httpx.HTTPError):
        return "模型接口请求失败。"
    return str(exc)


def _jsonable(value: Any) -> Any:
    """把 Pydantic 对象递归转换成普通 JSON 数据。"""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _usable_api_key(value: str | None) -> str | None:
    """过滤空值和文档占位符。"""

    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped == "replace-me":
        return None
    return stripped
