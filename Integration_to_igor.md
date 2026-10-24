# Интеграция финального прототипа — Игорю

Все команды ниже выполняются из корня `alfa-insight`. Рабочий backend больше
не зависит от `app.test` или корневой `tests/`. Company API, ORM и парсер Игоря
сохранены. Есть два способа: запустить готовую фабрику приложения или подключить
router и runtime к своему FastAPI. Не подключайте одновременно одинаковые маршруты.

## 1. Что уже готово

| Модуль | Назначение |
|---|---|
| `app.agent.api` | `create_app()`, `create_router()`, HTTP-схемы и обработчики ошибок |
| `app.agent.runtime` | Один LLM-клиент на процесс, lazy RAG, tools для любой выбранной компании |
| `app.agent.persistence` | Готовый SQLAlchemy-репозиторий: исходные Company/News, анализ, кеш, контекст, чат |
| `app.agent.service` | `run_analysis`, `analyze`, `answer_question` — прямые Python-вызовы |
| `app.agent.llm_client` | Два Base URL, безопасные метрики, ограниченное переключение |
| `app.agent.llm_protocol` | JSON-режим, допустимые поля компании, требования к доказательствам |
| `app.agent.chat` | Короткий ответ, пользовательские условия, резервный ответ, память на три вопроса |
| `app.rag.service_tools` | Поиск свежих новостей, пропуск удалённых записей старого индекса |
| `app.rag.hybrid` | Общая тематическая проверка результатов для PostgreSQL/JSON-поиска |
| `app.rag.ingest`, `review`, `cli`, `doctor` | OCR, сверка, индексация и проверка OCR |

В проекте один PostgreSQL с расширением `vector`: корпоративные таблицы,
`news_items`, `insights`, `chat_messages`, `dima_rag_records`, `dima_rag_state`.
Redis, Qdrant и отдельный OCR-сервер не нужны. OCR выполняется локальным
Tesseract через PyMuPDF в контейнере backend. E5 работает на CPU.

## 2. Docker: настройка и создание баз

Подготовлены `backend/Dockerfile.prototype`, `backend/.dockerignore`,
`compose.prototype.yml`, `.env.prototype.example`. Compose запускает backend
и PostgreSQL 16/pgvector; OCR и языки устанавливаются в Dockerfile.
Файлы отладки и их `.env` исключены из образа.

```bash
docker compose version
cp .env.prototype.example .env.prototype
```

Заполните `POSTGRES_PASSWORD` и `LLM_API_KEY` в `.env.prototype`.
Ключ можно взять из своего локального `.env`; в git он не нужен.
Пароль PostgreSQL задайте длинной шестнадцатеричной строкой: она безопасно
вставляется в URL подключения без дополнительного кодирования.

Для сокращения следующих команд в текущем bash:

```bash
dc() { docker compose --env-file .env.prototype -f compose.prototype.yml "$@"; }
dc config --quiet
dc build backend
dc up -d postgres
dc run --rm backend python -m app.agent.persistence init-db
dc run --rm backend python -m app.rag.cli init-db --store postgres
```

`backend/sql/schema.sql` создаёт исходные корпоративные таблицы при первом
старте пустого volume. `persistence init-db` добавляет только `news_items`,
`insights`, `chat_messages`; `rag.cli init-db` — расширение `vector` и таблицы
`dima_rag_*`. Эти операции выполняются явно, не на каждом HTTP-запросе.

Compose использует отдельный volume `prototype_db`, не отладочную БД порта 55432.
Если корпоративные данные уже в другой PostgreSQL, можно использовать её:
задайте `DATABASE_URL` для SQLAlchemy и `RAG_DATABASE_URL` для psycopg на одну БД,
обеспечьте наличие `vector` и выполните те же две команды инициализации.
Пароль в произвольном URL должен быть URL-encoded.

Если у Игоря уже есть другие таблицы с именами `insights`/`chat_messages`, сначала
сопоставьте схему с `persistence.py` или передайте собственный репозиторий.
`create_all` не преобразует существующую несовместимую таблицу в новую схему.

## 3. Данные компаний

Новая БД пустая: сначала загрузите корпоративные CSV или восстановите свою БД.
Имеющийся `scripts/import_csv.py` очищает корпоративные таблицы перед импортом,
поэтому следующий вариант предназначен только для новой БД прототипа.

В каталоге должны находиться `df_Uid_data_hack.csv`, `df_Fid_Uid_link_hack.csv`,
`df_Uid_payment_hack.csv` с исходными заголовками. CSV в образ не включаются.

