import { cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState } from 'react';
import { ArrowLeft, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { DateControl } from './DateControl.jsx';
import { SelectControl } from './SelectControl.jsx';
import { shortCopy, typograph } from './typography.js';

export const statuses = {
  QUEUED: 'В очереди', WAITING: 'Ожидает', STARTING: 'В резерве',
  RUNNING: 'Выполняется', SUCCEEDED: 'Выполнена', FAILED: 'Ошибка',
  CANCELLED: 'Отменена', PENDING: 'Ожидается', DELIVERED: 'Доставлено', SKIPPED: 'Пропущено',
};
export const sources = { MANUAL: 'Вручную', SCHEDULED: 'По расписанию' };
export const modes = { READ: 'Чтение', WRITE: 'Запись' };
export const terminal = (status) => ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(status);

export function formatDate(value) {
  if (!value) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value.split('-').reverse().join('.');
  const time = new Date(value);
  if (Number.isNaN(time.valueOf())) return value;
  return new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'medium',
  }).format(time) + '\u00a0МСК';
}

export function formatNumber(value, digits) {
  if (value == null || value === '') return '—';
  if (!Number.isFinite(Number(value))) return String(value);
  const precision = digits ?? (typeof value === 'string' ? value.split('.')[1]?.length : undefined);
  return new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: precision, maximumFractionDigits: precision ?? 3,
  }).format(Number(value));
}

