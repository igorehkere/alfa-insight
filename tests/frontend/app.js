const $ = (id) => document.getElementById(id);
const roles = { analyst: 'Аналитик', director: 'Руководитель', finance: 'Финансовый директор', manager: 'Клиентский менеджер' };
const suggestions = {
  analyst: ['Каких данных не хватает для уверенного вывода?', 'На каких источниках основан этот вывод?'],
  director: ['Какие действия стоит рассмотреть в первую очередь?', 'Что нужно уточнить перед принятием решения?'],
  finance: ['Какие параметры финансирования нужно уточнить?', 'Какие риски для денежных потоков здесь возможны?'],
  manager: ['Что стоит обсудить с клиентом?', 'Какие вопросы помогут уточнить ситуацию компании?'],
};
const state = { company: null, person: 'Дима', role: 'analyst', news: null, insight: null, catalog: [], ranked: [], feedMode: 'all', busy: false, loadingNews: false, sources: new Map() };
let searchSequence = 0;
let newsSequence = 0;
let timer;
let searchTimer;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function show(id, visible = true) { $(id).hidden = !visible; }
function errorBox(id, error) { $(id).textContent = error?.message || String(error || ''); show(id, Boolean(error)); }
function toast(message) {
  $('toast').textContent = message;
  show('toast');
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => show('toast', false), 4000);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.method === 'POST' ? 220000 : 25000);
  try {
    const response = await fetch(path, {
      method: options.method || 'GET', signal: controller.signal,
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      let detail = data.detail;
      if (Array.isArray(detail)) detail = detail.map((item) => item.msg).join('; ');
      throw new Error(`HTTP ${response.status}: ${typeof detail === 'string' ? detail : 'Не удалось выполнить запрос'}`);
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Истекло время ожидания. Проверьте сервер и повторите запрос.');
    if (error instanceof TypeError) throw new Error('Нет соединения с локальным сервером. Проверьте, что FastAPI запущен.');
    throw error;
  } finally { clearTimeout(timeout); }
}

function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && url.hostname !== 'test.invalid' ? url.href : null;
  } catch { return null; }
}

function dateLabel(value) {
  if (!value || Number.isNaN(new Date(value).getTime())) return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(value));
}

function saveContext() {
  try { localStorage.setItem('alfa-test-context-v1', JSON.stringify({ companyId: state.company?.company_id, person: state.person, role: state.role })); }
  catch { /* The app still works when browser storage is disabled. */ }
}

function readContext() {
  try {
    const value = JSON.parse(localStorage.getItem('alfa-test-context-v1') || '{}');
    if (typeof value.person === 'string') $('employee-name').value = value.person.slice(0, 80);
    if (Object.hasOwn(roles, value.role)) $('employee-role').value = value.role;
    return value;
  } catch { return {}; }
}

const remembered = readContext();

async function loadHealth() {
  try {
    const health = await api('/health');
    $('health-status').textContent = health.status !== 'ok' ? 'Нужна настройка LLM' : health.llm_mode === 'live' ? 'Живая модель' : 'Офлайн-режим';
    $('health-status').className = `status-pill ${health.status === 'ok' ? 'connected' : 'error'}`;
  } catch (error) {
    $('health-status').textContent = 'Сервер недоступен';
    $('health-status').className = 'status-pill error';
    $('health-status').title = error.message;
  }
}

async function loadCompanies(search = '') {
  const sequence = ++searchSequence;
  try {
    const data = await api(`/api/companies?${new URLSearchParams({ search, limit: '100' })}`);
    if (sequence !== searchSequence) return;
    const previous = $('company-select').value || state.company?.company_id || remembered.companyId;
    $('company-select').replaceChildren(el('option', data.items.length ? 'Выберите компанию из списка' : 'Компании не найдены'));
    $('company-select').firstChild.value = '';
    for (const item of data.items) {
      const option = el('option', `${item.company_id} · ${item.industry_name || 'Отрасль не указана'}${item.is_ambiguous ? ' — неоднозначный профиль' : ''}`);
      option.value = item.company_id;
      option.disabled = item.is_ambiguous;
      $('company-select').append(option);
    }
    if (data.items.some((item) => item.company_id === previous && !item.is_ambiguous)) $('company-select').value = previous;
    errorBox('context-error', null);
  } catch (error) { if (sequence === searchSequence) errorBox('context-error', error); }
}