```bash
CSV_DIR=/absolute/path/to/csv
dc run --rm -v "$CSV_DIR:/input:ro" backend sh -c \
  'DATABASE_URL="$RAG_DATABASE_URL" python scripts/import_csv.py --data-dir /input'
```

Для существующей наполненной корпоративной БД импорт повторять не нужно.

## 4. Модель и OCR

Один раз явно скачайте закреплённую E5-модель в общий volume `/models`:

```bash
dc run --rm backend python -m app.rag.cli download-model
dc run --rm backend python -m app.rag.doctor
```

Рабочий сервер использует `RAG_LOCAL_FILES_ONLY=1` и сам модель не скачивает.
`download-model` — единственная команда, явно разрешающая эту загрузку.
Индексация не расходует токены удалённой LLM. Параметры модели и размерность
384 должны совпадать на сервере и при индексации.

`doctor` проверяет языки `rus`, `eng` и действительно распознаёт тестовую
страницу через PyMuPDF. Успех — `ok: true`, `ocr_smoke: true`.
В Docker языки находятся в `/usr/share/tesseract-ocr/5/tessdata`.

Без Docker на Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng libgomp1 libpq5
tesseract --list-langs
export TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
export PYTHONPATH=backend
.venv/bin/python -m app.rag.doctor
```

Python-зависимости устанавливаются из `backend/requirements.txt`; для CPU
PyTorch см. последовательность установки в `Dockerfile.prototype`.

## 5. Банковские PDF: извлечение → сверка → индекс

Можно сначала проверить интеграцию с существующим сверенным фрагментом:

```bash
dc run --rm backend python -m app.rag.cli index-bank --store postgres \
  --input /srv/app/rag/data/bank_corpus.json
```

Это один проверенный банковский абзац, а не полностью подготовленная библиотека
всех PDF. Для расширения корпуса импортируйте реальные предоставленные документы:

```bash
PDF_DIR=/absolute/path/to/analitics
dc run --rm -v "$PDF_DIR:/input:ro" backend python -m app.rag.ingest \
  /input/ALFA-Macro_Insights-Policy_Rate_11Sep.pdf \
  --output /data/bank-corpus.json --id alfa-rate-2026-09-11 \
  --title 'Альфа-Банк: ключевая ставка, 11 сентября 2026' --date 2026-09-11 --ocr
```

Дата и название должны соответствовать документу. OCR-фрагменты создаются
с `reviewed=false`: сравните текст, числа и даты с PDF. Только после реальной
сверки выбранных страниц выполните, например:

```bash
dc run --rm backend python -m app.rag.review /data/bank-corpus.json \
  --id alfa-rate-2026-09-11 --pages 1 --confirm-reviewed
dc run --rm backend python -m app.rag.cli index-bank --store postgres \
  --input /data/bank-corpus.json
dc run --rm backend python -m app.rag.cli search --store postgres \
  --query 'ключевая ставка' --as-of 2026-10-20
```

`index-bank` заменяет банковскую часть индекса содержимым переданного корпуса;
держите все нужные документы в одном JSON. Непроверенные, будущие и слишком
старые банковские фрагменты не становятся подтверждённой позицией банка.
Пустой поиск — штатный результат, а не HTTP-ошибка анализа.

## 6. Новости

Имеющийся парсер Игоря можно запустить в том же образе:

```bash
dc run --rm backend python scripts/fetch_news.py --source all
dc run --rm backend python -m app.rag.cli index-db-news --store postgres
```

`index-db-news` индексирует последние 200 новостей из той же БД и сообщает,
сколько пропущено из-за отсутствия даты. Новые неиндексированные новости всё
равно доступны через SQL и резервный поиск. Удалённые записи старого индекса
не обрывают подбор. Для своего импорта доступны `news_records` и `store.upsert`.
Текущий парсер содержит две ленты одного Банка России: требование плана о двух
независимых издателях нужно закрыть добавлением источника на стороне news-модуля.

## 7. Простой запуск готового backend

```bash
dc up -d backend
dc ps
dc logs --tail 100 backend
```

Откройте `http://127.0.0.1:8000/docs`; состояние — `/health`.
Запускается `uvicorn app.agent.api:create_app --factory`, один worker.
Это готовый сервер без `app.test`. Он переиспользует Company API Игоря,
добавляет новости в формате контракта и endpoints анализа/чата.
Образ API не включает отладочную HTML-страницу; подключается основной frontend Игоря.
Для отдельного Vite dev-server задайте `CORS_ORIGINS` точными адресами frontend
через запятую (пример уже в `.env.prototype.example`). При интеграции в свой
`main.py` используйте свою CORS middleware; фабрика настраивает её автоматически.
Чтобы остановить с сохранением БД/моделей: `dc down` без `-v`.

