"""Drop-in service adapter: keep Igor's public calls and existing service loaders."""
import asyncio
from datetime import date

from app.agent.tools import ServiceTools


class HybridServiceTools(ServiceTools):
    def __init__(self, *, store, **kwargs):
        self.store = store
        # Construct once per analysis so cache version reflects the imported corpus.
        async def bank_searcher(query, *, limit, as_of):
            return await asyncio.to_thread(store.search, query, limit, as_of=as_of, kind="bank")
        super().__init__(bank_searcher=bank_searcher, corpus_version=store.version, **kwargs)

    async def list_news(self, limit=200):
        # Hybrid candidates can include older relevant articles outside the latest RSS batch.
        # Final personal scores/reasons still come from our explainable rank_news helper.
        recent = await super().list_news(limit)
        profile = await self.get_company_profile(self.company_id)
        features = profile.get("features") or {}
        parts = [profile.get("industry") or features.get("industry_name") or "",
                 features.get("region") or profile.get("region") or "",
                 " ".join(features.get("customs_countries") or [])]
        if (features.get("bank_products") or {}).get("loan"):
            parts.append("кредит ключевая ставка")
        query = " ".join(str(p) for p in parts if p).strip()[:500]
        if not query:
            return recent
        rows = await asyncio.to_thread(self.store.search, query, 10, kind="news", as_of=date.today())
        candidates = [await self.get_news(row["news_id"]) for row in rows]
        seen, result = set(), []
        for item in candidates + recent:
            if item["id"] not in seen:
                seen.add(item["id"])
                result.append(item)
        return result[:limit]

    async def search_related_news(self, query, limit=3):
        rows = await asyncio.to_thread(self.store.search, query, limit, kind="news", as_of=date.today())
        # Reload primary records: search excerpts are not substituted for article bodies.
        return [await self.get_news(row["news_id"]) for row in rows]
