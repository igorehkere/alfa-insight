"""Сохранение RSS-новостей через SQLAlchemy ORM и PostgreSQL UPSERT."""
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import News
from app.news.parser import ParsedNews


def save_news(session: Session, news: list[ParsedNews]) -> int:
    """Возвращает число новых строк. Транзакцией управляет вызывающий код."""
    inserted = 0
    for item in news:
        stmt = (
            insert(News)
            .values(
                title=item.title,
                description=item.description,
                content=item.content,
                source_url=item.source_url,
                source_name=item.source_name,
                published_at=item.published_at,
                category=None,  # классифицируем позже
            )
            .on_conflict_do_nothing(index_elements=[News.source_url])
        )
        result = session.execute(stmt)
        inserted += result.rowcount or 0
    return inserted