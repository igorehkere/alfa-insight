import { useEffect, useState } from 'react';
import { getNewsById, type NewsDetails as NewsDetailsData } from '../../api/news';
import { previewStatusLabel, type PreviewStatus } from '../NewsFeed';
import css from './index.module.scss';
import { Loader } from '../Loader';

interface NewsDetailsProps {
  newsId: number;
  onClose: () => void;
  previewStatus?: PreviewStatus;
}

function formatDateTime(value: string | null): string {
  if (!value) return 'Дата не указана';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

// Убрать после появления агента
const DEMO_RISKS = [
  'Увеличение стоимости обслуживания кредитов',
  'Рост финансовых расходов',
  'Возможная корректировка инвестиционных планов',
];
const DEMO_OPPORTUNITIES = [
  'Пересмотр условий финансирования',
  'Рост финансовых расходов',
  'Оптимизация структуры обязательств',
];

export function NewsDetails({ newsId }: NewsDetailsProps) {
  const [news, setNews] = useState<NewsDetailsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setNews(null);
    setIsLoading(true);
    setError(null);

    getNewsById(newsId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setNews(data);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : 'Не удалось загрузить новость');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [newsId]);

  return (
    <section className={css.details}>
      {isLoading && <Loader type='page'/>}
      {error && (
        <div className={css.error} role="alert">
          {error}
        </div>
      )}

      {!isLoading && !error && news && (
        <>
          <article className={`${css.card} ${css.mainNews}`}>
            <div className={css.newsTop}>
              <span className={css.source}>{news.source_name}</span>
              <time className={css.date} dateTime={news.published_at ?? undefined}>
                {formatDateTime(news.published_at)}
              </time>
            </div>
            <h2 className={css.newsTitle}>
              <span className={css.redDot} aria-hidden="true" />
              {news.title}
            </h2>
            <p className={css.bodyText}>
              {news.content?.trim() ||
                news.description?.trim() ||
                'Текст новости пока отсутствует в базе. Полная публикация доступна по ссылке на первоисточник.'}
            </p>
            <a href={news.source_url} className={css.sourceButton} target="_blank" rel="noopener noreferrer">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
                <path
                  d="M13 5h6v6m0-6-9 9M19 13v6H5V5h6"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Перейти к первоисточнику
            </a>
          </article>
          <section className={css.card}>
            <h3 className={css.sectionTitle}>Почему это важно для вашей компании?</h3>


            {/* // Изменить после появления агента */}
            <p className={css.bodyText}> 
              ПАО «Магнит» работает в сфере розничной торговли и использует кредитные продукты для финансирования
              оборотного капитала и инвестиционных проектов. Повышение ключевой ставки напрямую увеличит стоимость
              обслуживания кредитного портфеля и может повлиять на ваши инвестиционные планы.
            </p>
            <div className={css.contextBadges}>
              <span><img src='/images/industry_icon.svg'/> <b>Отрасль:</b> Розничная торговля</span>
              <span><img src='/images/product_icon.svg'/> <b>Продукты:</b> Кредит, РКО</span>
              <span><img src='/images/backpack_icon.svg'/> <b>Кредитный портфель:</b> 2,8 млрд ₽</span>
            </div>
          </section>

          <div className={css.twoColumns}>
            <section className={css.card}>
              <h3 className={css.sectionTitle}>Риски</h3>
              <ol className={css.numberedList}>
                {DEMO_RISKS.map((risk) => (
                  <li key={risk}>{risk}</li>
                ))}
              </ol>
            </section>
            {/* // Изменить после появления агента */}
            <section className={css.card}>
              <h3 className={css.sectionTitle}>Возможности</h3>
              <ol className={css.numberedList}>
                {DEMO_OPPORTUNITIES.map((opportunity) => (
                  <li key={opportunity}>{opportunity}</li>
                ))}
              </ol>
            </section>
          </div>

          <section className={`${css.card} ${css.aiCard}`}>
            <h3 className={css.aiTitle}>
              <span className={css.sparkle} aria-hidden="true">
                ✦
              </span>
              AI-анализ на основе данных банка
            </h3>


            {/* // Изменить после появления агента */}
            <p className={css.bodyText}>
              С учётом вашей отрасли и структуры кредитного портфеля, повышение ставки может увеличить расходы на
              обслуживание кредитов на 12–18% в 2027 году. Рекомендуем Вам рассмотреть возможность рефинансирования
              части обязательств.
            </p>
            <input
              className={css.questionInput}
              type="text"
              placeholder="Задайте уточняющий вопрос..."
              aria-label="Уточняющий вопрос (будет доступен после подключения агента)"
              title="AI-чат будет доступен после подключения агента"
              disabled
            />
          </section>
          <section className={`${css.card} ${css.sourceFooter}`}>
            <span>Источники и материалы</span>
            <a
              href={news.source_url}
              target="_blank"
              rel="noopener noreferrer"
              title="Открыть источник новости"
              aria-label="Открыть источник новости"
            >
              <img src='images/share_icon.svg'/>
            </a>
          </section>
        </>
      )}
    </section>
  );
}