function setStep(step) {
  ['context', 'news', 'analysis'].forEach((name, index) => {
    $(`step-${name}`).className = index + 1 === step ? 'active' : index + 1 < step ? 'done' : '';
  });
}

function renderContext() {
  $('active-company').textContent = state.company.company_id;
  $('active-industry').textContent = `${state.company.industry || 'Отрасль не указана'} · ${state.company.region || 'Регион не указан'}`;
  $('active-person').textContent = state.person;
  $('active-role').textContent = roles[state.role];
  show('setup-panel', false);
  show('context-strip');
  show('workspace');
  saveContext();
}

function resetAnalysis() {
  state.news = null;
  state.insight = null;
  state.sources = new Map();
  for (const id of ['article-panel', 'analysis-panel', 'analysis-error', 'analysis-loading', 'chat-panel']) show(id, false);
  show('article-empty');
  show('sources-empty');
  $('sources-list').replaceChildren();
  $('sources-count').textContent = '0';
  $('trace-list').replaceChildren();
  $('trace-count').textContent = '';
  $('raw-result').textContent = 'Анализ ещё не запущен.';
  history.replaceState(null, '', location.pathname);
}

function setBusy(busy) {
  state.busy = busy;
  for (const id of ['change-context', 'choose-news', 'choose-news-empty', 'refresh-analysis', 'retry-analysis', 'send-question']) $(id).disabled = busy;
  $('chat-question').disabled = busy;
  document.querySelectorAll('.news-card, .suggestion').forEach((button) => { button.disabled = busy; });
}

async function openNews() {
  if (!state.company || state.busy) return;
  if (!$('news-dialog').open) $('news-dialog').showModal();
  $('news-context').textContent = `${state.company.company_id} · ${state.person}, ${roles[state.role]}`;
  $('news-search').value = '';
  await loadNews();
}

async function loadNews() {
  const sequence = ++newsSequence;
  state.loadingNews = true;
  state.ranked = [];
  errorBox('news-error', null);
  $('news-list').replaceChildren(el('p', 'Загружаем новости…', 'news-empty'));
  $('reload-news').disabled = true;
  const [catalog, ranked] = await Promise.allSettled([
    api('/igor/api/news/catalog?limit=100&offset=0'),
    api(`/api/news?${new URLSearchParams({ company_id: state.company.company_id, limit: '100' })}`),
  ]);
  if (sequence !== newsSequence) return;
  state.loadingNews = false;
  $('reload-news').disabled = false;
  if (catalog.status === 'rejected') {
    errorBox('news-error', catalog.reason);
    $('news-list').replaceChildren(el('p', 'Каталог не загрузился. Нажмите «Обновить».', 'news-empty'));
    return;
  }
  state.catalog = catalog.value.items;
  state.ranked = ranked.status === 'fulfilled' ? ranked.value.items : [];
  if (ranked.status === 'rejected') errorBox('news-error', new Error(`Каталог доступен, но персональный подбор пока не готов. ${ranked.reason.message}`));
  renderNews();
}

function originLabel(item) {
  const source = item.source_name || item.source;
  if (source === 'synthetic_fixture') return 'Учебная новость';
  if (source === 'manual_submission') return 'Добавлена вручную';
  return source || 'Источник не указан';
}

