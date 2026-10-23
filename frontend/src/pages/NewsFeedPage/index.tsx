import { useEffect, useState } from 'react';
import { getNews, type News } from '../../api/news';

export const NewsFeedPage = () => {
  const [news, setNews] = useState<News[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getNews()
      .then((data) => setNews(data.items))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <p>Загрузка новостей...</p>;
  }

  return (
    <div>
      {news.map((item) => (
        <div key={item.id}>
          <h3>{item.title}</h3>
          <p>{item.description ?? 'Описание отсутствует'}</p>
        </div>
      ))}
    </div>
  );
};