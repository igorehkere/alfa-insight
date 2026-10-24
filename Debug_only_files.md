# Файлы только для отладки и проверок

Пути указаны относительно корня `alfa-insight`. Каждый перечисленный файл **не нужен для запуска финального проекта** и используется как отладочный, диагностический или проверочный. Это не означает, что регрессионные тесты бесполезны для качества: их стоит хранить в репозитории/CI. Удаление файлов сейчас не выполнялось.

Финальный backend: `app.agent.api:create_app`, `app.agent`, `app.rag`, `compose.prototype.yml`, `backend/Dockerfile.prototype`. Он не импортирует `app.test`; Docker исключает `app/test` и тестовые каталоги. Старый локальный интерфейс на 8001 пока зависит от перечисленного стенда.

| Путь | Для чего оставлен |
|---|---|
| `backend/app/test/.env` | Локальные настройки отладки. Может содержать секреты; в git и финальный образ не включать. |
| `backend/app/test/.env.example` | Служебный файл изолированного стенда; не является зависимостью финального backend. |
| `backend/app/test/.gitignore` | Служебный файл изолированного стенда; не является зависимостью финального backend. |
| `backend/app/test/QA_REPORT.md` | Инструкция или исторический отчёт об отладочном стенде. |
| `backend/app/test/README.md` | Инструкция или исторический отчёт об отладочном стенде. |
| `backend/app/test/__init__.py` | Служебный файл изолированного стенда; не является зависимостью финального backend. |
| `backend/app/test/ch.sh` | Запуск старого отладочного приложения. |
| `backend/app/test/chat_service.py` | Совместимый импорт рабочего чата app.agent.chat/service. |
| `backend/app/test/check_reported_cases.py` | Платное воспроизведение двух пользовательских ошибок по старым ID стенда. |
| `backend/app/test/compose.yml` | Отдельный PostgreSQL стенда. Финальный compose.prototype.yml его не использует. |
| `backend/app/test/config.py` | Настройки стенда, порт 55432 и тестовые пути. Не импортировать в финальном backend. |
| `backend/app/test/diagnose_agent.py` | Платный диагностический захват отдельного запроса агента. |
| `backend/app/test/launch.py` | Управление процессом и тестовым Docker Compose. |
| `backend/app/test/llm_client.py` | Совместимый импорт рабочего app.agent.llm_client; своей рабочей логики больше нет. |
| `backend/app/test/llm_endpoints.py` | Старый отладочный FastAPI, Swagger и HTML-стенд. Финальная фабрика — app.agent.api:create_app. |
| `backend/app/test/llm_protocol.py` | Совместимый импорт рабочего app.agent.llm_protocol. |
| `backend/app/test/prepare.py` | Создание и наполнение отдельной тестовой БД, учебные новости и небольшая выборка компаний. |
| `backend/app/test/probe_llm.py` | Платные короткие проверки двух LLM URL; никогда не запускать автоматически в production. |
| `backend/app/test/rag_store.py` | Совместимый импорт; фильтр теперь в app.rag.hybrid. |
| `backend/app/test/repository.py` | Отдельное хранилище test_llm_* для прежних отладочных анализов; финальная версия — app.agent.persistence. |
| `backend/app/test/requirements.txt` | Зависимости прежнего стенда; финальная установка использует backend/requirements.txt. |
| `backend/app/test/smoke.py` | Ручная HTTP-проверка отладочного стенда; --live расходует квоту. |
| `backend/app/test/tests/test_chat_context.py` | Старый entrypoint тестов: сами проверки перенесены в app.agent.tests.test_chat_context. |
| `backend/app/test/tests/test_endpoints.py` | Проверки старого отладочного HTTP-сервера с тестовой БД. |
| `backend/app/test/tests/test_llm_failover.py` | Старый entrypoint тестов: сами проверки перенесены в app.agent.tests.test_llm_failover. |
| `backend/app/test/tests/test_prototype_api.py` | Регрессионные проверки ФИНАЛЬНОЙ фабрики, PostgreSQL и всех компаний стенда; полезны для QA, но не для запуска. |
| `tests/.gitignore` | Служебный файл изолированного стенда; не является зависимостью финального backend. |
| `tests/QA.md` | Инструкция или исторический отчёт об отладочном стенде. |
| `tests/README.md` | Инструкция или исторический отчёт об отладочном стенде. |
| `tests/frontend/app.js` | Демонстрационный HTML/CSS/JavaScript-интерфейс; основной frontend Игоря его не требует. |
| `tests/frontend/index.html` | Демонстрационный HTML/CSS/JavaScript-интерфейс; основной frontend Игоря его не требует. |
| `tests/frontend/styles.css` | Демонстрационный HTML/CSS/JavaScript-интерфейс; основной frontend Игоря его не требует. |
| `tests/start.sh` | Сокращённый запуск старого стенда на 8001. |
| `backend/app/test/.data/` | Логи/JSON-отчёты, OCR-пробы, PID процесса и старые результаты; не runtime-данные финального проекта. |
| `tests/.artifacts/` | Скриншоты браузерного QA. |
| `backend/app/test/__pycache__/`, `backend/app/test/tests/__pycache__/` | Сгенерированный Python-кеш. |
| `backend/app/agent/main.py`, `backend/app/agent/local.py`, `backend/app/agent/offline.py` | Офлайн-демонстрация и тестовая LLM. Финальный API их не запускает. |
| `backend/app/agent/live_check.py` | Ручная платная проверка LLM; не startup hook. |
| `backend/app/agent/tests/`, `backend/app/rag/tests/` | Рабочие регрессионные тесты; сохранить для QA/CI, в runtime не нужны. |
| `backend/app/rag/evaluate.py`, `backend/app/rag/data/search_cases.json` | Оценка качества поиска на контрольных примерах. |
| `compose.dima.yml`, `backend/Dockerfile.dima` | Прежний отдельный RAG CLI-стенд; готовый backend использует compose.prototype.yml. |

Не удалять как «тестовые»: `app.agent.llm_protocol`, `app.agent.chat`, `app.agent.runtime`, `app.agent.api`, `app.agent.persistence`, `app.rag.hybrid`, `app.rag.service_tools`, `app.rag.ingest`, `app.rag.review`, `app.rag.cli`, `app.rag.doctor`. Это рабочая логика или необходимые операции подготовки документов и индекса. `backend/app/rag/data/bank_corpus.json` содержит сверенный стартовый фрагмент и может использоваться при подготовке индекса.