function renderNews() {
  if (state.loadingNews) {
    $('news-list').replaceChildren(el('p', 'Загружаем новости…', 'news-empty'));
    $('news-count').textContent = '';
    return;
  }
  const query = $('news-search').value.trim().toLocaleLowerCase('ru');
  const ranked = new Map(state.ranked.map((item) => [String(item.id), item]));
  let items = state.catalog.filter((item) => state.feedMode === 'all' || ranked.has(String(item.id)));
  if (state.feedMode === 'personal') items.sort((a, b) => (ranked.get(String(b.id))?.relevance_score || 0) - (ranked.get(String(a.id))?.relevance_score || 0));
  items = items.filter((item) => !query || (/^\d+$/.test(query) ? String(item.id) === query : `${item.title} ${item.source_name} ${item.description || ''}`.toLocaleLowerCase('ru').includes(query)));
  $('all-news').classList.toggle('selected', state.feedMode === 'all');
  $('personal-news').classList.toggle('selected', state.feedMode === 'personal');
  $('news-count').textContent = `Найдено событий: ${items.length}${state.catalog.length === 100 ? ' · показаны последние 100 записей' : ''}`;
  $('news-list').replaceChildren();
  if (!items.length) $('news-list').append(el('p', state.feedMode === 'personal' ? 'Для этой компании совпадений нет. Посмотрите всю ленту или добавьте свой текст.' : 'Ничего не найдено. Попробуйте другую тему или ID.', 'news-empty'));
  for (const item of items) {
    const button = el('button', null, 'news-card');
    button.type = 'button';
    button.disabled = state.busy;
    const top = el('div', null, 'news-card-top');
    const origin = originLabel(item);
    top.append(el('span', origin, `tag ${['synthetic_fixture', 'manual_submission'].includes(item.source_name) ? 'warning-tag' : ''}`), el('span', `#${item.id}`, 'muted'));
    const footer = el('div', null, 'news-card-footer');
    const score = ranked.get(String(item.id));
    footer.append(el('span', score ? `Релевантность: ${score.relevance_score}/100` : 'Открыть аналитику', score ? 'score' : ''), el('span', '↗'));
    if (score) button.title = score.relevance_reasons.map((reason) => reason.reason).join('\n');
    button.append(top, el('h3', item.title), el('div', dateLabel(item.published_at), 'news-card-meta'), footer);
    button.addEventListener('click', () => selectNews(item));
    $('news-list').append(button);
  }
}

function renderArticle(item) {
  show('article-empty', false);
  show('article-panel');
  $('article-title').textContent = item.title;
  $('article-body').textContent = item.content || item.description || item.body || 'Полный текст не предоставлен источником.';
  $('article-meta').textContent = `${dateLabel(item.published_at)} · ${originLabel(item)} · #${item.id}`;
  $('article-origin').textContent = originLabel(item);
  $('article-origin').className = `tag ${['synthetic_fixture', 'manual_submission'].includes(item.source_name) ? 'warning-tag' : ''}`;
  const url = safeUrl(item.source_url);
  show('article-link', Boolean(url));
  if (url) $('article-link').href = url;
}

async function selectNews(item) {
  if (state.busy) return;
  $('news-dialog').close();
  state.news = item;
  state.insight = null;
  history.replaceState(null, '', location.pathname);
  show('analysis-panel', false);
  show('chat-panel', false);
  $('sources-list').replaceChildren();
  $('sources-count').textContent = '0';
  show('sources-empty');
  $('trace-list').replaceChildren();
  $('trace-count').textContent = '';
  $('raw-result').textContent = 'Запрос выполняется…';
  renderArticle(item);
  setStep(3);
  await analyze(false, true);
}

