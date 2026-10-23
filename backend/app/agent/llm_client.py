from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from types import TracebackType
from typing import Self

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.agent.config import LLMSettings, resolve_model
from app.agent.schema import LLMReply, TokenUsage


class LLMError(RuntimeError):
    """Safe provider error for logging or returning from an API handler"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMClient:
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
