# Агент, LangGraph и офлайн-проверка

Актуальное подключение к единому main: [IGOR_INTEGRATION.md](IGOR_INTEGRATION.md).
`install_agent(app)` сохраняет приложение Игоря и добавляет insights/чат/trace
и персональную ленту. Хранение — в собственных `dima_agent_*` таблицах.
По умолчанию используется PostgreSQL FTS без embedding-модели; прежний hybrid
включается явно через `RAG_SEARCH_MODE=hybrid`. Лента не требует RAG/LLM.
Зависимости MVP: [requirements-mvp.txt](requirements-mvp.txt).

Старая инструкция прототипа: [Integration_to_igor.md](../../../Integration_to_igor.md).
Готовый сервер: `uvicorn app.agent.api:create_app --factory` (PYTHONPATH=backend).
Рабочая логика больше не находится в `app.test`: `LLMClient` включает failover
и JSON-протокол, `service.answer_question` — короткий чат с явным общим ответом
при недоступной LLM/недостатке доказательств. Память диалога обновляется после
каждого третьего вопроса; передавайте history/turn_count из репозитория.
Анализ сохраняет строгую проверку доказательств и 503 при недоступной LLM.

Полное объяснение архитектуры, потоков данных и интеграции с Игорем:
[EXPLANATION.md](../EXPLANATION.md).

`graph.py` реализует StateGraph с собственными узлами и ограниченными циклами;
`service.py` сохраняет публичные функции анализа и контекстного вопроса; `tools.py` — JSON- и сервисный
адаптеры; `recommendations.py` — объяснимый подбор новостей; `verifier.py` — проверки.
Обычная команда запуска ниже безопасна: используется `OfflineLLM`, без сети и ключей.
Живой клиент применяется только при явном подключении из backend.

## LLM-клиент

- `config.py`: настройки на `pydantic-settings`, проверка URL и выбор модели.
- `models.json`: соответствие коротких alias точным API ID моделей.
- `llm_client.py`: асинхронный `LLMClient`.
- `schema.py`: Pydantic-схемы запросов, анализа, источников, trace, чата
  и ответов LLM (`LLMReply`, `TokenUsage`).

`AgentAnalysis` содержит `result` и `agent_trace` до сохранения;
`InsightResponse` добавляет идентификаторы и статус для API.
Схемы проверяют уникальность источников и ссылки на них. Позиция банка
`found` требует текста и ссылки на банковский документ с номером страницы.
Verifier должен отдельно проверять содержание доказательств, пути
`company_fields` и ссылки ответа чата по сохранённому контексту.

Установить зависимости из `backend/requirements.txt` в окружение проекта.
Настройки читаются из `.env` в корне `alfa-insight`, независимо от текущей
директории. Переменные окружения имеют приоритет над `.env`.

| Переменная | Назначение |
| --- | --- |
| `LLM_BASE_URL` | HTTPS URL API организаторов, включая `/v1` |
| `LLM_API_KEY` | Ключ; хранить только в локальном `.env` или окружении |
| `LLM_MODEL` | Alias из `models.json` или точный ID из значений этого файла |
| `LLM_TIMEOUT_SECONDS` | Таймаут запроса, по умолчанию 30 секунд |
| `LLM_MAX_TOKENS` | Лимит выходных токенов, по умолчанию 256 |

`LLMSettings` валидирует настройки через Pydantic, ключ хранится как `SecretStr`.
Два URL проверялись с моделью `deepseek-v4.1`; доступность может меняться.
Настройки также включают `LLM_FALLBACK_BASE_URL`, `LLM_REASONING_EFFORT=none`
и `LLM_JSON_MODE=true`. Метрики доступны в `LLMClient.diagnostics`, без промптов.

Пример использования настоящего клиента внутри async-функции (выполнение расходует
лимит, в текущей работе не запускался):

```python
from app.agent.llm_client import LLMClient

async with LLMClient() as client:
    reply = await client.generate_text("Ответь одним словом: OK")
    print(reply.text)
```

Для диалога с историей или системной инструкцией используется
`await client.complete(messages)`. Оба метода принимают `model="kimi-k2.6"`
для смены модели на один вызов. Настройки можно передать явно через
`LLMClient(settings=LLMSettings(...))`.

В FastAPI клиент следует создать один раз на время lifespan приложения
и закрыть через `await client.aclose()` при завершении. Это позволяет
переиспользовать соединения и не блокировать event loop ожиданием LLM.

Ответ `LLMReply` содержит текст, модель, `TokenUsage`, время и причину завершения.
`finish_reason=length` означает достижение лимита выходных токенов.
Ошибки провайдера преобразуются в `LLMError` без тела ответа и ключа;
для HTTP-ошибок доступен `status_code`. Ошибки настроек — `LLMConfigurationError`,
пустые аргументы запроса — `ValueError`.

## Офлайн-отладка без токенов

`main()` использует только учебные JSON и офлайн-заглушку модели. Запуск из корня проекта:

```bash
PYTHONPATH=backend .venv/bin/python -m app.agent.main
```

Команда не создаёт LLMClient и не загружает `.env`. Риски и возможности в таком
примере остаются пустыми: реальный анализ моделью не имитируется.

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/app/agent/tests -v
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluate
```

В настоящем агенте ограничены количество LLM-вызовов, уточнение поиска и исправление
ответа. Сам SDK автоматических повторов не делает. Публичные DTO сохранены.
