# Диме: как устроены агент, OCR и RAG и как подключить их к приложению

Этот документ относится к актуальному коду в `development/alfa-insight`.
Дима отвечает за `app/agent`, `app/rag` и эту инструкцию. Основной `app/main.py`,
Company API, модели, подключение БД, RSS-парсер и frontend Игоря не изменялись.
Общие зависимости собраны в `backend/requirements.txt`; отдельного requirements в rag больше нет.

## 1. Что делает наша часть простыми словами

Пользователь выбирает компанию и новость. Компания передаётся по ID из данных банка.
Наша система получает её профиль, находит подтверждающие материалы, объясняет связь
события с бизнесом, проверяет ссылки и возвращает структурированный ответ.
Одинаковая новость может иметь разный смысл для разных компаний.

Есть два отдельных процесса:

1. **Подготовка данных**: прочитать PDF → распознать изображения → проверить текст →
   при необходимости вызвать VLM → вручную сверить → разбить на фрагменты →
   вычислить векторы → сохранить. Это отдельная команда, а не действие каждого посетителя.
2. **Ответ пользователю**: получить компанию/новость → составить план → найти уже
   подготовленные фрагменты → сделать анализ → проверить → вернуть ответ.
   Векторизуется только поисковый запрос; документы заново не распознаются.

OCR — чтение букв на картинке. VLM — языковая модель, которая также видит изображения.
Embedding — 384 числа, представляющие смысл фрагмента. pgvector — расширение
PostgreSQL для хранения и сравнения таких чисел. RAG — поиск источников перед ответом модели.

## 2. Карта файлов: с чего начать чтение

