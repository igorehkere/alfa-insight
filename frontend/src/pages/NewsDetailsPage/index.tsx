import { useEffect, useState } from 'react';

import { getNewsById, type NewsDetails } from '../../api/news';

interface NewsDetailsPageProps {
  newsId: number;
  onClose: () => void;
}

export const NewsDetailsPage = ({ newsId, onClose }: NewsDetailsPageProps) => {
  const [news, setNews] = useState<NewsDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const fetchNewsDetails = async () => {
      setLoading(true);
      setError('');
      setNews(null);
      try {
        const data = await getNewsById(newsId, controller.signal);
        setNews(data);
      } catch {
        if (!controller.signal.aborted) {
          setError('Не удалось загрузить новость');
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    };
    fetchNewsDetails();

    return () => controller.abort();
  }, [newsId]);

  return (
    <div>
      <button
        type="button"
        onClick={onClose}
        style={{
          marginBottom: '24px',
          padding: '8px 16px',
          cursor: 'pointer',
        }}
      >
        Закрыть
      </button>
      {loading && <p>Загрузка новости...</p>}
      {error && <p>{error}</p>}
      {!loading && !error && news && (
        <article>
          <h2
            style={{
              fontSize: '24px',
              marginBottom: '16px',
            }}
          >
            {news.title}
          </h2>
          <p style={{ marginBottom: '8px' }}>
            <strong>Источник:</strong> {news.source_name}
          </p>
          {news.published_at && (
            <p style={{ marginBottom: '20px' }}>
              <strong>Дата:</strong> {new Date(news.published_at).toLocaleString('ru-RU')}
            </p>
          )}
          <hr style={{ marginBottom: '20px' }} />
          <div
            style={{
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              marginBottom: '24px',
              overflowWrap: 'anywhere',
            }}
          >
            {news.content?.trim() || news.description?.trim() || 'Текст новости пока отсутствует.'}
          </div>
          <a href={news.source_url} target="_blank" rel="noopener noreferrer">
            Читать в источнике
          </a>
        </article>
      )}
    </div>
  );
};
