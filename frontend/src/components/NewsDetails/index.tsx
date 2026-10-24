import { useEffect, useRef, useState } from 'react';
import type { InsightResponse } from '../../api/agent-types';
import { analyzeNews, askQuestion } from '../../api/insights';
import { getNewsById, type NewsDetails as NewsDetailsData } from '../../api/news';
import css from './index.module.scss';
import { Loader } from '../Loader';
import { getAnalysisFactors } from '../../lib/getAnalyticsFactors';

interface NewsDetailsProps {
  newsId: string;
  companyId: string;
  industry: string | null;
  products: string;
  onClose: () => void;
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

export function NewsDetails({ newsId, companyId, industry, products }: NewsDetailsProps) {
  const [news, setNews] = useState<NewsDetailsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setChatError(null);
    setNews(null);
    setIsLoading(true);
    setError(null);
    contextVersion.current += 1;
    getNewsById(newsId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setNews(data);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setError(error instanceof Error ? error.message : 'Не удалось загрузить новость');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });

    return () => {
      controller.abort();
      contextVersion.current += 1;
    };
  }, [newsId]);

  const [analysis, setAnalysis] = useState<InsightResponse | null>(null);
  const analysisFields = analysis ? analysis.result.why_relevant.flatMap((item) => item.company_fields) : [];

  const analysisFactors = getAnalysisFactors(analysisFields);

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  // Номер текущего контекста компании и новости.
  const contextVersion = useRef(0);

  useEffect(() => {
    contextVersion.current += 1;

    setAnalysis(null);
    setAnalysisError(null);
    setIsAnalyzing(false);

    setChatMessages([]);
    setQuestion('');
    setIsAsking(false);
  }, [companyId, newsId]);

  const handleAnalyze = async () => {
    if (!companyId || !newsId || isAnalyzing) {
      return;
    }
    const version = contextVersion.current;
    setIsAnalyzing(true);
    setAnalysisError(null);
    try {
      const result = await analyzeNews({
        company_id: companyId,
        news_id: newsId,
        force_refresh: false,
      });
      // Не показываем результат старой компании или новости.
      if (version !== contextVersion.current) {
        return;
      }
      setAnalysis(result);
    } catch (error) {
      if (version !== contextVersion.current) {
        return;
      }
      setAnalysisError(error instanceof Error ? error.message : 'Не удалось выполнить анализ');
    } finally {
      if (version === contextVersion.current) {
        setIsAnalyzing(false);
      }
    }
  };

  // Chat
  const [chatError, setChatError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [isAsking, setIsAsking] = useState(false);
  const [chatMessages, setChatMessages] = useState<
    Array<{
      question: string;
      answer: string;
      warnings: string[];
    }>
  >([]);
  const handleAsk = async () => {
    if (!analysis || !question.trim() || isAsking) {
      return;
    }
    const versionAtRequestStart = contextVersion.current;
    const currentInsightId = analysis.insight_id;
    const text = question.trim();
    setIsAsking(true);
    setChatError(null);
    try {
      const response = await askQuestion(currentInsightId, { question: text });
      if (contextVersion.current !== versionAtRequestStart) {
        return;
      }
      setChatMessages((prev) => [
        ...prev,
        {
          question: text,
          answer: response.answer,
          warnings: response.warnings,
        },
      ]);
      setQuestion('');
    } catch (error) {
      if (contextVersion.current !== versionAtRequestStart) {
        return;
      }
      setChatError(error instanceof Error ? error.message : 'Не удалось получить ответ агента');
    } finally {
      if (contextVersion.current === versionAtRequestStart) {
        setIsAsking(false);
      }
    }
  };

  return (
    <section className={css.details}>
      {isLoading && <Loader type="page" />}
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
            {news.source_url && (
              <a href={news.source_url} className={css.sourceButton} target="_blank" rel="noopener noreferrer">
                Перейти к первоисточнику
              </a>
            )}
          </article>
          <button
            type="button"
            className={css.analyzeButton}
            onClick={handleAnalyze}
            disabled={isAnalyzing || analysis !== null}
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="m12 3 1.9 6.1L20 11l-6.1 1.9L12 19l-1.9-6.1L4 11l6.1-1.9L12 3Z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
              <path d="m19 17 .8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8L19 17Z" fill="currentColor" />
            </svg>

            <span>{isAnalyzing ? 'Анализируем новость...' : analysis ? 'Анализ выполнен' : 'Анализировать с ИИ'}</span>
          </button>

          {analysisError && <p role="alert">{analysisError}</p>}
          <section className={css.card}>
            <h3 className={css.sectionTitle}>Почему это важно для вашей компании?</h3>

            {analysis?.result.why_relevant.map((item, index) => (
              <p key={index} className={css.bodyText}>
                {item.text}
              </p>
            ))}
            {analysis && (
              <div className={css.contextBadges}>
                {industry && (
                  <span>
                    <img src="/images/industry_icon.svg" alt="" />
                    <b>Отрасль:</b> {industry}
                  </span>
                )}

                {products && products !== 'не указаны' && (
                  <span>
                    <img src="/images/product_icon.svg" alt="" />
                    <b>Продукты:</b> {products}
                  </span>
                )}

                {analysisFactors.length > 0 && (
                  <span>
                    <span aria-hidden="true">✦</span>
                    <b>ИИ учитывал:</b> {analysisFactors.join(', ')}
                  </span>
                )}
              </div>
            )}
          </section>

          <div className={css.twoColumns}>
            <section className={css.card}>
              <h3 className={css.sectionTitle}>Риски</h3>
              <ol className={css.numberedList}>
                {analysis?.result.risks.map((risk, index) => (
                  <li key={index}>{risk.text}</li>
                ))}
              </ol>
            </section>
            <section className={css.card}>
              <h3 className={css.sectionTitle}>Возможности</h3>
              <ol className={css.numberedList}>
                {analysis?.result.opportunities.map((item, index) => (
                  <li key={index}>{item.text}</li>
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

            <p className={css.bodyText}>
              {analysis?.result.ai_interpretation ??
                'Запустите анализ, чтобы получить персональные выводы для вашей компании.'}
            </p>

            {analysis && analysis.result.warnings.length > 0 && (
              <div className={css.aiWarnings}>
                {analysis.result.warnings.map((warning, index) => (
                  <p key={index}>{warning}</p>
                ))}
              </div>
            )}

            {/* История переписки */}
            {chatMessages.length > 0 && (
              <div className={css.chatMessages} role="log" aria-live="polite">
                {chatMessages.map((message, index) => (
                  <div className={css.chatExchange} key={index}>
                    {/* Сообщение пользователя */}
                    <div className={`${css.chatMessage} ${css.chatMessageUser}`}>
                      <div className={css.chatBubble}>{message.question}</div>
                    </div>

                    {/* Ответ агента */}
                    <div className={`${css.chatMessage} ${css.chatMessageAi}`}>
                      <span className={css.chatAvatar} aria-hidden="true">
                        ✦
                      </span>

                      <div className={css.chatAnswer}>
                        <span className={css.chatName}>AI-ассистент</span>

                        <div className={css.chatBubble}>{message.answer}</div>

                        {message.warnings.length > 0 && (
                          <div className={css.chatWarnings}>
                            {message.warnings.map((warning, warningIndex) => (
                              <p key={warningIndex}>{warning}</p>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <form
              className={css.chatComposer}
              onSubmit={(event) => {
                event.preventDefault();
                void handleAsk();
              }}
            >
              <input
                className={css.questionInput}
                type="text"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={analysis ? 'Задайте уточняющий вопрос...' : 'Сначала выполните анализ'}
                disabled={!analysis || isAsking}
                maxLength={2000}
                aria-label="Уточняющий вопрос агенту"
              />

              <button
                className={css.chatSendButton}
                type="submit"
                disabled={!analysis || !question.trim() || isAsking}
                aria-label="Отправить сообщение"
              >
                {isAsking ? (
                  <span className={css.sendSpinner} />
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path
                      d="m5 12 14-7-5 14-2.5-6.5L5 12Z"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinejoin="round"
                    />
                    <path d="m11.5 12.5 7.5-7.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                  </svg>
                )}
              </button>
            </form>
          </section>
          <section className={`${css.card} ${css.sourceFooter}`}>
            <div className={css.sourceBar}>
              <span className={css.sourceFooterTitle}>Источники и материалы</span>

              <details className={css.sourcesDropdown}>
                <summary
                  className={css.sourcesToggle}
                  aria-label="Показать источники и материалы"
                  title="Показать источники"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path
                      d="M14 5h5v5M19 5l-9 9M19 13v6H5V5h6"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </summary>

                <div className={css.sourcesMenu}>
                  <div className={css.sourcesMenuHeader}>
                    <strong>Источники анализа</strong>

                    <span>{analysis?.result.sources.length ?? 0}</span>
                  </div>

                  {analysis && analysis.result.sources.length > 0 ? (
                    <div className={css.sourcesList}>
                      {analysis.result.sources.map((source, index) => (
                        <div className={css.sourceItem} key={source.id}>
                          <span className={css.sourceNumber}>{index + 1}</span>

                          <div className={css.sourceItemContent}>
                            <span className={css.sourceType}>
                              {source.type === 'bank' ? 'Документ Альфа-Банка' : 'Новостной источник'}
                            </span>

                            <strong className={css.sourceItemTitle}>
                              {source.type === 'bank' ? source.document_title : source.title}
                            </strong>

                            {source.type === 'bank' && (
                              <span className={css.sourcePage}>Страница {source.page_number}</span>
                            )}

                            {source.url && (
                              <a href={source.url} target="_blank" rel="noopener noreferrer" className={css.sourceLink}>
                                Открыть источник
                                <span aria-hidden="true">↗</span>
                              </a>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className={css.sourcesEmpty}>
                      {analysis ? 'Подтверждённые источники не найдены.' : 'Источники появятся после анализа новости.'}
                    </p>
                  )}
                </div>
              </details>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
