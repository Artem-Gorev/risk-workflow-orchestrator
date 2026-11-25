import { useEffect, useId, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { shortCopy, typograph } from './typography.js';
import {
  api, BackButton, Button, Empty, ErrorText, Field, formatDate, go, href, JobLink, Loading,
  Panel, processName, QueryState, sources, Status, statuses, Table, terminal, usePoll, useWrite, Value,
} from './ui.jsx';

const views = {
  queue: ['QUEUED', 'WAITING'],
  active: ['STARTING', 'RUNNING'],
  history: ['SUCCEEDED', 'FAILED', 'CANCELLED'],
};
const viewNames = { queue: 'Очередь', active: 'Активные', history: 'История' };
const emptyFilters = { status: '', processId: '', calculationDate: '', triggerType: '' };
const emptyViews = { queue: 'Очередь пуста', active: 'Нет активных задач', history: 'История пока пуста' };
export const reasonText = {
  RESOURCE_CONFLICT: 'Ожидание ресурса', NO_WORKER_SLOT: 'Все места исполнителей заняты',
  QUEUE_FAIRNESS: 'Ожидание очередности',
};

function useDraft(key) {
  const [draft, setDraft] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(key) || '{}'); } catch { return {}; }
  });
  useEffect(() => {
    try { sessionStorage.setItem(key, JSON.stringify(draft)); } catch { /* session storage may be disabled */ }
  }, [draft, key]);
  return [draft, (patch) => setDraft((previous) => ({ ...previous, ...patch }))];
}

function CalendarHint({ cases }) {
  return cases?.startDate ? <>{formatDate(cases.startDate)} — {formatDate(cases.endDate)}</> : undefined;
}

function Metric({ label, value, detail, href: link }) {
  const content = <>
    <div className="metric-number">{value}</div>
    <div className="metric-label">{typograph(label)}</div>
    {detail && <div className="metric-detail">{typograph(detail)}</div>}
  </>;
  return link ? <a className="metric" href={link}>{content}</a> : <div className="metric">{content}</div>;
}

export function Dashboard({ reference }) {
  const overview = usePoll((signal) => api('/overview', { signal }));
  const counts = overview.data?.countsByStatus;
  const occupied = overview.data?.occupiedSlots;
  const slots = overview.data?.maxSlots;
  return <div className="stack overview">
    <ErrorText error={overview.error} stale={!!overview.data} retry={overview.refresh} />
    <div className="metrics" aria-busy={overview.loading}>
      <Metric label="Места исполнителей" value={occupied == null ? '—' : <>{occupied}<span> / {slots}</span></>}
        detail={occupied == null ? 'Занято / всего' : `Свободно: ${Math.max(0, slots - occupied)}`} />
      <Metric label="Задачи в очереди" value={counts ? counts.QUEUED + counts.WAITING : '—'} href={href('queue')} />
      <Metric label="Ждут ресурса" value={overview.data?.resourceWaitingCount ?? '—'} href={href('queue', 'waiting')} />
      <Metric label="Ждут исполнителя" value={overview.data?.slotWaitingCount ?? '—'} href={href('queue', 'waiting')} />
    </div>
    <LaunchArea reference={reference} />
    <Panel className="overview-status" title="Задачи по статусам" actions={overview.data?.serverTime ?
      <span className="updated-at">Обновлено {formatDate(overview.data.serverTime)}</span> : undefined}>
      {counts ? <div className="status-overview">
        {Object.entries(views).flatMap(([view, options]) => options.map((status) =>
          <a key={status} href={href(view, status.toLowerCase())}><Status value={status} /><strong>{counts[status] ?? 0}</strong></a>))}
      </div> : overview.loading ? <Loading /> : <Empty>Состояние задач пока недоступно.</Empty>}
    </Panel>
  </div>;
}

