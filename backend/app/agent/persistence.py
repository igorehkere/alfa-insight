"""Default prototype repository. Igor can inject another repository with the same methods."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.connect import SessionLocal
from app.models import Company, News
from app.main import company_profile, counterparties
from app.agent.errors import InvalidInput, NotFound
from app.agent.tools import news_from_record


class PersistenceBase(DeclarativeBase):
    pass


class SavedInsight(PersistenceBase):
    __tablename__ = "insights"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    cache_key: Mapped[str] = mapped_column(Text, index=True)
    company_id: Mapped[str] = mapped_column(Text, index=True)
    news_id: Mapped[str] = mapped_column(Text)
    public: Mapped[dict] = mapped_column(JSONB)
    context: Mapped[dict] = mapped_column(JSONB)
    usage: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedMessage(PersistenceBase):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def serialize_news(row):
    item = news_from_record(row)
    if row.source_name in {"manual_submission", "synthetic_fixture"}:
        item["provenance"] = row.source_name
    if row.source_url.startswith("https://test.invalid/"):
        item["source_url"] = None
    return item


def profile(company_id):
    with SessionLocal() as session:
        # Reuse Igor's feature extraction, ambiguity handling and payment summary.
        result = company_profile(company_id, session=session)
        return result


def payment_links(company_id, *, direction="outgoing", limit=10):
    with SessionLocal() as session:
        return counterparties(company_id, direction=direction, limit=limit, offset=0, session=session)


def load_news(news_id):
    if not str(news_id).isascii() or not str(news_id).isdigit() or not 0 < int(news_id) <= 2**63 - 1:
        raise InvalidInput("news_id — положительный числовой ID из /api/news/catalog")
    with SessionLocal() as session:
        row = session.get(News, int(news_id))
        return serialize_news(row) if row else None


def list_news(*, limit=200, offset=0):
    with SessionLocal() as session:
        stmt = select(News).order_by(News.published_at.desc().nulls_last(), News.id.desc()).limit(limit).offset(offset)
        return [serialize_news(row) for row in session.scalars(stmt)]


def add_news(request):
    source_url = str(request.source_url) if request.source_url else None
    with SessionLocal.begin() as session:
        # Original URL is metadata, never fetched. Internal UUID prevents URL uniqueness collisions.
        row = News(title=request.title, content=request.body, description=None,
                   source_url=source_url or f"https://test.invalid/manual/{uuid4().hex}",
                   source_name="manual_submission", published_at=request.published_at)
        if source_url:
            old = session.scalar(select(News).where(News.source_url == source_url))
            if old:
                from fastapi import HTTPException
                raise HTTPException(409, "Новость с таким URL уже существует")
        session.add(row)
        session.flush()
        return serialize_news(row)


def cache_key(profile_data, news, corpus_version, model_identity, prompt_version):
    payload = {"profile": profile_data, "news": news, "corpus": corpus_version,
               "model": model_identity, "prompt": prompt_version}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def cached(key):
    with SessionLocal() as session:
        row = session.scalar(select(SavedInsight).where(SavedInsight.cache_key == key)
                             .order_by(SavedInsight.created_at.desc(), SavedInsight.id.desc()).limit(1))
        return row.public if row else None


def save_run(key, company_id, news_id, run):
    from app.agent.schema import InsightResponse
    identifier = "insight-" + uuid4().hex
    public = InsightResponse(insight_id=identifier, company_id=company_id,
                             news_id=news_id, **run.analysis).model_dump(mode="json")
    with SessionLocal.begin() as session:
        session.add(SavedInsight(id=identifier, cache_key=key, company_id=company_id,
                                 news_id=news_id, public=public, context=run.context, usage=run.usage))
    return public


def load_insight(identifier):
    with SessionLocal() as session:
        row = session.get(SavedInsight, identifier)
        if row is None:
            raise NotFound("Анализ не найден")
        return {**row.public, "context": row.context}


def save_answer(identifier, question, response):
    with SessionLocal.begin() as session:
        session.add(SavedMessage(insight_id=identifier, question=question, response=response))


def messages(identifier):
    load_insight(identifier)
    with SessionLocal() as session:
        return [{"question": row.question, "response": row.response}
                for row in session.scalars(select(SavedMessage).where(SavedMessage.insight_id == identifier)
                                          .order_by(SavedMessage.id))]


def chat_history(identifier):
    """Only the last two turns enter model memory; keep total for cycle resets."""
    with SessionLocal() as session:
        query = select(SavedMessage).where(SavedMessage.insight_id == identifier)
        total = session.scalar(select(func.count()).select_from(SavedMessage).where(SavedMessage.insight_id == identifier))
        rows = list(session.scalars(query.order_by(SavedMessage.id.desc()).limit(2)))
        return {"turn_count": total, "items": [{"question": row.question, "response": row.response} for row in reversed(rows)]}


def counts():
    with SessionLocal() as session:
        return {name: session.scalar(select(func.count()).select_from(model))
                for name, model in [("companies", Company), ("news", News), ("insights", SavedInsight)]}


def initialize():
    """Explicit prototype migration; never called on web startup."""
    from app.connect import engine
    News.__table__.create(engine, checkfirst=True)
    PersistenceBase.metadata.create_all(engine)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Initialize only insights, chat_messages and Igor's news_items")
    parser.add_argument("command", choices=["init-db"])
    parser.parse_args()
    initialize()
    print("news_items, insights, chat_messages готовы; корпоративные таблицы не менялись")
