export interface News {
  id: number;
  title: string;
  description: string | null;
  source_name: string;
  source_url: string;
  published_at: string | null;
  category: string | null;
}

export interface NewsDetails extends News {
  content: string | null;
}

export interface NewsResponse {
  items: News[];
  limit: number;
  offset: number;
}

export const getNews = async (signal?: AbortSignal): Promise<NewsResponse> => {
  const response = await fetch('/api/news/catalog?limit=20', {
    signal,
  });

  if (!response.ok) {
    throw new Error('Не удалось загрузить новости');
  }

  return response.json();
};

export const getNewsById = async (newsId: number, signal?: AbortSignal): Promise<NewsDetails> => {
  const response = await fetch(`/api/news/${newsId}`, {
    signal,
  });

  if (!response.ok) {
    throw new Error('Не удалось загрузить подробности новости');
  }

  return response.json();
};