async function analyze(force = false, loadArticle = false) {
  if (!state.news || state.busy) return;
  setBusy(true);
  show('analysis-error', false);
  show('analysis-loading');
  const started = Date.now();
  $('analysis-timer').textContent = '0 с';
  timer = setInterval(() => { $('analysis-timer').textContent = `${Math.floor((Date.now() - started) / 1000)} с`; }, 1000);
  try {
    if (loadArticle) {
      state.news = await api(`/igor/api/news/${encodeURIComponent(state.news.id)}`);
      renderArticle(state.news);
    }
    const result = await api('/api/insights/analyze', { method: 'POST', body: { company_id: state.company.company_id, news_id: String(state.news.id), force_refresh: force } });
    await renderAnalysis(result);
    history.replaceState(null, '', `${location.pathname}?${new URLSearchParams({ insight: result.insight_id })}`);
    toast('Анализ готов. Источники и шаги агента доступны справа.');
  } catch (error) {
    $('analysis-error-text').textContent = error.message;
    show('analysis-error');
    $('raw-result').textContent = error.message;
  } finally {
    clearInterval(timer);
    show('analysis-loading', false);
    setBusy(false);
    loadHealth();
  }
}

function citations(parent, sourceIds = []) {
  for (const sourceId of sourceIds) {
    const number = state.sources.get(sourceId);
    if (!number) continue;
    const anchor = el('a', `[${number}]`, 'citation');
    anchor.href = `#source-${number}`;
    anchor.title = `Источник ${number}`;
    parent.append(anchor);
  }
}

function resultCard(title, items, className = '') {
  const card = el('section', null, `panel result-card ${className}`);
  card.append(el('h3', title));
  for (const item of items || []) {
    const text = typeof item === 'string' ? item : item.text;
    const paragraph = el('p', text, 'statement');
    citations(paragraph, item.source_ids);
    card.append(paragraph);
  }
  if (!items?.length) card.append(el('p', 'Подтверждённых выводов в этой категории нет.', 'muted'));
  return card;
}

async function renderAnalysis(publicResult) {
  state.insight = publicResult;
  const result = publicResult.result;
  state.sources = new Map(result.sources.map((source, index) => [source.id, index + 1]));
  const container = $('analysis-sections');
  container.replaceChildren();
  container.append(resultCard('Что произошло', result.what_happened), resultCard('Почему это важно для компании', result.why_relevant));
  const bank = el('section', null, 'panel result-card bank-card');
  bank.append(el('h3', 'Взгляд Альфа-Банка'));
  if (result.bank_position.status === 'found') {
    const quote = el('blockquote', result.bank_position.text);
    citations(quote, result.bank_position.source_ids);
    bank.append(quote);
  } else bank.append(el('p', 'Подтверждённая позиция банка по этому событию не найдена.', 'not-found'));
  container.append(bank);
  const ai = resultCard('Интерпретация события', [result.ai_interpretation], 'ai-card');
  ai.querySelector('h3').append(el('span', 'AI', 'ai-label'));
  container.append(ai);
  const impacts = el('div', null, 'impact-grid');
  impacts.append(resultCard('Возможные риски', result.risks, 'impact-card'), resultCard('Возможности', result.opportunities, 'impact-card opportunity'));
  container.append(impacts, resultCard('Что стоит проверить', result.suggested_actions));
  show('warnings-box', Boolean(result.warnings.length));
  $('warnings-title').textContent = `Ограничения анализа · ${result.warnings.length}`;
  $('warnings-list').replaceChildren(...result.warnings.map((warning) => el('li', warning)));
  renderSources(result.sources);
  renderTrace(publicResult.agent_trace);
  $('raw-result').textContent = JSON.stringify(publicResult, null, 2);
  show('analysis-panel');
  show('chat-panel');
  $('chat-history').replaceChildren();
  $('chat-suggestions').replaceChildren();
  $('chat-question').value = '';
  errorBox('chat-error', null);
  for (const question of suggestions[state.role] || suggestions.analyst) {
    const button = el('button', question, 'suggestion');
    button.type = 'button';
    button.addEventListener('click', () => { $('chat-question').value = question; $('chat-question').focus(); });
    $('chat-suggestions').append(button);
  }
  const id = publicResult.insight_id;
  const [trace, history] = await Promise.allSettled([api(`/api/insights/${encodeURIComponent(id)}/trace`), api(`/api/insights/${encodeURIComponent(id)}/messages`)]);
  if (state.insight?.insight_id !== id) return;
  if (trace.status === 'fulfilled') renderTrace(trace.value.items);
  if (history.status === 'fulfilled') {
    for (const message of history.value.items) {
      appendMessage('user', message.question);
      appendMessage('assistant', message.response.answer, message.response.source_ids, message.response.warnings);
    }
  } else errorBox('chat-error', new Error(`История не загрузилась. ${history.reason.message}`));
}

