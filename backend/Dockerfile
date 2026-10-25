FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/backend \
    RAG_SEARCH_MODE=fts \
    RAG_LOCAL_FILES_ONLY=1 \
    RAG_MODEL_CACHE=/models \
    RAG_CPU_THREADS=2 \
    TOKENIZERS_PARALLELISM=false \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng libgomp1 libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY backend/requirements.txt /srv/backend/requirements.txt
RUN pip install --no-cache-dir 'torch==2.14.1' --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app /srv/backend/app
COPY backend/scripts /srv/backend/scripts
COPY backend/sql /srv/backend/sql
COPY data/raw /srv/data/raw

RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /models && chown appuser:appuser /models
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.agent.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
