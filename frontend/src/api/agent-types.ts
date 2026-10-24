export interface NewsCard {
  id: string;
  title: string;
  source: string;
  source_url: string | null;
  published_at: string | null;
  topic_tags: string[];
  relevance_score: number;
  relevance_reasons: Array<{ company_field: string; reason: string }>;
}

export interface CompanyNewsFeed {
  company_id: string;
  items: NewsCard[];
}

export interface NewsDetails {
  id: string;
  title: string;
  body: string;
  source: string;
  source_url: string | null;
  published_at: string | null;
  topic_tags: string[];
  provenance: string;
  body_is_summary: boolean;
}

// Готовая фабрика возвращает только items, без limit/offset в ответе.
export interface NewsCatalogResponse {
  items: NewsDetails[];
}

export interface AnalyzeRequest {
  company_id: string;
  news_id: string;
  force_refresh?: boolean;
}

export interface Statement {
  text: string;
  source_ids: string[];
}
export interface ImpactStatement extends Statement {
  certainty: string;
}

interface SourceBase {
  id: string;
  title: string;
  url: string | null;
}

export interface NewsSource extends SourceBase {
  type: 'news';
}
export interface BankSource extends SourceBase {
  type: 'bank';
  document_id: string;
  document_title: string;
  page_number: number;
}
export type Source = NewsSource | BankSource;

export type BankPosition =
  | { status: 'found'; text: string; source_ids: string[] }
  | { status: 'not_found'; text: null; source_ids: [] };

export interface InsightResult {
  what_happened: Statement[];
  why_relevant: Array<{ text: string; company_fields: string[] }>;
  risks: ImpactStatement[];
  opportunities: ImpactStatement[];
  bank_position: BankPosition;
  ai_interpretation: string;
  suggested_actions: string[];
  sources: Source[];
  warnings: string[];
}

export interface AgentTraceStep {
  step: number;
  phase: 'plan' | 'execute' | 'verify';
  tool: string | null;
  status: 'ok' | 'empty' | 'insufficient_evidence' | 'error';
  details: string;
}

export interface InsightResponse {
  insight_id: string;
  company_id: string;
  news_id: string;
  status: 'completed';
  result: InsightResult;
  agent_trace: AgentTraceStep[];
}

export interface AskRequest {
  question: string;
}
export interface AnswerResponse {
  answer: string;
  source_ids: string[];
  warnings: string[];
}
export interface TraceResponse {
  items: AgentTraceStep[];
}
export interface MessagesResponse {
  items: Array<{ question: string; response: AnswerResponse }>;
}

// title: 1–500 символов; body: 1–24000, не только пробелы.
// published_at: ISO datetime с часовым поясом или null; source_url: HTTP(S) или null.
export interface ManualNewsRequest {
  title: string;
  body: string;
  source_url?: string | null;
  published_at?: string | null;
}

export interface ApiError {
  detail: string | Array<{ loc: Array<string | number>; msg: string; type: string }>;
}
