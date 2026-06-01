import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.core.config import (
    AI_PROVIDER_LABELS,
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

DEFAULT_FAMILY_MODELS = {
    "gpt": "gpt-5.4-mini",
    "openai_compatible": "gpt-5.4-mini",
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-5",
    "deepseek": "deepseek-v4.1",
}


class AIAdvisorError(RuntimeError):
    pass


class AIAdvisorProvider(Protocol):
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


class MockAIAdvisorProvider:
    name: str = "Mock AI"
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
        baseline = context.get("baseline")
        if isinstance(baseline, dict):
            return baseline
        raise AIAdvisorError(f"{task_name} 没有可用的本地规则基线。")

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        risk = context["risk_assessment"]
        allocation = context["allocation"]
        return_analysis = context["return_analysis"]
        compliance = context["compliance_review"]
        top_bucket = max(allocation["buckets"], key=lambda item: item["target_weight_pct"])

        return AIAdvisorReview(
            provider=self.name,
            model=self.model,
            is_model_generated=self.is_model_generated,
            summary=(
                "当前未启用真实大模型，以下为本地规则解读。"
                f"组合风险等级为 {risk['risk_level']}，最大权重资产为 "
                f"{top_bucket['instrument']}，预期年化收益约为 "
                f"{return_analysis['expected_annual_return_pct']}%。"
            ),
            key_insights=[
                f"风险评分为 {risk['risk_score']}/100，权益上限为 {risk['max_equity_pct']}%。",
                (
                    f"{top_bucket['instrument']} 权重最高，目标占比 "
                    f"{top_bucket['target_weight_pct']}%。"
                ),
                "配置结果已通过规则层合规检查，但仍需要结合真实客户适当性材料复核。",
            ],
            action_items=[
                "接入模型 API Key 后可获得真实模型生成的中文投顾解读。",
                "执行前确认客户身份、风险问卷、资金来源和产品准入清单。",
                "定期复核行情数据授权、资产漂移和人工复核触发条件。",
            ],
            limitations=[
                "本地 mock 不是大模型推理结果。",
                *compliance["warnings"],
            ],
        )

    async def close(self) -> None:
        return None


class DisabledAIAdvisorProvider:
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
        response = await self.client.post(
            _join_url(self.base_url, "/v1/responses"),
            headers=_bearer_headers(self.api_key),
            json={
                "model": self.model,
                "instructions": system_instructions,
                "input": _task_prompt(user_prompt, schema, context),
                "text": {"format": _json_schema_format(task_name, schema)},
                "max_output_tokens": 1200,
            },
        )
        response.raise_for_status()
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
        response = await self.client.post(
            _join_url(self.base_url, self.endpoint),
            headers=_bearer_headers(self.api_key),
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": _task_prompt(user_prompt, schema, context)},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 1200,
            },
        )
        response.raise_for_status()
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
        prompt = _task_prompt(user_prompt, schema, context)
        response = await self.client.post(
            _join_url(self.base_url, "/v1/messages"),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1200,
                "system": system_instructions,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
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
        endpoint = f"/v1beta/models/{self.model}:generateContent"
        prompt = _task_prompt(user_prompt, schema, context)
        response = await self.client.post(
            _join_url(self.base_url, endpoint),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
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
        response.raise_for_status()
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
    def __init__(self, provider: AIAdvisorProvider) -> None:
        self.provider = provider

    async def create_review(self, context: dict[str, Any]) -> AIAdvisorReview:
        return await self.provider.create_review(_jsonable(context))

    async def generate_json(
        self,
        task_name: str,
        system_instructions: str,
        user_prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.provider.generate_json(
            task_name=task_name,
            system_instructions=system_instructions,
            user_prompt=user_prompt,
            schema=schema,
            context=_jsonable(context),
        )

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


def build_ai_advisor_service(settings: Settings, agent_key: str | None = None) -> AIAdvisorService:
    model_settings = resolve_ai_model_settings(settings, agent_key)
    provider_name = model_settings.ai_advisor_provider
    model_api_key = _usable_api_key(model_settings.openai_api_key)
    if provider_name == "disabled":
        provider: AIAdvisorProvider = DisabledAIAdvisorProvider()
    elif provider_name == "mock" or (provider_name == "auto" and not model_api_key):
        provider = MockAIAdvisorProvider()
    else:
        if not model_api_key:
            provider_label = AI_PROVIDER_LABELS.get(provider_name, provider_name)
            raise AIAdvisorError(f"AI 提供方为 {provider_label} 时必须配置模型 API Key。")
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
    family = settings.ai_model_family
    model = _model_for_family(settings)
    base_url = _required_base_url(settings.openai_base_url)
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
    family = settings.ai_model_family
    model = settings.openai_model.strip()
    if family in {"gpt", "openai_compatible"}:
        return model
    if model and model != DEFAULT_FAMILY_MODELS["gpt"]:
        return model
    return DEFAULT_FAMILY_MODELS[family]


def _required_base_url(base_url: str) -> str:
    value = base_url.strip()
    if not value:
        raise AIAdvisorError("模型 API URL 不能为空。")
    return value


def _task_prompt(user_prompt: str, schema: dict[str, Any], context: dict[str, Any]) -> str:
    return (
        f"{user_prompt}\n\n"
        "必须严格输出符合以下 JSON Schema 的 JSON 对象：\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "输入上下文：\n"
        f"{json.dumps(context, ensure_ascii=False)}"
    )


def _review_schema() -> dict[str, Any]:
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
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": schema,
    }


def _extract_openai_response_text(payload: dict[str, Any]) -> str:
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
    choices = payload.get("choices") or []
    if choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content
    raise AIAdvisorError("Chat Completions 返回中没有可解析的文本内容。")


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("content", []):
        text = item.get("text")
        if isinstance(text, str):
            chunks.append(text)
    if chunks:
        return "".join(chunks)
    raise AIAdvisorError("Claude Messages 返回中没有可解析的文本内容。")


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise AIAdvisorError("Gemini 返回中没有候选内容。")
    parts = candidates[0].get("content", {}).get("parts", [])
    chunks = [part.get("text") for part in parts if isinstance(part.get("text"), str)]
    if chunks:
        return "".join(chunks)
    raise AIAdvisorError("Gemini 返回中没有可解析的文本内容。")


def _parse_json_payload(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIAdvisorError("模型返回内容不是有效 JSON。") from exc
    return payload


def _review(provider: str, model: str, payload: dict[str, Any]) -> AIAdvisorReview:
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
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _bearer_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _usable_api_key(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped == "replace-me":
        return None
    return stripped