export async function api(path, { method = 'GET', body, signal, headers } = {}) {
  let response;
  const timeout = AbortSignal.timeout(12000);
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  try {
    response = await fetch('/api/v1' + path, {
      method, signal: requestSignal, cache: 'no-store',
      headers: { Accept: 'application/json', ...(method !== 'GET' && { 'Content-Type': 'application/json' }), ...headers },
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    const message = timeout.aborted
      ? 'API не ответил вовремя. Повторите запрос.'
      : 'Связь с API прервана. Проверьте подключение и повторите запрос.';
    throw Object.assign(new Error(message), {
      code: 'NETWORK_ERROR', uncertain: true, cause: error,
    });
  }
  const data = await response.json().catch(() => null);
  if (response.ok && data !== null) return data;
  const messages = {
    401: 'Для доступа требуется вход.',
    403: 'Сервер отклонил запрос. Проверьте доступ и настройку PUBLIC_ORIGIN.',
    409: 'Действие конфликтует с текущим состоянием.',
    422: 'Проверьте поля запроса.',
  };
  const details = Array.isArray(data?.detail) ? data.detail : [];
  const message = data?.message || (details.length
    ? details.map(({ loc, msg }) => loc.slice(1).join('.') + ' — ' + msg).join(' · ')
    : messages[response.status] || (response.status >= 500 || response.ok
      ? 'API временно недоступен. Повторите запрос.'
      : 'Сервер отклонил запрос.'));
  throw Object.assign(new Error(message), {
    status: response.status, code: data?.code, details, data,
    uncertain: response.status >= 500 || response.ok,
  });
}

export function usePoll(load, dependencies = [], { interval = 3500, enabled = true, until } = {}) {
  const key = JSON.stringify(dependencies);
  const [state, setState] = useState({ key, data: undefined, error: null });
  const [generation, setGeneration] = useState(0);
  const loadRef = useRef(load);
  const untilRef = useRef(until);
  loadRef.current = load;
  untilRef.current = until;
  const refresh = useCallback(() => setGeneration((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer;
    async function tick() {
      let complete = false;
      try {
        const data = await loadRef.current(controller.signal);
        if (controller.signal.aborted) return;
        complete = Boolean(untilRef.current?.(data));
        setState({ key, data, error: null });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState((previous) => ({ key, data: previous.key === key ? previous.data : undefined, error }));
      }
      if (!controller.signal.aborted && interval && !complete) timer = setTimeout(tick, interval);
    }
    tick();
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, generation, enabled, interval]);

  const active = state.key === key ? state : {};
  return { data: active.data, error: active.error ?? null, loading: enabled && active.data === undefined && !active.error, refresh };
}

export function href(page, id) {
  return '#/' + page + (id != null ? '/' + encodeURIComponent(id) : '');
}
export function go(page, id) {
  window.location.hash = href(page, id);
}

export function JobLink({ id, children }) {
  if (!id) return '—';
  return <a className="job-link" href={href('job', id)} title={String(id)}>
    {typograph(children) ?? <span className="mono">{String(id).slice(0, 8)}</span>}
  </a>;
}

export function Panel({ title, subtitle, actions, children, className = '' }) {
  return <section className={'panel ' + className}>
    {(title || subtitle || actions) && <div className="panel-head">
      {title && <h2>{typograph(title)}</h2>}
      {actions && <div className="actions panel-actions">{actions}</div>}
      {subtitle && <p>{typograph(subtitle)}</p>}
    </div>}
    <div className="panel-body">{children}</div>
  </section>;
}

export function Button({ variant = 'secondary', className = '', type = 'button', children, ...props }) {
  return <button type={type} className={'button button-' + variant + ' ' + className} {...props}>{typograph(children)}</button>;
}

export function BackButton({ to, onClick }) {
  const icon = <ArrowLeft size={20} aria-hidden="true" />;
  return to
    ? <a className="button button-ghost icon-button back-button" href={to} aria-label="Назад">{icon}</a>
    : <Button variant="ghost" className="icon-button back-button" onClick={onClick} aria-label="Назад">{icon}</Button>;
}

export function RefreshButton({ onClick, label = 'Обновить' }) {
  return <Button variant="ghost" className="icon-button" onClick={onClick} aria-label={label}>
    <RefreshCw size={20} aria-hidden="true" />
  </Button>;
}

export function Status({ value }) {
  if (!value) return '—';
  return <span className={'status status-' + String(value).toLowerCase().replace(/[^a-z0-9_-]/g, '')}>
    {typograph(statuses[value] || value)}
  </span>;
}

function NumberControl({ control, id, label, describedBy }) {
  const input = useRef(null);
  const { min, max, step, disabled, readOnly, value, onChange } = control.props;
  const current = value === '' ? null : Number(value);

  function change(delta) {
    const field = input.current;
    if (step === 'any') {
      const next = (Number(field.value) || 0) + delta;
      field.value = String(Math.min(max ?? Infinity, Math.max(min ?? -Infinity, next)));
    } else {
      field.stepUp(delta);
    }
    onChange?.({ target: field, currentTarget: field });
  }

  const locked = disabled || readOnly;
  return <div className="number-control">
    {cloneElement(control, { id, ref: input, 'aria-describedby': describedBy })}
    <div className="number-steps">
      <button type="button" aria-label={`Увеличить «${label}»`} onClick={() => change(1)}
        disabled={locked || (max != null && current != null && current >= Number(max))}>
        <ChevronUp size={18} aria-hidden="true" />
      </button>
      <button type="button" aria-label={`Уменьшить «${label}»`} onClick={() => change(-1)}
        disabled={locked || (min != null && current != null && current <= Number(min))}>
        <ChevronDown size={18} aria-hidden="true" />
      </button>
    </div>
  </div>;
}

export function Field({ label, hint, children }) {
  const fieldId = useId();
  const id = isValidElement(children) ? children.props.id ?? fieldId : fieldId;
  const labelId = id + '-label';
  const describedBy = hint ? id + '-hint' : undefined;
  let control = children;
  if (isValidElement(children)) {
    if (children.type === 'select' && !children.props.multiple) {
      control = <SelectControl control={children} id={id} labelId={labelId} describedBy={describedBy} />;
    } else if (children.type === 'input' && children.props.type === 'date') {
      control = <DateControl control={children} id={id} labelId={labelId} describedBy={describedBy} />;
    } else if (children.type === 'input' && children.props.type === 'number') {
      control = <NumberControl control={children} id={id} label={label} describedBy={describedBy} />;
    } else {
      control = cloneElement(children, { id, 'aria-describedby': describedBy });
    }
  }
  return <div className="field">
    <label className="field-label" htmlFor={id} id={labelId}>{typograph(label)}</label>
    {control}
    {hint && <small id={describedBy}>{shortCopy(hint)}</small>}
  </div>;
}

export function Table({ headers, children, caption }) {
  return <div className="table-wrap"><table>
    {caption && <caption>{typograph(caption)}</caption>}
    <thead><tr>{headers.map((title, index) => <th key={index} scope="col">{typograph(title)}</th>)}</tr></thead>
    <tbody>{children}</tbody>
  </table></div>;
}

export function Empty({ children }) {
  return <div className="empty">{shortCopy(children ?? 'Здесь пока нет данных')}</div>;
}

export function Loading({ children = 'Загружаем данные…' }) {
  return <div className="empty" role="status">{typograph(children)}</div>;
}

export function ErrorText({ error, retry, stale = false }) {
  if (!error) return null;
  return <div className="error-box" role="alert">
    <div><strong>{typograph(stale ? 'Данные могут быть устаревшими' : error.status === 409 ? 'Конфликт' : 'Не удалось выполнить запрос')}</strong>
      <div>{shortCopy(error.message || String(error))}{error.code && <span className="error-code"> · {error.code}</span>}</div>
    </div>
    {retry && <Button variant="ghost" onClick={retry}>Повторить</Button>}
  </div>;
}

export function QueryState({ query, children }) {
  return <>
    <ErrorText error={query.error} stale={query.data != null} retry={query.refresh} />
    {query.loading && <Loading />}
    {query.data != null && children(query.data)}
  </>;
}

export function Scope({ scope }) {
  if (!scope) return '—';
  return scope.type === 'GLOBAL' ? 'Глобальная' : 'На\u00a0' + formatDate(scope.date);
}

export function Value({ value, digits }) {
  if (value == null) return '—';
  if (Array.isArray(value)) return value.map((item) => formatNumber(item, digits)).join(', ');
  if (typeof value === 'object') return <dl className="value-list">
    {Object.entries(value).map(([key, part]) => <div key={key}><dt className="mono">{key}</dt><dd><Value value={part} /></dd></div>)}
  </dl>;
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  const formatted = formatNumber(value, digits);
  return digits == null ? formatted : formatted.replace(',', '.');
}

export function processName(reference, id) {
  return typograph(reference.processes.find((process) => process.processId === id)?.name) || id;
}

export function useWrite() {
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(null);

  async function run(intent, retry = false) {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    setFailure(null);
    let key = intent.key;
    try {
      if (!retry && intent.idempotent) {
        key = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join('');
      }
      const result = await api(intent.path, {
        method: intent.method || 'POST', body: intent.body,
        headers: key ? { 'Idempotency-Key': key } : undefined,
      });
      if (mounted.current) intent.onSuccess?.(result);
    } catch (error) {
      if (!mounted.current) return;
      setFailure({ error, intent: { ...intent, key } });
      intent.onFailure?.(error);
    } finally {
      lock.current = false;
      if (mounted.current) setPending(false);
    }
  }
  return {
    run, pending, failure,
    retry: failure?.error.uncertain && failure.intent.key ? () => run(failure.intent, true) : undefined,
  };
}
