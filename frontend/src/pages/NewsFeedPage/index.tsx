import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AnimatePresence, motion } from 'motion/react';
import { getNews, type News } from '../../api/news';
import { NewsDetailsPage } from '../NewsDetailsPage';
import css from './index.module.scss';
import { Loader } from '../../components/Loader';

export const NewsFeedPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const companyId = searchParams.get('company');
  const rawNewsId = searchParams.get('news');

  const parsedNewsId = Number(rawNewsId);

  const selectedNewsId = rawNewsId !== null && Number.isSafeInteger(parsedNewsId) && parsedNewsId > 0 ? parsedNewsId : null;

  const [news, setNews] = useState<News[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const fetchNews = async () => {
      try {
        const data = await getNews(controller.signal);

        setNews(data.items);
      } catch {
        if (!controller.signal.aborted) {
          setError('Не удалось загрузить новости');
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    };
    fetchNews();

    return () => controller.abort();
  }, []);

  const handleSelectNews = (newsId: number) => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.set('news', String(newsId));

      return params;
    });
  };

  const handleCloseNews = () => {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.delete('news');

      return params;
    });
  };

  return (
    <main className={`${css.page} ${selectedNewsId !== null ? css.withDetails : ''}`}>
      <motion.section
        layout
        className={css.feed}
        transition={{
          layout: {
            duration: 0.45,
            ease: [0.22, 1, 0.36, 1],
          },
        }}
      >
        <h1>Персональная аналитика событий</h1>
        {companyId && <p className={css.company}>Выбранная компания: {companyId}</p>}
        <h2 className={css.subtitle}>Новости</h2>
        {loading && <Loader type='page'/>}
        {error && <p>{error}</p>}
        {!loading && !error && news.length === 0 && <p>Новостей пока нет.</p>}
        <div className={css.newsList}>
          {news.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`${css.newsItem} ${selectedNewsId === item.id ? css.active : ''}`}
              onClick={() => handleSelectNews(item.id)}
              aria-pressed={selectedNewsId === item.id}
            >
              <h3>{item.title}</h3>

              <p className={css.description}>
                {item.description
                  ? `${item.description.slice(0, 180)}${item.description.length > 180 ? '...' : ''}`
                  : 'Описание отсутствует'}
              </p>

              <div className={css.newsMeta}>
                <span>{item.source_name}</span>

                {item.published_at && <span>{new Date(item.published_at).toLocaleDateString('ru-RU')}</span>}
              </div>
            </button>
          ))}
        </div>
      </motion.section>
      <AnimatePresence>
        {selectedNewsId !== null && (
          <motion.aside
            key="news-details"
            className={css.detailsPanel}
            initial={{
              x: '100%',
              opacity: 0,
            }}
            animate={{
              x: 0,
              opacity: 1,
            }}
            exit={{
              x: '100%',
              opacity: 0,
            }}
            transition={{
              duration: 0.4,
              ease: [0.22, 1, 0.36, 1],
            }}
            aria-label="Подробности новости"
          >
            <NewsDetailsPage key={selectedNewsId} newsId={selectedNewsId} onClose={handleCloseNews} />
          </motion.aside>
        )}
      </AnimatePresence>
    </main>
  );
};
