from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connect import get_session
from app.models import News

router = APIRouter(
    prefix="/api/news",
    tags=["News"],
)


@router.get("/catalog")
def get_news(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    stmt = (select(News).order_by(
            News.published_at.desc().nulls_last(),
            News.id.desc(),
        ).limit(limit).offset(offset))

    news = session.scalars(stmt).all()

    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "source_name": item.source_name,
                "source_url": item.source_url,
                "published_at": item.published_at,
                "category": item.category,
            }
            for item in news
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{news_id}")
def get_news_by_id(
    news_id: int,
    session: Session = Depends(get_session),
):
    news = session.get(News, news_id)

    if news is None:
        raise HTTPException(
            status_code=404,
            detail="Новость не найдена",
        )

    return {
        "id": news.id,
        "title": news.title,
        "description": news.description,
        "content": news.content,
        "source_name": news.source_name,
        "source_url": news.source_url,
        "published_at": news.published_at,
        "category": news.category,
    }
