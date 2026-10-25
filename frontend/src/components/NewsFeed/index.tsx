import { useMemo, useState } from 'react';
import type { News } from '../../api/news';
import css from './index.module.scss';
import { Loader } from '../Loader';
import { getRelevanceStatus } from '../../lib/getRelevanceStatus';

export type PreviewStatus = 'risk' | 'opportunity' | 'watch';
type Tab = 'all' | 'high' | 'low';

const TABS: { id: Tab; label: string }[] = [
  { id: 'all', label: 'Все новости' },
  { id: 'high', label: 'Высокая релевантность' },
  { id: 'low', label: 'Низкая релевантность' },
];

function formatDate(date: string | null): string {
  if (!date) return 'Дата не указана';
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed);
}

interface NewsFeedProps {
  items: News[];
  selectedNewsId: string | null;
  onSelectNews: (newsId: string) => void;
  isLoading: boolean;
  error: string | null;
}

export function NewsFeed({ items, selectedNewsId, onSelectNews, isLoading, error }: NewsFeedProps) {
  const [tab, setTab] = useState<Tab>('all');
  const [query, setQuery] = useState('');
  const [showSourceFilter, setShowSourceFilter] = useState(false);
  const [source, setSource] = useState('all');

  const sources = useMemo(() => [...new Set(items.map((item) => item.source_name))].sort(), [items]);

  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase('ru');

    return items.filter((item) => {
      const score = item.relevance_score;
      if (tab === 'high' && (score === undefined || score < 20)) {
        return false;
      }
      if (tab === 'low' && (score === undefined || score >= 20)) {
        return false;
      }
      if (source !== 'all' && item.source_name !== source) {
        return false;
      }
      if (!term) return true;
      const reasons = item.relevance_reasons?.map((reason) => reason.reason).join(' ') ?? '';

      return (
        item.title.toLocaleLowerCase('ru').includes(term) ||
        item.source_name.toLocaleLowerCase('ru').includes(term) ||
        reasons.toLocaleLowerCase('ru').includes(term)
      );
    });
  }, [items, tab, query, source]);

  return (
    <section className={css.feed}>
      <nav className={css.tabs}>
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`${css.tab} ${tab === item.id ? css.tabActive : ''}`}
            onClick={() => setTab(item.id)}
            aria-pressed={tab === item.id}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className={css.searchRow}>
        <div className={css.searchBox}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" strokeWidth="1.8" fill="none" />
            <path d="m15.5 15.5 5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
          <input
            type="search"
            aria-label="Поиск по новостям"
            placeholder="Поиск по новостям"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <button
          type="button"
          className={`${css.filterButton} ${showSourceFilter ? css.filterButtonActive : ''}`}
          onClick={() => setShowSourceFilter((old) => !old)}
          aria-expanded={showSourceFilter}
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
            <path d="M4 7h16M7 12h10m-7 5h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          Фильтры
        </button>
      </div>

      {showSourceFilter && (
        <label className={css.sourceFilter}>
          Источник
          <select value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="all">Все источники</option>
            {sources.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}

      <p className={css.foundText}>Найдено {filtered.length} новостей в базе</p>
      <p className={css.demoNote}>Метки риска и возможностей — демонстрационные до подключения AI-агента.</p>

      {isLoading && <Loader type="page" />}
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      {!isLoading && !error && filtered.length === 0 && (
        <p className={css.stateMessage}>Новостей по выбранным условиям нет.</p>
      )}

      <div className={css.cards}>
        {!isLoading &&
          !error &&
          filtered.map((item) => {
            const relevance = getRelevanceStatus(item.relevance_score);
            return (
              <button
                type="button"
                key={item.id}
                onClick={() => onSelectNews(item.id)}
                className={`${css.card} ${selectedNewsId === item.id ? css.cardSelected : ''}`}
                aria-pressed={selectedNewsId === item.id}
              >
                <div className={css.cardTop}>
                  <span className={`${css.badge} ${css[relevance.className]}`}>
                    {relevance.label}
                    <span className={css.relevanceTooltip} role="tooltip">
                      Релевантность: {item.relevance_score ?? '—'}/100
                    </span>
                  </span>

                  <time className={css.date} dateTime={item.published_at ?? undefined}>
                    {formatDate(item.published_at)}
                  </time>
                </div>

                <h3 className={css.title}>{item.title.replace(/\s*\(\d{1,2}\.\d{1,2}\.\d{4}\)\s*$/, '')}</h3>

                <p className={css.excerpt}>
                  {item.relevance_reasons?.map((reason) => reason.reason).join(' ') ||
                    'Подобрано на основе профиля компании.'}
                </p>

                <div className={css.publisher}>
                  <span>{item.source_name}</span>
                </div>
              </button>
            );
          })}
      </div>
    </section>
  );
}