Без Docker после подготовки БД, модели и переменных окружения:

```bash
PYTHONPATH=backend .venv/bin/python -m uvicorn app.agent.api:create_app --factory --host 127.0.0.1 --port 8000
```

## 8. Подключение к существующему FastAPI Игоря

Вместо фабрики можно использовать свой `main.py`. Минимальный пример:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.agent import persistence
from app.agent.api import create_router, install_error_handlers
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentLimits

@asynccontextmanager
async def lifespan(app):
    app.state.agent_runtime = AgentRuntime.from_env(persistence)
    app.state.agent_limits = AgentLimits(deadline_seconds=90)
    try:
        yield
    finally:
        await app.state.agent_runtime.aclose()

app = FastAPI(lifespan=lifespan)
app.include_router(create_router(include_news=False))
install_error_handlers(app)
# Далее существующие company/news routers Игоря.
```

Если lifespan уже есть, вставьте создание/закрытие runtime в него, сохранив
свою инициализацию. Не создавайте второй FastAPI поверх первого и второй LLM-клиент
на каждый запрос. `include_news=False` добавляет только insights/chat/trace/history;
Игорь оставляет свои news routes. Тогда его `GET /api/news/{id}` должен отдавать
`body`, `source`, строковый `id`, `topic_tags`, а `/api/news?company_id=...` —
персональный рейтинг. Готовая нормализация: `app.agent.tools.news_from_record`.
Её можно применить к ORM News без HTTP-вызова собственного сервера.

`install_error_handlers` устанавливает обработку AgentError и ошибок БД:
404/409/422/503. Если свои обработчики уже есть, сохраните их и перенесите
соответствующее отображение ошибок вместо повторной регистрации.

## 9. Прямые Python-функции для собственных endpoints

```python
from app.agent.service import run_analysis, analyze, answer_question
from app.agent.schema import InsightResponse
from app.agent.recommendations import recommend_news
```

`await run_analysis(company_id, news_id, tools, limits=...)` возвращает
`AnalysisRun`: `analysis` = `{result, agent_trace}`, `context` = закрытый снимок
компании/новости/доказательств, `usage` = расход по этапам. HTTP self-calls нет.

```python
runtime = request.app.state.agent_runtime
async with runtime.analysis_lock:
    tools = await runtime.tools(company_id)
    run = await run_analysis(company_id, news_id, tools)
    # Сохранить run.analysis, run.context, run.usage в своём репозитории.
    public = InsightResponse(insight_id=generated_id, company_id=company_id,
                             news_id=news_id, **run.analysis).model_dump(mode="json")
