"""Validated LLM settings and model aliases. No configuration is loaded on import."""

from pathlib import Path
from typing import Annotated

from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
MODELS_PATH = Path(__file__).with_name("models.json")
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class LLMConfigurationError(ValueError):
    """Configuration cannot be used; the message contains no secret values."""


def resolve_model(name: str) -> str:
    """Resolve only the approved aliases or their exact API IDs."""
    try:
        aliases = TypeAdapter(dict[NonEmptyText, NonEmptyText]).validate_json(
            MODELS_PATH.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        raise LLMConfigurationError("Не удалось прочитать корректный models.json") from None

    name = name.strip()
    if name in aliases:
        return aliases[name]
    if name in aliases.values():
        return name
    raise LLMConfigurationError("Модель отсутствует в разрешённом models.json")


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",  # The same .env also contains DATABASE_URL.
        frozen=True,
        hide_input_in_errors=True,
    )

    base_url: HttpUrl
    api_key: SecretStr = Field(min_length=1, repr=False)
    model: NonEmptyText
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    max_tokens: int = Field(default=256, gt=0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https" or any(
            part is not None
            for part in (value.username, value.password, value.query, value.fragment)
        ):
            raise ValueError("Нужен HTTPS URL без учётных данных и параметров")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key не должен быть пустым")
        return value

    @classmethod
    def from_env(cls) -> "LLMSettings":
        try:
            return cls()
        except (OSError, ValidationError):
            raise LLMConfigurationError(
                "Проверь LLM_BASE_URL, LLM_API_KEY, LLM_MODEL и числовые лимиты в .env"
            ) from None
