from __future__ import annotations

from collections.abc import Sequence
from collections import deque
import logging
from time import perf_counter
from types import TracebackType
from typing import Self

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.agent.config import LLMSettings, resolve_model
from app.agent.errors import LLMError
from app.agent.llm_protocol import CompletionAdapter, POLICY_VERSION
from app.agent.schema import LLMReply, TokenUsage

logger = logging.getLogger(__name__)


class _SingleEndpointClient:
    """Reuse one client in a FastAPI lifespan or an async context manager.

    No automatic retries; each completion makes at most one API attempt.
    The caller owns conversation history and agent orchestration.
    """

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings if settings is not None else LLMSettings.from_env()
        self._model = resolve_model(self.settings.model)
        self._client = AsyncOpenAI(
            api_key=self.settings.api_key.get_secret_value(),
            base_url=str(self.settings.base_url),
            timeout=self.settings.timeout_seconds,
            max_retries=0,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    @property
    def cache_identity(self) -> str:
        return self._model

    async def complete(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMReply:
        """Send a conversation and return a validated text reply and usage."""
        if not messages:
            raise ValueError("Список сообщений не должен быть пустым")
        if max_tokens is not None and not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens должен быть от 1 до 8192")
        model_id = resolve_model(model) if model is not None else self._model
        started = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                # Kept for this third-party Chat Completions endpoint.
                max_tokens=max_tokens if max_tokens is not None else self.settings.max_tokens,
            )
        except APITimeoutError:
            raise LLMError("Истекло время ожидания LLM") from None
        except APIConnectionError:
            raise LLMError("Не удалось подключиться к API LLM") from None
        except APIStatusError as exc:
            messages_by_status = {
                401: "Ключ отклонён провайдером",
                403: "Доступ к модели запрещён",
                404: "Не найден endpoint или модель",
                429: "Превышен лимит запросов или квота",
            }
            message = messages_by_status.get(exc.status_code, "Ошибка API LLM")
            raise LLMError(
                f"{message} (HTTP {exc.status_code})", status_code=exc.status_code
            ) from None
        except APIError:
            raise LLMError("SDK не смог обработать ответ LLM") from None

        if not response.choices:
            raise LLMError("Провайдер вернул ответ без вариантов завершения")
        choice = response.choices[0]
        if not choice.message.content or not choice.message.content.strip():
            raise LLMError("Провайдер вернул ответ без текста")
        usage = response.usage
        return LLMReply(
            text=choice.message.content,
            model=response.model,
            usage=TokenUsage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            ) if usage is not None else None,
            elapsed_seconds=round(perf_counter() - started, 3),
            finish_reason=choice.finish_reason,
        )

    async def generate_text(self, prompt: str, *, model: str | None = None) -> LLMReply:
        """Convenience method for a single user message."""
        if not prompt.strip():
            raise ValueError("Текст запроса не должен быть пустым")
        return await self.complete([{"role": "user", "content": prompt}], model=model)


def unavailable(error: LLMError) -> bool:
    # The shared client deliberately hides raw SDK errors. These two public
    # messages represent only timeout/connection failures; malformed replies do not.
    diagnostics = getattr(error, "diagnostics", {})
    empty_final = diagnostics.get("finish_reason") == "stop" and diagnostics.get("content_characters") == 0
    return (empty_final or error.status_code == 408
            or (error.status_code is not None and 500 <= error.status_code <= 599)
            or (error.status_code is None and str(error) in {
                "Истекло время ожидания LLM", "Не удалось подключиться к API LLM"}))


class LLMClient(_SingleEndpointClient):
    def __init__(self, settings: LLMSettings | None = None):
        settings = settings if settings is not None else LLMSettings.from_env()
        super().__init__(settings)
        self.diagnostics = deque(maxlen=50)
        self._client = CompletionAdapter(self._client, settings, "primary", self._record)
        self._fallback = None
        self._use_fallback = False
        if settings.fallback_base_url and str(settings.fallback_base_url).rstrip("/") != str(settings.base_url).rstrip("/"):
            self._fallback = _SingleEndpointClient(settings.model_copy(update={"base_url": settings.fallback_base_url}))
            self._fallback._client = CompletionAdapter(self._fallback._client, settings, "fallback", self._record)

    def _record(self, metrics):
        self.diagnostics.append(metrics)
        logger.info("LLM response metrics: %s", metrics)

    @property
    def cache_identity(self):
        return f"{super().cache_identity}:{POLICY_VERSION}:{self.settings.reasoning_effort}:{self.settings.json_mode}"

    @property
    def active_endpoint(self):
        return "fallback" if self._use_fallback else "primary"

    async def complete(self, messages, *, model=None, max_tokens=None):
        if self._use_fallback:
            return await self._fallback.complete(messages, model=model, max_tokens=max_tokens)
        try:
            return await super().complete(messages, model=model, max_tokens=max_tokens)
        except LLMError as exc:
            if self._fallback is None or not unavailable(exc):
                raise
        logger.warning("Основной LLM endpoint недоступен; запрос направлен на резервный")
        # One extra attempt with the same approved model, key, messages and limits.
        # A successful switch persists until the client/server is restarted.
        reply = await self._fallback.complete(messages, model=model, max_tokens=max_tokens)
        self._use_fallback = True
        return reply

    async def aclose(self):
        try:
            await super().aclose()
        finally:
            if self._fallback is not None:
                await self._fallback.aclose()
