import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AnimatePresence, motion } from 'motion/react';
import { getNews, type News } from '../../api/news';
import { NewsFeed, previewStatusForIndex } from '../../components/NewsFeed';
import { NewsDetails } from '../../components/NewsDetails';
import css from './index.module.scss';

type BankProducts = Record<string, boolean | null>;
interface CompanyProfile {
  industry: string | null;
  features?: {
    bank_products?: BankProducts;
  };
}

const PRODUCTS: Record<string, string> = {
  settlement: 'РКО',
  guarantee: 'Гарантии',
  loan: 'Кредит',
  leasing: 'Лизинг',
  deposit: 'Депозит',
  payroll: 'Зарплатный проект',
  factoring: 'Факторинг',
};
const ROLES: Record<string, string> = {
  owner: 'Владелец бизнеса',
  director: 'Руководитель',
  finance: 'Финансовый директор',
  analyst: 'Аналитик',
};

function getSelectedNewsId(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

export const NewsFeedPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const companyId = searchParams.get('company');
  const role = searchParams.get('role');
  const selectedNewsId = getSelectedNewsId(searchParams.get('news'));

  const [news, setNews] = useState<News[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [company, setCompany] = useState<CompanyProfile | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    getNews(controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setNews(response.items);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : 'Не удалось загрузить новости');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!companyId) {
      setCompany(null);
      return;
    }
    const controller = new AbortController();
    setCompany(null);
    fetch(`/api/companies/${encodeURIComponent(companyId)}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Профиль компании недоступен');
        return response.json() as Promise<CompanyProfile>;
      })
      .then((data) => {
        if (!controller.signal.aborted) setCompany(data);
      })
      .catch(() => {
        if (!controller.signal.aborted) setCompany(null);
      });
    return () => controller.abort();
  }, [companyId]);

  const productNames = useMemo(() => {
    const features = company?.features?.bank_products;
    if (!features) return 'не указаны';
    const selected = Object.entries(PRODUCTS)
      .filter(([key]) => features[key] === true)
      .map(([, label]) => label);
    return selected.length > 0 ? selected.join(', ') : 'не указаны';
  }, [company]);

  const selectedStatus = useMemo(() => {
    const index = news.findIndex((item) => item.id === selectedNewsId);
    return index >= 0 ? previewStatusForIndex(index) : undefined;
  }, [news, selectedNewsId]);

  const openNews = (id: number) => {
    setSearchParams((old) => {
      const next = new URLSearchParams(old);
      next.set('news', String(id));
      return next;
    });
  };

  const closeNews = () => {
    setSearchParams((old) => {
      const next = new URLSearchParams(old);
      next.delete('news');
      return next;
    });
  };
  const analyticsUrl = useMemo(() => {
    const params = new URLSearchParams(searchParams);
    params.delete('news');
    const query = params.toString();
    return `/analytics${query ? `?${query}` : ''}`;
  }, [searchParams]);

  return (
    <div className={css.shell}>
      <aside className={css.sidebar} aria-label="Навигация">
        <div className={css.brand}>
          <span className={css.logoMark} aria-hidden="true">
            А
          </span>
          <span className={css.brandText}>Альфа-Банк</span>
        </div>
        <nav className={css.sideNav}>
          <Link className={css.navLink} to="/">
            Главная
          </Link>
          <Link className={`${css.navLink} ${css.navActive}`} to={analyticsUrl} aria-current="page">
            Аналитика
          </Link>
          <button className={css.navLink} type="button" disabled title="Раздел пока не реализован">
            Компании
          </button>
          <button className={css.navLink} type="button" disabled title="Раздел пока не реализован">
            Чат с AI
          </button>
          <button className={css.navLink} type="button" disabled title="Раздел пока не реализован">
            Настройки
          </button>
          <button className={css.navLink} type="button" disabled title="Раздел пока не реализован">
            Рекомендации
          </button>
        </nav>
      </aside>

      <div className={css.mainColumn}>
        <header className={css.header}>
          <div className={css.companyContext}>
            <strong>Контекст компании:</strong>
            <div className={css.contextLine}>
              <span>Компания: {companyId ?? 'не выбрана'}</span>
              <i aria-hidden="true" />
              <span>Отрасль: {company?.industry ?? 'не указана'}</span>
              <i aria-hidden="true" />
              <span>Продукты: {productNames}</span>
            </div>
          </div>
          <div className={css.person} title="Демонстрационный профиль из макета">
            <div className={css.personText}>
              <strong>Иван Иванов</strong>
              <span>{ROLES[role ?? ''] ?? 'Руководитель'} · демо</span>
            </div>
            <div className={css.avatar} aria-hidden="true">
              ИИ
            </div>
          </div>
        </header>

        <div className={`${css.workspace} ${selectedNewsId !== null ? css.workspaceOpen : ''}`}>
          <motion.div
            layout
            className={css.feedColumn}
            transition={{ layout: { duration: 0.44, ease: [0.22, 1, 0.36, 1] } }}
          >
            <NewsFeed
              items={news}
              selectedNewsId={selectedNewsId}
              onSelectNews={openNews}
              isLoading={isLoading}
              error={error}
            />
          </motion.div>

          <AnimatePresence initial={false}>
            {selectedNewsId !== null && (
              <motion.aside
                key="details-panel"
                className={css.detailsColumn}
                initial={{ x: 95, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ x: 95, opacity: 0 }}
                transition={{ duration: 0.44, ease: [0.22, 1, 0.36, 1] }}
              >
                <NewsDetails newsId={selectedNewsId} previewStatus={selectedStatus} onClose={closeNews} />
              </motion.aside>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
};