| Файл | Важные функции и назначение |
|---|---|
| [agent/service.py](agent/service.py) | [run_analysis](agent/service.py#L169), [analyze](agent/service.py#L187), [answer_question](agent/service.py#L192): стабильные функции для Игоря |
| [agent/graph.py](agent/graph.py) | [analysis_graph](agent/graph.py#L147): сборка графа; [plan_analysis](agent/graph.py#L62), [find_evidence](agent/graph.py#L73), [verify_analysis](agent/graph.py#L113): его шаги |
| [agent/state.py](agent/state.py) | [AgentState](agent/state.py#L25): контекст одного анализа; [AgentLimits](agent/state.py#L15): ограничения |
| [agent/schema.py](agent/schema.py) | Pydantic-схемы JSON: [InsightResponse](agent/schema.py#L118), [InsightResult](agent/schema.py#L73), [AnswerResponse](agent/schema.py#L125) |
| [agent/tools.py](agent/tools.py) | [ServiceTools](agent/tools.py#L108): подключение сервисов Игоря; [JsonTools](agent/tools.py#L61): тестовые данные; [news_from_record](agent/tools.py#L29): адаптация новости |
| [rag/service_tools.py](rag/service_tools.py) | [HybridServiceTools](rag/service_tools.py#L8): тот же интерфейс с гибридным поиском по БД/JSON |
| [agent/recommendations.py](agent/recommendations.py) | [recommend_news](agent/recommendations.py#L76), [rank_news](agent/recommendations.py#L15): объяснимый подбор новостей |
| [agent/verifier.py](agent/verifier.py) | [verify_result](agent/verifier.py#L30), [verify_answer](agent/verifier.py#L85): проверка источников, выдержек, дат, чисел и полей компании |
| [agent/prompts.py](agent/prompts.py) | [messages](agent/prompts.py#L22), `SYSTEM`: инструкции модели; данные источников передаются отдельно |
| [agent/llm_client.py](agent/llm_client.py) | [LLMClient.complete](agent/llm_client.py#L58): один запрос к разрешённой модели, без автоматических повторов |
| [rag/ingest.py](rag/ingest.py) | [extract_pdf](rag/ingest.py#L56), [assess_quality](rag/ingest.py#L43), [import_pdf](rag/ingest.py#L109): PDF, Tesseract, контроль качества, сохранение |
| [rag/vlm.py](rag/vlm.py) | [extract_page](rag/vlm.py#L31), [enrich_pdf](rag/vlm.py#L57): Qwen3-VL для выбранных страниц, кеш и учёт вызовов |
| [rag/review.py](rag/review.py) | [approve_pages](rag/review.py#L8): явное подтверждение сверенных страниц |
| [rag/models.py](rag/models.py) | [Document](rag/models.py#L7), [Chunk](rag/models.py#L20), [PageExtraction](rag/models.py#L31), [Corpus](rag/models.py#L41): структура корпуса |
| [rag/embeddings.py](rag/embeddings.py) | [get_encoder](rag/embeddings.py#L97): одна модель на процесс; [E5Encoder](rag/embeddings.py#L43): query/passages/split_passage |
| [rag/index.py](rag/index.py) | [bank_records](rag/index.py#L11), [news_records](rag/index.py#L28), [prepare](rag/index.py#L44): подготовка записей и переиспользование векторов |
| [rag/postgres.py](rag/postgres.py) | [PgVectorStore.initialize](rag/postgres.py#L23), [upsert](rag/postgres.py#L50), [search](rag/postgres.py#L75): отдельные таблицы и поиск |
| [rag/schema.sql](rag/schema.sql) | Только наши таблицы `dima_rag_records`, `dima_rag_state` и индексы |
| [rag/json_store.py](rag/json_store.py) | [JsonVectorStore](rag/json_store.py#L10): такой же интерфейс для разработки без PostgreSQL |
| [rag/hybrid.py](rag/hybrid.py) | [fuse](rag/hybrid.py#L12): объединение лексического и семантического ранжирования |
| [rag/cli.py](rag/cli.py) | Команды загрузки модели, индексации и поиска |
| [agent/main.py](agent/main.py) | Бесплатная офлайн-демонстрация агента |
| [agent/live_check.py](agent/live_check.py) | Явная проверка с удалённой моделью, максимум два запроса |
| [agent/data/fixtures.json](agent/data/fixtures.json) | Синтетические компании и новости для тестов |
| [rag/data/bank_corpus.json](rag/data/bank_corpus.json) | Один ранее сверенный реальный фрагмент банковской аналитики |


## 3. Что именно делает LangGraph

Мы используем собственный `StateGraph`, без готового автономного агента.

```mermaid
flowchart TD
  load[Загрузить компанию и новость] --> plan[Составить план]
  plan --> search[Найти доказательства]
  search --> assess[Оценить достаточность]
  assess -->|Нет доказательств и остался бюджет| refine[Уточнить поиск]
  refine --> plan
  assess -->|Есть данные или уточнение исчерпано| generate[Сформировать анализ]
  generate --> verify[Проверить результат]
  verify -->|Одна разрешённая попытка| repair[Исправить]
  repair --> generate
  verify -->|Проверка пройдена| finish[Завершить]
  verify -->|Исправление не помогло| error[Вернуть ошибку]
```

`load_company_and_news` проверяет ID и наличие текста. `plan_analysis` просит LLM
предложить максимум два коротких запроса. `find_evidence` вызывает только наши
read-only инструменты. `assess_evidence` проверяет наличие допустимого банковского
источника; это простая проверка достаточности, а не оценка смысла всех доказательств.
Если источников нет, разрешено одно уточнение. Отсутствие позиции банка допустимо.

`generate_analysis` получает JSON. `verify_analysis` проверяет его. Некорректный JSON
или неподтверждённые утверждения допускают одну попытку исправления, затем ошибка.
План, повторный поиск и исправление вместе ограничены общим числом вызовов.

По умолчанию: 4 LLM-вызова, 8 инструментальных шагов, 90 секунд; до 2000 выходных токенов
на план и 3000 на анализ, максимум 5 банковских
фрагментов в контексте. SDK сам не повторяет запросы. Проверяемые факты и позиция
банка должны быть выдержками из полученных источников. Это не доказывает правильность
всей интерпретации: экспертная оценка всё равно нужна.

Скомпилированный граф создаётся один раз. Состояние каждого запроса отдельное.
**Промежуточные checkpoints в БД сейчас не включены.** Сбой процесса может прервать
текущий запрос. Финальный анализ и контекст должен сохранять backend Игоря.
Наличие LangGraph само по себе не обеспечивает продолжение запроса на другой VM.

## 4. Установка на Ubuntu

Все команды ниже выполняются из корня `alfa-insight`, где находятся `backend` и `frontend`.
На новой Ubuntu используйте Python 3.12+ и отдельное виртуальное окружение.
Не пересоздавайте уже настроенное окружение без необходимости.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng libgomp1
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install 'torch==2.14.1' --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r backend/requirements.txt
tesseract --list-langs
```

В языках должны быть `rus` и `eng`. Tesseract — системная программа и языковые файлы,
поэтому его нельзя установить одной строкой Python requirements. PyMuPDF использует
интеграцию с движком; `pytesseract` для этого кода не требуется.
Если языки не обнаруживаются, найдите каталог `tessdata` и установите
`TESSDATA_PREFIX` на сервере. Пример пути: `/usr/share/tesseract-ocr/5/tessdata`.
Номер каталога зависит от Ubuntu и установленной версии.

Установка CPU PyTorch перед общими requirements предотвращает загрузку CUDA-пакетов.
Пакеты `langgraph`, `sentence-transformers`, `pgvector`, `numpy` и PyMuPDF описаны
в общем requirements с комментариями справа. Расширение pgvector должно быть
установлено также **на сервере PostgreSQL**: Python-пакет не заменяет расширение.

## 5. Сначала запусти без сети, БД и токенов

```bash
PYTHONPATH=backend .venv/bin/python -m app.agent.main --state-dir .dima-data/demo
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/app/agent/tests -v
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/app/rag/tests -v
PYTHONPATH=backend .venv/bin/python -m app.rag.evaluate
```

Демонстрация использует `OfflineLLM` и синтетические данные. Она показывает граф,
источники, кеш и уточняющий вопрос; **не генерирует настоящие риски и возможности**.
JSON анализов сохраняется в указанную папку. Это проверка в одном процессе,
не хранилище для двух серверов. Тесты RAG используют явный тестовый encoder для
проверки хранения и повторной индексации; качество настоящей E5 ими не измеряется.
`rag.evaluate` проверяет старый небольшой лексический корпус, а не весь гибридный поиск.

## 6. Как подготовить PDF

Ниже путь к PDF из папки `analitics` в корне `alfa-insight`.
Само добавление PDF в эту папку не запускает импорт или индексацию.
На сервере сохраните эту структуру либо замените путь на свой.
Дата и название задаются явно: парсер не угадывает метаданные по имени файла.

```bash
PYTHONPATH=backend .venv/bin/python -m app.rag.ingest \
  analitics/ALFA-Macro_Insights-Policy_Rate_11Sep.pdf \
  --output .dima-data/bank-corpus.json \
  --id alfa-rate-2026-09-11 \
  --title 'Ключевая ставка: обзор Альфа-Банка' \
  --date 2026-09-11 --ocr --dpi 300 \
  --image-dir .dima-data/pages/alfa-rate-2026-09-11
```

Извлечение идёт постранично. Даже если заголовок имеет текстовый слой, большая
растровая картинка на странице запускает OCR. Числа, даты и знаки сохраняются.
Проблемы отмечаются в `pages[].issues`: мало текста, подозрительные символы,
много чисел, возможный график/таблица. Эти проверки эвристические: они могут
пропустить плохо распознанную таблицу. Наличие графиков проверяйте и глазами.
Для отмеченных страниц сохраняются PNG. Номер страницы физический, начиная с 1.

Все новые фрагменты имеют `reviewed=false`. Это выбранная консервативная политика:
автоматический quality check не заменяет сверку банковских цифр.
Страницы без полезного текста сохраняются как проблемные, но не становятся
пустыми фрагментами. При отсутствии OCR-движка импорт завершится ошибкой и не
перезапишет прежний корпус. Повтор неизменного импорта с теми же параметрами
не выполняет OCR заново. Одновременные изменения одного JSON-файла не поддерживаются.

## 7. Как применить VLM к сложной странице

В корне `alfa-insight` должен быть ваш локальный `.env` с `LLM_BASE_URL`,
`LLM_API_KEY`, `LLM_MODEL`. Он читается LLM-клиентом независимо от рабочей папки.
Не публикуйте `.env`. Для VLM используем alias `qwen3-vl` из `models.json`.
Провайдер должен поддерживать изображения в Chat Completions; обычного текстового
endpoint недостаточно. Локально веса 30B-модели не загружаются.

```bash
PYTHONPATH=backend .venv/bin/python -m app.rag.vlm \
  analitics/ALFA-Macro_Insights-Policy_Rate_11Sep.pdf \
  --corpus .dima-data/bank-corpus.json --id alfa-rate-2026-09-11 \
  --pages 1 --max-pages 2
```

Это платная команда: максимум по одному запросу на выбранную некешированную страницу,
без скрытых повторов. По умолчанию нельзя обработать больше двух страниц за запуск.
Кеш учитывает hash PDF, страницу, параметры и версию prompt. Повтор использует кеш.
Частично выполненные платные страницы кешируются, даже если следующая не удалась;
корпус заменяется только после успешной обработки выбранных страниц.

VLM возвращает текст, Markdown-таблицы, описания рисунков и список нечитаемых областей.
Описания рисунков явно помечены как результат VLM. Нельзя выдавать догадку модели
о графике за дословную позицию банка. Фрагменты после VLM тоже остаются непроверенными.
VLM может ошибаться и в числах, и в связях строк/столбцов.

После сравнения с оригиналом подтвердите выбранные страницы:

```bash
PYTHONPATH=backend .venv/bin/python -m app.rag.review \
  .dima-data/bank-corpus.json --id alfa-rate-2026-09-11 \
  --pages 1 --confirm-reviewed
```

Если на странице ошибка, сначала исправьте текст соответствующего фрагмента в JSON,
затем подтверждайте. Команда подтверждения сама не сверяет содержание.
Далее обязательно повторите индексацию: изменение reviewed меняет допустимость поиска.

## 8. Embeddings и работа без БД

Настройки RAG читаются из **окружения процесса**, а не автоматически из LLM `.env`.
Образец: [rag/settings.env.example](rag/settings.env.example).
Для начала можно использовать такие переменные:

```bash
export RAG_MODEL_CACHE="$PWD/.dima-data/models"
export RAG_CPU_THREADS=2
export RAG_BATCH_SIZE=8
export TOKENIZERS_PARALLELISM=false
PYTHONPATH=backend .venv/bin/python -m app.rag.cli download-model
```

Первый запуск скачает `intfloat/multilingual-e5-small` и выведет identity с commit hash.
По умолчанию код уже фиксирует ревизию `614241f622f53c4eeff9890bdc4f31cfecc418b3`.
Запишите hash после `@` до `:mean`, установите его в `RAG_EMBEDDING_REVISION` на обоих
серверах. После предварительного скачивания можно задать `RAG_LOCAL_FILES_ONLY=1`.
`main` допустим для первого знакомства, но не для воспроизводимого релиза.

Модель CPU-only, загружается лениво один раз на процесс, вызовы сериализованы для
контроля временной памяти. `query:` используется для запросов, `passage:` — для
источников. Векторы нормализованы, размерность 384. Длинные тексты режутся по токенам
с перекрытием до вызова модели: лимит 512 не приводит к незаметной потере хвоста.
Веса и tokenizer сами по себе не работают без движка; здесь движок PyTorch через
sentence-transformers. ONNX/INT8 в этой версии не включён.

Для первого запуска можно индексировать уже сверенный небольшой корпус:

```bash
PYTHONPATH=backend .venv/bin/python -m app.rag.cli index-bank \
  --input backend/app/rag/data/bank_corpus.json
PYTHONPATH=backend .venv/bin/python -m app.rag.cli index-news \
  --input backend/app/agent/data/fixtures.json
PYTHONPATH=backend .venv/bin/python -m app.rag.cli search \
  --query 'ключевая ставка и стоимость кредита' --as-of 2026-10-23
PYTHONPATH=backend .venv/bin/python -m app.rag.cli search \
  --kind news --query 'строительство' --as-of 2026-10-23
```

Индекс с настоящими векторами окажется в `.dima-data/vector-index.json`.
После подготовки своего корпуса замените `--input` на `.dima-data/bank-corpus.json`.
У синтетических новостей в fixtures явно отмечено происхождение.
Импорт новости без даты запрещён: дату не придумываем. Backend должен решить,
добавить ли подтверждённую дату или оставить такую новость вне векторного индекса.

Текст и версия модели образуют fingerprint. Повторный импорт того же текста
переиспользует вектор; изменение названия/даты/проверенности обновляет метаданные
без пересчёта неизменного текста. Новое содержание вычисляется заново.
Если поменялась версия модели, старый индекс не смешивается с новым: нужна полная
переиндексация. Для JSON создайте новый файл через `--index`.

## 9. PostgreSQL + pgvector

Для разработки есть необязательный [compose.dima.yml](../../compose.dima.yml).
Он поднимает только отдельную БД и CLI нашей части, не весь сайт Игоря.
[Dockerfile.dima](../Dockerfile.dima) содержит Tesseract, языки и CPU-зависимости.
Docker для работы из виртуального окружения не обязателен, но полезен для повторяемого
облачного запуска. В полноценном приложении Игорь может перенести эти зависимости
в общий Dockerfile. Не нужно заводить второй публичный API только для RAG.

```bash
export DIMA_DB_PASSWORD='replace_with_long_random_alphanumeric_password'
docker compose -f compose.dima.yml up -d rag-db
docker compose -f compose.dima.yml build rag
docker compose -f compose.dima.yml run --rm rag init-db
docker compose -f compose.dima.yml run --rm rag download-model
docker compose -f compose.dima.yml run --rm \
  -v "$PWD/backend/app/rag/data:/input:ro" \
  rag index-bank --store postgres --input /input/bank_corpus.json
docker compose -f compose.dima.yml run --rm rag search \
  --store postgres --query 'ключевая ставка' --as-of 2026-10-23
```

Используйте пароль без специальных URL-символов в этом учебном примере, либо корректно
URL-encode его в DSN. Compose не публикует порт PostgreSQL наружу.
Данные и модели находятся в named volumes. `docker compose down` останавливает
сервисы; **не добавляйте `-v`, если хотите сохранить данные**.
Для вызовов LLM в CLI-контейнере передавайте отдельно только нужные LLM-переменные.
По умолчанию ключи в Compose не передаются.

Для уже существующей БД:

```bash
export RAG_DATABASE_URL='postgresql://user:password@private-db-host:5432/database'
PYTHONPATH=backend .venv/bin/python -m app.rag.cli init-db
PYTHONPATH=backend .venv/bin/python -m app.rag.cli index-bank \
  --store postgres --input .dima-data/bank-corpus.json
PYTHONPATH=backend .venv/bin/python -m app.rag.cli index-news \
  --store postgres --input backend/app/agent/data/fixtures.json
PYTHONPATH=backend .venv/bin/python -m app.rag.cli search \
  --store postgres --query 'инфляция и кредит' --as-of 2026-10-23
```

`init-db` запускается один раз при развёртывании, не в каждом worker. У его роли должны
быть права на extension/tables. Managed-БД может требовать установку extension через
администратора; runtime-роль приложения не должна иметь права создания расширений.
Таблицы Игоря не изменяются. Новости векторизуются в наших таблицах; первичная запись
новости остаётся в его `News`.

Импорт bank считается снимком переданного корпуса: удалённые из снимка банковские
фрагменты исчезают из нашего индекса. Передавайте полный накопленный корпус.
Импорт news заменяет только новости с ID из переданного списка. Остальные сохраняются.
Импорт сериализован транзакционной advisory-блокировкой; при ошибке изменения откатываются.
Старые векторы не пересчитываются. Миграции/импорт запускает один job, не обе app-копии.
Для замены модели подготовьте новый индекс/новую БД параллельно и переключите обе
копии согласованно; автоматического удаления работающего индекса здесь нет.

Поиск объединяет cosine similarity и полнотекстовый поиск PostgreSQL (`russian` и
`simple`) через reciprocal rank fusion. Темы дают небольшой дополнительный вес.
Дата ограничивает источники сверху; агент дополнительно считает текущую позицию
банка применимой только в пределах 365 дней до новости. Это эвристика, не правило ТЗ;
исторические материалы могут быть полезны отдельно, но не как актуальный прогноз.
Порог cosine 0.65 — начальное значение, а не вероятность и не доказательство смысла.
Его нужно подобрать по размеченным примерам. Не более двух фрагментов одного отчёта
попадает в выдачу. Используется точный vector scan: для небольшого корпуса отдельный
HNSW пока не нужен. Фильтры дат/проверенности выполняются до ранжирования в БД.

## 10. Игорю: как подключить без изменения наших функций

Сигнатуры из [for_igor.md](for_igor.md) сохранены:

```python
from app.agent.recommendations import recommend_news
from app.agent.service import run_analysis, answer_question, analyze
```

`analyze` возвращает только result + trace. `run_analysis` возвращает объект с
`analysis`, `context`, `usage`. Для endpoint анализа предпочтителен `run_analysis`,
потому что `context` нужен следующему вопросу.

### Создание ресурсов

На старте каждого backend-процесса создайте один `LLMClient` и прогрейте `get_encoder`.
Загрузка модели и psycopg работают синхронно: в async lifespan используйте
`await asyncio.to_thread(...)`. Клиент LLM закройте при завершении процесса.
Не создавайте модель в каждом endpoint и не выполняйте OCR при старте backend.
Пример заготовки ресурсов (Игорь вставляет в свой lifespan):

```python
import asyncio
import os
from app.agent.llm_client import LLMClient
from app.rag.embeddings import get_encoder
from app.rag.postgres import PgVectorStore

llm = LLMClient()
encoder = await asyncio.to_thread(get_encoder)
store = PgVectorStore(os.environ['RAG_DATABASE_URL'], encoder)
# Сохранить llm/store в app.state. При shutdown: await llm.aclose().
```

### Адаптеры к существующим сервисам

```python
from app.rag.service_tools import HybridServiceTools

tools = await asyncio.to_thread(
    HybridServiceTools,
    store=store,
    llm=llm,
    company_id=company_id,
    profile_loader=load_company_profile,
    news_loader=load_news,
    news_lister=list_news,
    counterparties_loader=load_counterparties,
)
```

Имена `load_company_profile`, `load_news` и т.д. здесь — функции, которые предоставляет
Игорь, а не готовые импорты. Их формы:

```python
async def load_company_profile(company_id: str) -> dict: ...
async def load_news(news_id: str): ...  # ORM News или dict; None если нет записи
async def list_news(*, limit: int) -> list: ...
async def load_counterparties(company_id: str, *, direction: str, limit: int) -> dict: ...
```

Если его SQLAlchemy-код синхронный, оберните работу в `asyncio.to_thread` и создавайте
собственную короткоживущую Session внутри функции. Не передавайте одну Session
одновременно нескольким инструментам. Не вызывайте собственный FastAPI через HTTP.
Профиль должен содержать `company_id`, `features`, отрасль и ограничения данных.
Обезличенный ID не позволяет искать настоящее юрлицо во внешнем интернете.

`HybridServiceTools` сохраняет read-only ограничения компании, использует pgvector
для банковского поиска и связанных новостей. Для ленты добавляет семантических
кандидатов к последним новостям; окончательные баллы и причины остаются объяснимыми
правилами `rank_news`. Векторная похожесть сама по себе не доказывает влияние на компанию.

### Вызовы внутри ручек

```python
feed = await recommend_news(company_id, tools, limit=10)
run = await run_analysis(company_id, str(news_id), tools)
# Сохранить run.analysis, run.context, run.usage, company_id, news_id и insight_id.
# Наружу вернуть InsightResponse с ID, статусом completed и **run.analysis.
# run.context не отдавать frontend.

saved_for_chat = {
    'company_id': saved.company_id,
    'news_id': str(saved.news_id),
    'result': saved.analysis['result'],
    'context': saved.context,
}
reply = await answer_question(saved_for_chat, question, tools)
# Trace: {'items': saved.analysis['agent_trace']}
```

`source_url` для непубличного PDF может быть null. Страница и document_id сохраняются;
ссылку просмотра закрытого оригинала backend должен выдавать с проверкой прав.
Для HTTP: NotFound → 404, AmbiguousProfile → 409, InvalidInput → 422,
AgentUnavailable → 503. EvidenceError означает, что проверенный результат не получен:
нельзя возвращать completed; согласуйте с frontend явную ошибку анализа.
Не отправляйте клиенту произвольные сырые исключения/ответы провайдера.

### Индексация новых новостей

После фиксации новости в БД запустите job с текущими сохранёнными записями:

```python
from app.rag.index import news_records
rows = await asyncio.to_thread(news_records, changed_news_records, encoder)
await asyncio.to_thread(store.upsert, rows, kind='news',
                        parent_ids={str(n.id) for n in changed_news_records})
```

Здесь `changed_news_records` — ORM-объекты с уже загруженными полями или dict.
В dict-варианте соберите IDs через `n['id']`. Сначала commit основной новости,
затем индексирование: не удерживайте её ORM-транзакцию на время работы модели.
При ошибке job повторяется явно; fingerprint не позволяет пересчитать неизменный текст.
Можно начать с периодического CLI-импорта JSON, затем добавить job в RSS-процесс.

## 11. Две копии приложения на VK Cloud

Рекомендуемый минимальный вариант: балансировщик → два backend → общая PostgreSQL.
На каждом backend один web-worker и одна E5-модель. В сумме две копии модели,
а не одна на весь кластер. Это позволяет выполнять поиск при отказе одной app-копии.
Вторая копия модели не удваивает стоимость API LLM сама по себе.

Нужно предусмотреть:

- общий PostgreSQL для insights, context, chat, кеша и состояния расходов;
- одинаковые commit кода, модель/ревизию и настройки на обеих копиях;
- подготовленный локальный кеш модели на каждой VM; после прогрева local-only;
- общие PDF/изображения в защищённом object storage либо одинаковых копиях;
- один активный importer и ограниченную параллельность тяжёлого OCR;
- health/readiness checks, которые проверяют не только HTTP, но и нужные ресурсы;
- backup БД с проверенным восстановлением и постоянный диск;
- общий контроль бюджета LLM и защиту от одинакового анализа на двух репликах.

Наши advisory locks защищают **индексацию**. Они не реализуют общий кеш insights,
дедупликацию платных анализов или дневной рублёвый лимит. Это интеграционные задачи
в persistence-слое Игоря. Не обещайте отказоустойчивость всей системы при одной БД
на одной из двух VM: отказ её узла остановит оба приложения.
Начальный ориентир 2–4 vCPU / 4–8 GB RAM на узел — только оценка до нагрузочного теста.
OCR лучше выполнять заранее или отдельно от пользовательских запросов.

## 12. Проверка удалённой LLM

```bash
PYTHONPATH=backend .venv/bin/python -m app.agent.live_check --live
```

Команда расходует общий лимит организаторов. Она использует синтетическую компанию
и новость, настоящий сверенный банковский фрагмент, максимум два LLM-запроса.
Результат/usage: `.dima-data/live-report.json`. При ошибке нет тихого перехода на mock.
Эта команда проверяет текстовый анализ, не мультимодальный endpoint. VLM проверяется
отдельной командой и тоже расходует квоту. Не запускайте проверки в цикле.
По ТЗ общий лимит 500 ₽/сутки; без тарифов провайдера usage не переводится точно в рубли.

## 13. Что пока не закрыто этой реализацией

- В приложении Игоря ещё нужно подключить новые HTTP-ручки и сервисные адаптеры.
- Нужен импорт и ручная сверка полного банковского корпуса. Один готовый абзац не
  является полной базой знаний. Исторический отчёт за 2021 год нельзя выдавать за
  текущую позицию 2026 года.
- Не реализованы общий кеш аналитики, checkpoint-resume, автоматический failover БД,
  общий дневной бюджет и scheduler новостей. Архитектура их допускает.
- Векторная близость и детерминированный verifier не гарантируют экономическую истину.
  Нужна оценка Recall@k/nDCG на размеченных запросах, качества OCR чисел/таблиц,
  корректности цитат и полезности интерпретаций на двух разных компаниях.
- Docker Compose этой части не является готовым деплоем всего сайта. Реальные
  ресурсы VK Cloud, права managed-БД, балансировщик и стоимость проверяются отдельно.

## 14. Первичные документы по используемым технологиям

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Multilingual E5 small: префиксы и ограничение длины](https://huggingface.co/intfloat/multilingual-e5-small)
- [SentenceTransformer](https://sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html)
- [pgvector Python](https://github.com/pgvector/pgvector-python)
- [PyMuPDF OCR](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html)
- [Tesseract installation](https://tesseract-ocr.github.io/tessdoc/Installation.html)

## 15. Результаты проверки этой версии

- Офлайн-агент: 30 тестов; в том числе параллельные разные компании и ветки повторов.
- RAG: 8 тестов без сети и отдельный тест на настоящем PostgreSQL 16 с pgvector.
- Лексические примеры: 10/10, только для небольшого исходного корпуса.
- Tesseract: проверена первая сканированная страница отчёта о ставке, около 6000
  извлечённых символов, 6 фрагментов; они остаются unreviewed. Полные 64 страницы
  этой проверкой не покрыты.
- Сделано два платных обращения к DeepSeek: первый план обрезан лимитом 500,
  второй — настоящий анализ с фиксированным офлайн-планом, прошедший verifier.
  Это подтверждает реальный анализ, но не полный прогон с реальным LLM-планированием.
  Бюджет планирования увеличен до 2000 токенов; новый платный прогон не делался.
  Отчёты: `.dima-data/live-report.json`, `.dima-data/live-analysis-report.json`.
- Удалённый VLM endpoint не вызывался: адаптер и кеш проверены на тестовом клиенте.
- Compose проходит проверку конфигурации; полный Docker-образ приложения и VK Cloud
  в этой локальной проверке не разворачивались.

Дополнительно проверена настоящая E5 на CPU: русский и английский запросы находят
банковский фрагмент, длинный текст разбивается без потери хвоста. Создан локальный
JSON-индекс с реальными векторами и пройден сквозной сценарий E5 → PostgreSQL/pgvector
→ HybridServiceTools → LangGraph с явно офлайн-LLM. Отчёты: `.dima-data/embedding-report.json`
и `.dima-data/postgres-e5-report.json`. Веса и рабочие данные исключены из Git через
локальный `.dima-data/.gitignore`.