function renderSources(sources) {
  $('sources-list').replaceChildren();
  $('sources-count').textContent = String(sources.length);
  show('sources-empty', !sources.length);
  sources.forEach((source, index) => {
    const card = el('div', null, 'source-card');
    card.id = `source-${index + 1}`;
    card.append(el('span', `${index + 1} · ${source.type === 'bank' ? 'Аналитика банка' : 'Новость'}`, 'tag'), el('strong', source.document_title || source.title));
    if (source.type === 'bank') card.append(el('p', `Страница ${source.page_number}`));
    const url = safeUrl(source.url);
    if (url) {
      const link = el('a', 'Первоисточник ↗');
      link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link);
    }
    $('sources-list').append(card);
  });
}

function renderTrace(items) {
  $('trace-list').replaceChildren();
  $('trace-count').textContent = `· ${items.length}`;
  const phases = { plan: 'Планирование', execute: 'Получение данных', verify: 'Проверка' };
  const tools = { get_company_profile: 'Профиль компании', get_news: 'Текст новости', search_bank_analytics: 'Поиск аналитики банка', get_company_counterparties: 'Платёжные связи', search_related_news: 'Связанные новости' };
  for (const step of items) {
    const item = el('li', null, step.status === 'ok' ? '' : 'trace-warning');
    item.append(el('strong', `${step.step}. ${tools[step.tool] || phases[step.phase] || step.phase}`), el('p', step.details));
    $('trace-list').append(item);
  }
}

function appendMessage(author, text, sourceIds = [], warnings = []) {
  const message = el('div', null, `chat-message ${author}`);
  message.append(el('span', author === 'user' ? 'Вопрос' : 'Альфа Инсайт', 'message-author'), el('span', text));
  citations(message, sourceIds);
  if (warnings.length) message.append(el('p', warnings.join(' '), 'message-warning'));
  $('chat-history').append(message);
  $('chat-history').scrollTop = $('chat-history').scrollHeight;
}

async function loadDiagnostics() {
  $('diagnostic-panel').open = true;
  $('refresh-diagnostics').disabled = true;
  try {
    const data = await api('/api/debug/llm');
    $('diagnostic-result').replaceChildren();
    if (!data.items.length) $('diagnostic-result').append(el('p', 'Вызовов после запуска сервера ещё нет. Готовый результат мог прийти из кеша.', 'field-note'));
    for (const item of data.items.slice(-12).reverse()) {
      const card = el('div', null, 'metric');
      card.append(el('strong', `${item.stage} · ${item.endpoint}`), el('span', `Завершение: ${item.finish_reason || item.error || '—'}`), el('span', `Токены: ${item.total_tokens ?? '—'} · reasoning: ${item.reasoning_tokens ?? '—'}`), el('span', `Текст: ${item.content_characters ?? '—'} символов · ${item.elapsed_seconds ?? '—'} с`));
      $('diagnostic-result').append(card);
    }
  } catch (error) { $('diagnostic-result').replaceChildren(el('p', error.message, 'field-note')); }
  finally { $('refresh-diagnostics').disabled = false; }
}

async function restoreInsight(id) {
  if (!/^insight-[a-f0-9]{32,64}$/.test(id)) throw new Error('Некорректная ссылка на анализ. Выберите компанию и новость заново.');
  const saved = await api(`/api/insights/${encodeURIComponent(id)}`);
  const [company, news] = await Promise.all([api(`/api/companies/${encodeURIComponent(saved.company_id)}`), api(`/igor/api/news/${encodeURIComponent(saved.news_id)}`)]);
  state.company = company;
  state.person = $('employee-name').value.trim() || 'Дима';
  state.role = $('employee-role').value;
  state.news = news;
  renderContext(); renderArticle(news); setStep(3);
  await renderAnalysis(saved);
}

