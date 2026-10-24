# Игорю: подключение к единому backend

Моя часть находится в `app/agent` и `app/rag`. Твой код я не менял.
Агент, RAG, кеш, сохранение анализа, чат и журнал шагов готовы к подключению.

## В твоём main

После создания `app` и подключения своих роутеров добавь:

```python
from app.agent.integration import install_agent

install_agent(app)
```

Запуск остаётся обычным: `uvicorn app.main:app`. Твои роутеры и lifespan сохраняются.
Если `GET /api/news` уже реализован у тебя, используй `include_feed=False`.

## Перед первым запуском

В корневом `.env` нужны `DATABASE_URL`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
и `RAG_SEARCH_MODE=fts`. RAG использует ту же PostgreSQL. Модель E5 и pgvector
для этого режима не нужны. Зависимости моей части — в `requirements-mvp.txt` рядом.

Из корня проекта, в активированном окружении:

```bash
export PYTHONPATH=backend
python -m app.agent.persistence init-db
python -m app.rag.cli init-db --store fts
python -m app.rag.cli index-bank --store fts --input backend/app/rag/data/reviewed_corpus.json
```

Команды создают только мои таблицы `dima_agent_*` и `dima_rag_fts_*`.
Компании и новости берутся из твоей существующей БД. В корпусе — 10 сверенных
выдержек из 3 банковских документов; полный OCR всех PDF не импортирован.

## Запросы

- Лента: `GET /api/news?company_id=U_25835&limit=10`.
- Анализ: `POST /api/insights/analyze`, тело `{"company_id":"U_25835","news_id":"2"}`.
- Вопрос: `POST /api/insights/{insight_id}/ask`, тело `{"question":"Что проверить?"}`.
- Шаги: `GET /api/insights/{insight_id}/trace`.
- Сохранённый результат: `GET /api/insights/{insight_id}`.
- История: `GET /api/insights/{insight_id}/messages`.

Заменяй пример `2` реальным ID новости и передавай ID строками. Сохраняй полученный
`insight_id`: он нужен чату. `force_refresh=true` создаёт новый анализ и историю.
Показывай `warnings`; отсутствие позиции банка — допустимый результат.

Пока запускай один worker: очередь и защита от повторной генерации работают
внутри процесса. Старые анализы из прототипных таблиц автоматически не переносятся.

Проверены HTTP, настоящая PostgreSQL/FTS, кеш, чат и trace на синтетических данных.
Живой вызов LLM в этой доработке не выполнен: требуется разрешение на расход квоты
и отправку выдержки провайдеру.

Подробности, ошибки и вариант подключения через твои HTTP-обработчики:
[IGOR_INTEGRATION.md](IGOR_INTEGRATION.md).