function LaunchArea({ reference }) {
  const { cases, processes } = reference;
  const active = processes.filter((process) => process.isActive);
  const [draft, setDraft] = useDraft('riskflow.launch');
  const processId = draft.processId ?? active.find((process) => process.processId === 'liquidity-risk')?.processId ?? active[0]?.processId ?? '';
  const date = draft.date ?? cases?.startDate ?? '';
  const [showPriority, setShowPriority] = useState(Boolean(draft.priority));
  const priority = draft.priority ?? '';
  const selected = active.find((process) => process.processId === processId);
  const scenario = cases?.items?.find((item) => item.calculationDate === date);
  const write = useWrite();

  function launch(event) {
    event.preventDefault();
    if (!selected || !date) return;
    write.run({
      path: '/jobs', idempotent: true,
      body: { processId, calculationDate: date, ...(priority && showPriority ? { priority: Number(priority) } : {}) },
      onSuccess: ({ jobId }) => go('job', jobId),
    });
  }

  return <div className="launch-grid">
    <Panel title="Ручной запуск">
      <form className="stack form-stack" onSubmit={launch}>
        <Field label="Процесс" hint={selected?.description}>
          <select required value={processId} onChange={(event) => setDraft({ processId: event.target.value })}>
            <option value="" disabled>Выберите процесс</option>
            {processId && !selected && <option disabled value={processId}>{processId} (недоступен)</option>}
            {active.map(({ processId: id, name }) => <option key={id} value={id}>{typograph(name)}</option>)}
          </select>
        </Field>
        <Field label="Расчётная дата" hint={<CalendarHint cases={cases} />}>
          <input type="date" required min={cases?.startDate} max={cases?.endDate}
            value={date} onChange={(event) => setDraft({ date: event.target.value })} />
        </Field>
        {showPriority && <Field label="Приоритет" hint="От 1 до 9, по умолчанию — приоритет процесса">
          <input type="number" min="1" max="9" step="1" value={priority}
            placeholder={String(selected?.priority ?? '')} onChange={(event) => setDraft({ priority: event.target.value })} />
        </Field>}
        <ErrorText error={write.failure?.error} retry={write.retry} />
        <div className="form-footer">
          <Button type="submit" variant="primary" disabled={!selected || !date || !cases || write.pending}>
            {write.pending ? 'Ставим в очередь…' : 'Запустить расчёт'}
          </Button>
          <Button variant="ghost" aria-expanded={showPriority} onClick={() => setShowPriority(!showPriority)}>
            Приоритет
          </Button>
        </div>
      </form>
    </Panel>
    <Panel title="Сценарии расчёта" className="scenario-panel">
      {cases?.items?.length ? <div className="stack form-stack">
        <Field label="Дата и сценарий">
          <select value={scenario?.calculationDate ?? ''} onChange={(event) => setDraft({ date: event.target.value })}>
            <option value="" disabled>Выберите сценарий</option>
            {cases.items.map((item) => <option key={item.calculationDate} value={item.calculationDate}>
              {formatDate(item.calculationDate)} — {typograph(item.name)}
            </option>)}
          </select>
        </Field>
        {scenario && <div className="scenario-info">
          <div className="scenario-context">
            <p>{shortCopy(scenario.description)}</p>
            {scenario.expectedErrors?.[processId]
              ? <dl className="scenario-outcome"><dt>Ожидаемая ошибка</dt><dd className="mono">{scenario.expectedErrors[processId]}</dd></dl>
              : scenario.expected?.[processId] != null && <dl className="scenario-outcome">
                <dt>Ожидаемый результат</dt><dd className="scenario-result"><Value value={scenario.expected[processId]} digits={2} /></dd>
              </dl>}
          </div>
          <div className="scenario-details">
            <details><summary>Исходные значения</summary>
              <div className="scenario-values"><Value value={scenario.inputs} /></div>
            </details>
            <details><summary>Коэффициенты</summary>
              <div className="scenario-values">
                <p className="muted">Ревизия&nbsp;{cases.factorRevision}</p>
                <Value value={cases.factors} />
              </div>
            </details>
            {Object.keys(scenario.expectedErrors ?? {}).length > 0 && <details>
              <summary>Ожидаемые ошибки</summary>
              <dl className="value-list scenario-values">{Object.entries(scenario.expectedErrors).map(([process, error]) =>
                <div key={process}><dt>{processName(reference, process)}</dt><dd className="mono">{error}</dd></div>)}</dl>
            </details>}
          </div>
        </div>}
      </div> : reference.loading ? <Loading /> : <Empty>Сценарии расчёта недоступны.</Empty>}
    </Panel>
  </div>;
}

