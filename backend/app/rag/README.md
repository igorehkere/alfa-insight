# Поиск банковской аналитики и подготовка PDF

Актуальное подключение: [инструкция Игорю](../agent/IGOR_INTEGRATION.md).
Основной режим по IMPLEMENTATION_PLAN — `RAG_SEARCH_MODE=fts`: PostgreSQL
Full-Text Search с GIN-индексами `russian`/`simple`, без pgvector и E5.
Подготовка: `python -m app.rag.cli init-db --store fts`, затем
`python -m app.rag.cli index-bank --store fts --input backend/app/rag/data/reviewed_corpus.json`.
CLI читает корневой `.env`, окружение имеет приоритет. Импорт выполняется явно.
FTS хранит только свои `dima_rag_fts_*` таблицы. Повторный импорт не создаёт дублей.
Индекс банковских материалов заменяется целиком, новости остаются.

Проверенный корпус: [8 документов, 46 выдержек](data/REVIEWED_CORPUS.md).
`bank_corpus.json` остаётся маленькой тестовой фикстурой.
`fts.py` и `records.py` не загружают embedding-модель или NumPy.

## Дополнительный гибридный режим

Прежняя интеграция и Docker с OCR: [Integration_to_igor.md](../../../Integration_to_igor.md).
Для этого варианта теперь явно задайте `RAG_SEARCH_MODE=hybrid`.
Рабочий образ — `backend/Dockerfile.prototype`, Compose — `compose.prototype.yml`.
Тематический фильтр теперь находится в общем `hybrid.py`, поиск свежих SQL-новостей
и пропуск удалённых записей — в `service_tools.py`. Отладочные адаптеры не нужны.
`RAG_LOCAL_FILES_ONLY` по умолчанию 1; сначала явно выполните `cli download-model`.
OCR можно проверить командой `python -m app.rag.doctor` без LLM-вызова.

Общие Python-зависимости: `backend/requirements.txt`. Tesseract и rus/eng ставятся
системно или через `backend/Dockerfile.dima`. Отдельного rag requirements больше нет.

Начните с [подробной инструкции](../EXPLANATION.md): там есть команды OCR, VLM,
проверки страниц, загрузки E5, JSON-индекса, pgvector и интеграции с backend Игоря.

- `ingest.py`, `vlm.py`, `review.py`: подготовка и подтверждение корпуса.
- `embeddings.py`: одна CPU E5 на процесс, префиксы query/passage, нормализация.
- `index.py`, `json_store.py`, `postgres.py`, `hybrid.py`: инкрементальная индексация и поиск.
- `cli.py`: явные команды, без фоновой индексации при запуске сервера.
- `service_tools.py`: адаптер для существующих функций агента.
- `search.py`: прежний небольшой лексический поиск для бесплатной офлайн-проверки.
- `data/bank_corpus.json`: один сверенный абзац для регрессионных тестов.
- `data/reviewed_corpus.json`: рабочая проверенная выборка из восьми документов.