```

`await analyze(...)` возвращает только `{result, agent_trace}`. Для чата
предпочтителен `run_analysis`, потому что он отдаёт контекст для сохранения.
Снимок `context` не отправляйте на frontend и не заменяйте текущим профилем
другой компании. `insight_id` назначает сервер/репозиторий после анализа.

`await recommend_news(company_id, tools, limit=10)` возвращает контрактную
ленту с `relevance_score` и `relevance_reasons`, не вызывает LLM.

Для вопроса загрузите анализ вместе с закрытым context и историю:

```python
saved = await asyncio.to_thread(repo.load_insight, insight_id)
memory = await asyncio.to_thread(repo.chat_history, insight_id)
response = await answer_question(
    saved, question, runtime.chat_tools(saved["company_id"]),
    history=memory["items"], turn_count=memory["turn_count"],
)
await asyncio.to_thread(repo.save_answer, insight_id, question, response)
# response: {"answer": str, "source_ids": list[str], "warnings": list[str]}
```

Для этого фрагмента дополнительно нужен `import asyncio`. Чат не обращается
к E5 и не пересчитывает аналитику. Проверка scope использует company_id из
сохранённого анализа; тестовых allowlist из восьми компаний в рабочем коде нет.

## 10. Как устроены короткие ответы и память

- Один ответ модели: максимум 650 выходных токенов; текст до 600 символов,
  пользовательская цитата при необходимости до 180 символов. Ориентир — до 80 слов.
- На ошибочный JSON/доказательства допускается одно исправление, не бесконечный цикл.
- Если источников нет, сразу возвращаются общие следующие действия без вызова LLM.
- При недоступной LLM, таймауте или двух непрошедших проверку ответах возвращается
  короткий резервный текст с явной пометкой в warnings. Это не позиция банка.
- Пользовательские числа, например количество оборудования, не становятся
  доказательствами банковских ставок. Они допустимы в атрибутированной цитате.
- На вопросы 1/2/3 передаётся соответственно 0/1/2 предыдущих пары; вопрос 4
  начинает новый цикл. Анализ, компания и новость сохраняются. Затем 5/6/7 — 1/2/0.
- В модели не копится вся переписка. База может хранить её для интерфейса;
  `chat_history` выбирает только последние две пары и общее число вопросов.
- Ответы ассистента из истории не считаются подтверждёнными источниками.

Это явное изменение поведения чата по просьбе Димы: для корректного вопроса
нехватка доказательств/отказ LLM не превращается в 422/503. Анализ по-прежнему
возвращает 503 при отказе LLM. Неизвестный insight/company, неверный ввод и
недоступная БД сохраняют обычные HTTP-ошибки: ответ не подменяет проверку доступа.

## 11. Репозиторий и настройки

`AgentRuntime` принимает модуль/объект репозитория с синхронными методами:

| Метод | Результат |
|---|---|
| `profile(company_id)` | Исходный профиль выбранной компании |
| `load_news(news_id)` | Нормализованная новость или None |
| `list_news(limit=..., offset=...)` | Список нормализованных новостей |
| `payment_links(company_id, direction=..., limit=...)` | Доступные платёжные связи |
| `cache_key(profile, news, corpus_version, model_identity, prompt_version)` | Стабильный ключ |
| `cached(key)` | Публичный сохранённый анализ или None |
| `save_run(key, company_id, news_id, run)` | Сохраняет public/context/usage, возвращает public |
| `load_insight(insight_id)` | Публичный анализ плюс private `context`; неизвестный ID → NotFound |
| `chat_history(insight_id)` | `{items: последние две пары, turn_count: общее число}` |
| `save_answer(insight_id, question, response)` | Сохраняет вопрос и ответ |
| `messages(insight_id)` | История для интерфейса |
| `add_news(payload)` | Ручной текст, provenance=manual_submission, без загрузки URL |

Готовый `persistence.py` использует подключение `app.connect.SessionLocal` и
Company/News Игоря. Можно передать свой совместимый объект вместо него.
У каждой операции свой короткий SQLAlchemy Session; sync-код вызывается в thread.
Для одного worker `analysis_lock` также сериализует вопросы и защищает кеш от
повторной генерации. При переходе на несколько worker/реплик потребуется общий
замок/claim в БД, иначе локальный lock не координирует разные процессы.

Ключевые env: `DATABASE_URL`, `RAG_DATABASE_URL`, `LLM_BASE_URL`,
`LLM_FALLBACK_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`,
`LLM_REASONING_EFFORT=none`, `LLM_JSON_MODE=true`, `RAG_MODEL_CACHE`,
`RAG_LOCAL_FILES_ONLY=1`. Base URL переключается при timeout/connection/408/5xx
или пустом финальном stop. Ошибки ключа/квоты не умножают сетевые попытки.
После успешного переключения используется резервный адрес до перезапуска.
Метрики доступны как `runtime.llm.diagnostics`; ключи, промпты и reasoning не пишутся.

## 12. Контроль интеграции

Через Swagger: companies → news → analyze → сохранённый insight → ask → trace.
`GET /api/news/{id}` теперь включает `topic_tags`. Публичные DTO анализа и
чата сохраняют структуру API_CONTRACT.md. Дополнительно есть получение
сохранённого анализа и истории. Роль сотрудника пока не меняет серверный анализ.

Бесплатные проверки:

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/app/agent/tests -q
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/app/rag/tests -q
# Этот интеграционный набор требует отдельную подготовленную тестовую БД:
backend/app/test/ch.sh prepare
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/app/test/tests -q
```

На 24.10.2026: 62 теста агента, 8 RAG, 13 HTTP-интеграционных проверок прошли;
один отдельный opt-in RAG/PostgreSQL тест пропущен. HTTP-набор прогнал все
8 компаний локальной БД и реальный PostgreSQL/E5 с имитацией LLM; отказ модели
не мешает получить ответ. Дублирующие entrypoint-тесты стенда не суммируются.
Проверены сброс памяти, кеш, отсутствие private context в ответе и независимость
рабочих модулей от app.test. OCR smoke и `docker compose config --quiet` прошли.
Один живой вопрос о станках после переноса также вернул HTTP 200: один вызов,
2103 входных + 292 выходных = 2395 токенов, reasoning=0, исправление не понадобилось.
Отчёт стенда: `backend/app/test/.data/core-chat-after-migration.json`.
Полная сборка нового Docker-образа здесь не выполнялась; перед релизом выполните
`dc build backend` и описанный старт с чистыми volumes/реальными данными.

Не запускайте платные smoke-скрипты автоматически на каждом старте контейнера.
Список материалов, которые не нужны для работы backend, — `Debug_only_files.md`.