$('company-search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadCompanies($('company-search').value), 300); });
$('context-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!$('employee-name').value.trim()) { errorBox('context-error', new Error('Укажите имя сотрудника.')); return; }
  $('open-news').disabled = true;
  errorBox('context-error', null);
  try {
    state.company = await api(`/api/companies/${encodeURIComponent($('company-select').value)}`);
    state.person = $('employee-name').value.trim();
    state.role = $('employee-role').value;
    resetAnalysis(); renderContext(); setStep(2);
    state.feedMode = 'all';
    await openNews();
  } catch (error) { errorBox('context-error', error); }
  finally { $('open-news').disabled = false; }
});
$('change-context').addEventListener('click', () => { if (!state.busy) { show('setup-panel'); show('context-strip', false); show('workspace', false); setStep(1); $('context-form').scrollIntoView({ block: 'center' }); } });
$('choose-news').addEventListener('click', openNews);
$('choose-news-empty').addEventListener('click', openNews);
$('close-news').addEventListener('click', () => $('news-dialog').close());
$('reload-news').addEventListener('click', loadNews);
$('news-search').addEventListener('input', renderNews);
$('all-news').addEventListener('click', () => { state.feedMode = 'all'; renderNews(); });
$('personal-news').addEventListener('click', () => { state.feedMode = 'personal'; renderNews(); });
$('refresh-analysis').addEventListener('click', () => analyze(true));
$('retry-analysis').addEventListener('click', () => analyze(false));
$('refresh-diagnostics').addEventListener('click', loadDiagnostics);
$('error-diagnostics').addEventListener('click', () => { loadDiagnostics(); $('diagnostic-panel').scrollIntoView({ block: 'center' }); });
$('chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = $('chat-question').value.trim();
  if (!question || !state.insight || state.busy) return;
  setBusy(true);
  errorBox('chat-error', null);
  $('send-question').textContent = 'Готовим ответ…';
  try {
    const response = await api(`/api/insights/${encodeURIComponent(state.insight.insight_id)}/ask`, { method: 'POST', body: { question } });
    appendMessage('user', question);
    appendMessage('assistant', response.answer, response.source_ids, response.warnings);
    $('chat-question').value = '';
  } catch (error) { errorBox('chat-error', error); }
  finally { setBusy(false); $('send-question').textContent = 'Отправить ↑'; }
});
$('manual-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('save-manual').disabled = true;
  errorBox('manual-error', null);
  show('manual-success', false);
  try {
    const date = $('manual-date').value;
    const response = await api('/api/news/manual', { method: 'POST', body: { title: $('manual-title').value.trim(), body: $('manual-body').value.trim(), source_url: $('manual-url').value.trim() || null, published_at: date ? new Date(date).toISOString() : null } });
    $('manual-form').reset();
    $('manual-success').textContent = `Новость #${response.id} добавлена. Найдите её в ленте и выберите для анализа.`;
    show('manual-success');
    state.feedMode = 'all'; $('news-search').value = String(response.id);
    await loadNews();
    if (!state.catalog.some((item) => String(item.id) === String(response.id))) {
      state.catalog.unshift(await api(`/igor/api/news/${encodeURIComponent(response.id)}`));
      renderNews();
    }
  } catch (error) { errorBox('manual-error', error); }
  finally { $('save-manual').disabled = false; }
});

await Promise.allSettled([loadCompanies(), loadHealth()]);
const savedId = new URLSearchParams(location.search).get('insight');
if (savedId) {
  setBusy(true);
  try { await restoreInsight(savedId); }
  catch (error) { errorBox('context-error', error); }
  finally { setBusy(false); }
}
