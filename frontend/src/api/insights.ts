import type {
  AnalyzeRequest,
  InsightResponse,
  AskRequest,
  AnswerResponse,
  TraceResponse,
  MessagesResponse,
} from './agent-types';

async function readApiResponse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join('; ')
          : `HTTP ${response.status}`;

    throw new Error(message);
  }

  return data as T;
}

export const analyzeNews = async (request: AnalyzeRequest): Promise<InsightResponse> => {
  const response = await fetch('/api/insights/analyze', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  return readApiResponse<InsightResponse>(response);
};

export const getInsight = async (insightId: string): Promise<InsightResponse> => {
  const response = await fetch(`/api/insights/${encodeURIComponent(insightId)}`);

  return readApiResponse<InsightResponse>(response);
};

export const askQuestion = async (insightId: string, request: AskRequest): Promise<AnswerResponse> => {
  const response = await fetch(`/api/insights/${encodeURIComponent(insightId)}/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  return readApiResponse<AnswerResponse>(response);
};

export const getTrace = async (insightId: string): Promise<TraceResponse> => {
  const response = await fetch(`/api/insights/${encodeURIComponent(insightId)}/trace`);

  return readApiResponse<TraceResponse>(response);
};

export const getMessages = async (insightId: string): Promise<MessagesResponse> => {
  const response = await fetch(`/api/insights/${encodeURIComponent(insightId)}/messages`);

  return readApiResponse<MessagesResponse>(response);
};