export function Jobs({ view, initialStatus, reference }) {
  const initial = initialStatus?.toUpperCase();
  const [filters, setFilters] = useState({ ...emptyFilters, status: views[view].includes(initial) ? initial : '' });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [cursors, setCursors] = useState(['']);
  const filtersId = useId();
  const filterCount = Object.values(filters).filter(Boolean).length;
  const cursor = cursors.at(-1);
  const query = usePoll((signal) => {
    const params = new URLSearchParams({ view, limit: '20' });
    for (const [name, value] of Object.entries(filters)) if (value) params.set(name, value);
    if (cursor) params.set('cursor', cursor);
    return api('/jobs?' + params, { signal });
  }, [view, filters, cursor]);
  function change(name, value) {
    setFilters((previous) => ({ ...previous, [name]: value }));
    setCursors(['']);
  }
  function resetFilters() {
    setFilters(emptyFilters);
    setCursors(['']);
  }
  return <div className="stack jobs-screen">
    <div className="segmented" aria-label="Представление задач">
      {Object.entries(viewNames).map(([page, name]) => <a key={page} href={href(page)}
        aria-current={page === view ? 'page' : undefined}>{name}</a>)}
    </div>
    <div className="jobs-filters" data-expanded={filtersOpen}>
      <div className="jobs-filter-bar">
        <Button aria-expanded={filtersOpen} aria-controls={filtersId} onClick={() => setFiltersOpen((open) => !open)}>
          Фильтры{filterCount ? ` (${filterCount})` : ''}<ChevronDown size={17} aria-hidden="true" />
        </Button>
        {filterCount > 0 && <Button variant="ghost" onClick={resetFilters}>Сбросить</Button>}
      </div>
      <div className="filter-grid" id={filtersId} aria-label="Фильтры" role="group">
        <Field label="Статус"><select value={filters.status} onChange={(event) => change('status', event.target.value)}>
          <option value="">Все статусы</option>
          {views[view].map((status) => <option key={status} value={status}>{typograph(statuses[status])}</option>)}
        </select></Field>
        <Field label="Процесс"><select value={filters.processId} onChange={(event) => change('processId', event.target.value)}>
          <option value="">Все процессы</option>
          {reference.processes.map(({ processId, name }) => <option key={processId} value={processId}>{typograph(name)}</option>)}
        </select></Field>
        <Field label="Расчётная дата"><input type="date" value={filters.calculationDate}
          onChange={(event) => change('calculationDate', event.target.value)} /></Field>
        <Field label="Источник"><select value={filters.triggerType} onChange={(event) => change('triggerType', event.target.value)}>
          <option value="">Все источники</option>
          {Object.entries(sources).map(([code, name]) => <option key={code} value={code}>{typograph(name)}</option>)}
        </select></Field>
      </div>
    </div>
    <div>
      <QueryState query={query}>{({ items, nextCursor }) => <>
        {items.length ? <Table headers={['Процесс', 'Расчётная дата', 'Источник', 'Статус', 'Время', 'Причина ожидания']}
          mobile={{ status: 3, wide: [5] }}>
          {items.map((job) => <tr key={job.jobId}>
            <td><JobLink id={job.jobId}>{processName(reference, job.processId)}</JobLink>
              <div className="muted mono table-secondary">{job.jobId.slice(0, 8)}</div></td>
            <td>{formatDate(job.calculationDate)}</td><td>{typograph(sources[job.trigger?.type]) || '—'}</td>
            <td><Status value={job.status} /></td>
            <td>{formatDate(view === 'history' ? job.finishedAt : view === 'active' ? job.startedAt ?? job.queuedAt : job.queuedAt)}</td>
            <td className={job.waitReason ? undefined : 'record-empty'}>{reasonText[job.waitReason?.code] || job.waitReason?.code || '—'}</td>
          </tr>)}
        </Table> : <Empty>{filterCount ? 'По выбранным фильтрам задач нет' : emptyViews[view]}</Empty>}
        {(cursors.length > 1 || nextCursor) && <div className="pagination">
          <Button disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>Назад</Button>
          <span>Страница {cursors.length}</span>
          <Button disabled={!nextCursor} onClick={() => setCursors([...cursors, nextCursor])}>Далее</Button>
        </div>}
      </>}</QueryState>
    </div>
  </div>;
}

