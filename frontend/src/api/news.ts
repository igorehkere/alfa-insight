import type { CompanyNewsFeed, NewsCard, NewsDetails as ApiNewsDetails } from './agent-types';

export interface News {
  id: string;
  title: string;
  description: string | null;
  source_name: string;
  source_url: string | null;
  published_at: string | null;
  category: string | null;

  relevance_score?: number;
  relevance_reasons?: NewsCard['relevance_reasons'];
}

export interface NewsDetails extends News {
  content: string | null;
  body_is_summary?: boolean;
}

export interface NewsResponse {
  items: News[];
}

function mapNewsCard(card: NewsCard): News {
  return {
    id: card.id,
    title: card.title,

    // У персональной карточки нет описания статьи.
    description: null,

    source_name: card.source,
    source_url: card.source_url,
    published_at: card.published_at,

    category: card.topic_tags[0] ?? null,

    relevance_score: card.relevance_score,
    relevance_reasons: card.relevance_reasons,
  };
}

function mapNewsDetails(data: ApiNewsDetails): NewsDetails {
  return {
    id: data.id,
    title: data.title,
    description: null,
    content: data.body,
    source_name: data.source,
    source_url: data.source_url,
    published_at: data.published_at,
    category: data.topic_tags[0] ?? null,
    body_is_summary: data.body_is_summary,
  };
}

export const getNews = async (companyId: string, signal?: AbortSignal): Promise<NewsResponse> => {
  const params = new URLSearchParams({
    company_id: companyId,
    limit: '20',
  });
  const response = await fetch(`/api/news?${params.toString()}`, { signal });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить новости: HTTP ${response.status}`);
  }
  const data: CompanyNewsFeed = await response.json();

  return {
    items: data.items.map(mapNewsCard),
  };
};

export const getNewsById = async (newsId: string, signal?: AbortSignal): Promise<NewsDetails> => {
  const response = await fetch(`/api/news/${encodeURIComponent(newsId)}`, { signal });
  if (!response.ok) {
    throw new Error(`Не удалось загрузить новость: HTTP ${response.status}`);
  }
  const data: ApiNewsDetails = await response.json();

  return mapNewsDetails(data);
};
