export interface News {
  id: number;
  title: string;
  description: string | null;
  source_name: string;
  source_url: string;
  published_at: string | null;
  category: string | null;
}

export interface NewsResponse {
  items: News[];
  limit: number;
  offset: number;
}

export const getNews = async (): Promise<NewsResponse> => {
  const response = await fetch('/api/news/catalog?limit=20');

  if (!response.ok) {
    throw new Error('Не удалось загрузить новости');
  }

  return response.json();
};