export function BatchLaunch({ reference }) {
  const { cases, revisions } = reference;
  const [draft, setDraft] = useDraft('riskflow.batch');
  const date = draft.date ?? cases?.startDate ?? '';
  const revision = draft.revision ?? (revisions[0]?.revision != null ? String(revisions[0].revision) : '');
  const selected = revisions.find((item) => String(item.revision) === revision);
  const write = useWrite();

  function start(event) {
    event.preventDefault();
    if (!date || !selected) return;
    write.run({
      path: '/batches', body: { calculationDate: date, factorRevision: Number(revision) },
      onSuccess: ({ batchId }) => go('batch', batchId),
    });
  }

  return <div className="grid-2 batch-launch">
    <Panel>
      <form className="stack form-stack" onSubmit={start}>
        <Field label="Расчётная дата" hint={<CalendarHint cases={cases} />}>
          <input type="date" required min={cases?.startDate} max={cases?.endDate}
            value={date} onChange={(event) => setDraft({ date: event.target.value })} />
        </Field>
        <Field label="Ревизия коэффициентов">
          <select required value={revision} onChange={(event) => setDraft({ revision: event.target.value })}>
            <option value="" disabled>Выберите ревизию</option>
            {revisions.map((item) => <option key={item.revision} value={item.revision}>Ревизия {item.revision}</option>)}
          </select>
        </Field>
        <ErrorText error={write.failure?.error} />
        {write.failure?.error.uncertain && <p className="muted">{shortCopy('Ответ о создании серии не получен. Проверьте активные задачи перед повторным запуском')} <a href={href('active')}>Открыть активные задачи</a></p>}
        <div><Button type="submit" variant="primary" disabled={!cases || !selected || !date || write.pending}>
          {write.pending ? 'Создаём серию…' : 'Запустить серию'}
        </Button></div>
      </form>
    </Panel>
    <Panel title="Коэффициенты">
      {selected ? <><Value value={selected.value} />
        {selected.source && <p className="muted">Задача-источник <JobLink id={selected.source} /></p>}</>
        : reference.loading ? <Loading /> : <Empty>Выберите доступную ревизию.</Empty>}
    </Panel>
  </div>;
}

export function Batch({ id, reference }) {
  const query = usePoll((signal) => api('/batches/' + encodeURIComponent(id), { signal }), [id],
    { until: (data) => terminal(data.status) });
  return <div className="detail-page">
    <BackButton to={href('batches')} />
    <QueryState query={query}>{(data) => {
      const summary = data.steps.find((step) => step.processId === 'risk-summary');
      return <div className="stack form-stack">
        <div className="detail-strip">
          <div className="detail-summary">
            <span>Дата <strong>{formatDate(data.calculationDate)}</strong></span>
            <span>Ревизия <strong>{data.factorRevision}</strong></span>
            <span className="mono breakable">{data.batchId}</span>
          </div>
          <Status value={data.status} />
        </div>
        {data.error && <ErrorText error={{ message: data.error }} />}
        {summary?.actual != null && <div className="result-banner">
          <div><span>Итог сводки</span><strong><Value value={summary.actual} digits={2} /></strong></div>
          <CheckState passed={summary.passed} />
        </div>}
        <Table headers={['Процесс', 'Статус', 'Ожидалось', 'Получено', 'Проверка', 'Задача', 'Ошибка']}
          mobile={{ status: 1, wide: [6] }}>
          {data.steps.map((step) => <tr key={step.processId}>
            <td><strong>{processName(reference, step.processId)}</strong></td><td><Status value={step.status} /></td>
            <td><Value value={step.expected} digits={2} /></td><td><Value value={step.actual} digits={2} /></td>
            <td><CheckState passed={step.passed} /></td><td><JobLink id={step.jobId} /></td>
            <td className={step.error ? undefined : 'record-empty'}>{step.error ? <>{typograph(step.error.message)} <span className="mono">({step.error.code})</span></> : '—'}</td>
          </tr>)}
        </Table>
      </div>;
    }}</QueryState>
  </div>;
}

function CheckState({ passed }) {
  if (passed == null) return '—';
  return passed ? <span className="check-label">Совпадает</span> : <span className="failed-label">Расхождение</span>;
}
