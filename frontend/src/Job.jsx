import { useRef, useState } from 'react';
import { typograph } from './typography.js';
import {
  api, BackButton, Button, Empty, ErrorText, formatDate, go, href, JobLink, modes, Panel,
  processName, QueryState, RefreshButton, Scope, sources, Status, Table, terminal, usePoll, useWrite, Value,
} from './ui.jsx';
import { reasonText } from './Workspace.jsx';

const eventNames = {
  JOB_QUEUED: 'Поставлена в очередь', JOB_WAITING: 'Начала ожидать',
  JOB_WAIT_REASON_CHANGED: 'Причина ожидания изменилась', JOB_RESERVED: 'Ресурсы зарезервированы',
  JOB_STARTED: 'Расчёт начался', JOB_RETRY_SCHEDULED: 'Назначен повтор', JOB_SUCCEEDED: 'Расчёт завершён',
  JOB_FAILED: 'Ошибка расчёта', JOB_CANCELLED: 'Задача отменена',
};
const coefficientStatuses = {
  SAME_REVISION: 'Одна ревизия коэффициентов', MIXED_REVISIONS: 'Ревизии коэффициентов различаются',
  UNAVAILABLE: 'Ревизии коэффициентов не определены',
};

function WaitDetails({ reason }) {
  const fairness = reason.code === 'QUEUE_FAIRNESS';
  return <>
    <p>{reasonText[reason.code] || reason.code}</p>
    {!!reason.conflicts?.length && <Table headers={['Ресурс', 'Область', 'Запрошен', fairness ? 'Режим предыдущей задачи' : 'Удерживается',
      fairness ? 'Предыдущая задача' : 'Блокирующая задача']}>
      {reason.conflicts.map((conflict, index) => <tr key={conflict.blockingJobId + conflict.resourceKey + index}>
        <td className="mono">{conflict.resourceKey}</td><td><Scope scope={conflict.scope} /></td>
        <td>{modes[conflict.requestedMode] || conflict.requestedMode}</td>
        <td>{modes[conflict.activeMode] || conflict.activeMode}</td><td><JobLink id={conflict.blockingJobId} /></td>
      </tr>)}
    </Table>}
  </>;
}

export function Job({ id, reference }) {
  const path = '/jobs/' + encodeURIComponent(id);
  const query = usePoll((signal) => api(path, { signal }), [id], { interval: 2500, until: (data) => terminal(data.status) });
  const job = query.data;
  const write = useWrite();
  const active = job && !terminal(job.status);
  const canManage = job && !job.trigger?.demoId;
  const provenance = usePoll((signal) => api(path + '/provenance', { signal }), [id], { enabled: !!job?.result, interval: 0 });
  const parent = job?.batchId ? href('batch', job.batchId)
    : job?.trigger?.demoId ? href('demo', job.trigger.demoId)
      : href(active ? (['QUEUED', 'WAITING'].includes(job.status) ? 'queue' : 'active') : 'history');

  function act(action) {
    const idempotent = action === 'restart' || action === 'replay';
    write.run({
      path: path + '/' + action, idempotent, body: {},
      onSuccess: ({ jobId }) => { if (idempotent) go('job', jobId); else query.refresh(); },
      onFailure: query.refresh,
    });
  }

  return <div className="detail-page">
    <BackButton to={parent} />
    {!job && <h1 className="page-title">Карточка задачи</h1>}
    <QueryState query={query}>{() => <div className="stack job-content">
      <header className="job-heading job-title-row">
        <h1 className="page-title">{processName(reference, job.processId)}</h1>
        <p className="mono muted breakable">{job.jobId}</p>
        <Status value={job.status} />
      </header>
      {canManage &&
        <div className="actions">
          {['QUEUED', 'WAITING'].includes(job.status) && <Button variant="danger" disabled={write.pending} onClick={() => act('cancel')}>
            Отменить
          </Button>}
          {['STARTING', 'RUNNING'].includes(job.status) && <Button variant="danger" disabled={write.pending} onClick={() => act('stop')}>
            Остановить
          </Button>}
          {!active && <Button disabled={write.pending} onClick={() => act('restart')}>Запустить снова</Button>}
          {job.status === 'SUCCEEDED' && job.hasSnapshot && <Button variant="primary" disabled={write.pending} onClick={() => act('replay')}>
            Воспроизвести по снимку
          </Button>}
        </div>}
      <ErrorText error={write.failure?.error} retry={write.retry} />
      {write.failure?.error.uncertain && !write.retry && <div className="subtle-box actions">
        {typograph('Ответ о выполнении действия не получен. Перед повтором проверьте текущий статус')}
        <RefreshButton onClick={query.refresh} label="Обновить карточку" />
      </div>}
      <dl className="facts">
        <div><dt>Расчётная дата</dt><dd>{formatDate(job.calculationDate)}</dd></div>
        <div><dt>Версия процесса</dt><dd>{job.processVersion}</dd></div>
        <div><dt>Источник</dt><dd>{typograph(sources[job.trigger?.type]) || job.trigger?.type || '—'}</dd></div>
        <div><dt>Инициатор</dt><dd>{job.trigger?.initiatorId || '—'}</dd></div>
        <div><dt>Приоритет</dt><dd>{job.priority}</dd></div>
        <div><dt>Поставлена</dt><dd>{formatDate(job.queuedAt)}</dd></div>
        <div><dt>Старт</dt><dd>{formatDate(job.startedAt)}</dd></div>
        <div><dt>Завершение</dt><dd>{formatDate(job.finishedAt)}</dd></div>
        <div><dt>Попытка</dt><dd>{job.attempt ?? '—'}</dd></div>
        {job.retryAt && <div><dt>Время повтора</dt><dd>{formatDate(job.retryAt)}</dd></div>}
        {job.trigger?.scheduledFor && <div><dt>Плановый запуск</dt><dd>{formatDate(job.trigger.scheduledFor)}</dd></div>}
        {job.trigger?.restartedFrom && <div><dt>Перезапуск задачи</dt><dd><JobLink id={job.trigger.restartedFrom} /></dd></div>}
        {job.replayOf && <div><dt>Воспроизведение задачи</dt><dd><JobLink id={job.replayOf} /></dd></div>}
        {job.batchId && <div><dt>Серия расчётов</dt><dd><a href={href('batch', job.batchId)}>{job.batchId.slice(0, 8)}</a></dd></div>}
        {job.trigger?.demoId && <div><dt>Демонстрация</dt><dd><a href={href('demo', job.trigger.demoId)}>{job.trigger.demoId.slice(0, 8)}</a></dd></div>}
      </dl>
      {job.waitReason && <Panel title="Почему задача ждёт" className="waiting-panel">
        <WaitDetails reason={job.waitReason} />
      </Panel>}
      {job.error && <ErrorText error={job.error} />}
      {job.result && <Panel title="Результат" className="job-result">
        <Table headers={['Ресурс', 'Область', 'Ревизия', 'Значение']}>
          {job.result.outputs.map((output) => <tr key={output.resourceKey + (output.scope.date || '')}>
            <td className="mono">{output.resourceKey}</td><td><Scope scope={output.scope} /></td>
            <td>{output.revision}</td><td className="output-value"><Value value={output.value} digits={2} /></td>
          </tr>)}
        </Table>
      </Panel>}
      {job.result && <Panel title="Происхождение результата">
        <QueryState query={provenance}>{(data) => <>
          {coefficientStatuses[data.coefficientStatus] && <p className="subtle-box">{typograph(coefficientStatuses[data.coefficientStatus])}</p>}
          {data.inputs.length ? <Table headers={['Входной ресурс', 'Область', 'Ревизия', 'Задача-источник', 'Коэффициенты']}>
            {data.inputs.map((input) => <tr key={input.resourceKey + (input.scope.date || '')}>
              <td className="mono">{input.resourceKey}</td><td><Scope scope={input.scope} /></td><td>{input.revision}</td>
              <td><JobLink id={input.sourceJobId}>{input.sourceProcessId ? processName(reference, input.sourceProcessId) : undefined}</JobLink></td>
              <td>{input.coefficients ? <>
                <div className="mono">{input.coefficients.resourceKey}</div>
                <div>Ревизия {input.coefficients.revision} {input.coefficients.sourceJobId && <JobLink id={input.coefficients.sourceJobId} />}</div>
              </> : '—'}</td>
            </tr>)}
          </Table> : <Empty>Входных ревизий нет.</Empty>}
        </>}</QueryState>
      </Panel>}
      {job.hasSnapshot && <Snapshot id={id} path={path} />}
      <JobEvents id={id} finished={terminal(job.status)} />
    </div>}</QueryState>
  </div>;
}

function Snapshot({ id, path }) {
  const [open, setOpen] = useState(false);
  const query = usePoll((signal) => api(path + '/snapshot', { signal }), [id], { enabled: open, interval: 0 });
  return <Panel title="Сохранённый снимок входов">
    {!open && <Button onClick={() => setOpen(true)}>Показать снимок</Button>}
    <QueryState query={query}>{(snapshot) => <>
      <dl className="facts">
        <div><dt>Обработчик</dt><dd>{snapshot.handler}</dd></div>
        <div><dt>Версия обработчика</dt><dd className="mono breakable">{snapshot.handlerVersion}</dd></div>
      </dl>
      <h3>Значения входов</h3><Value value={snapshot.inputs} />
      {!!snapshot.versions?.length && <><h3>Ревизии снимка</h3>
        <Table headers={['Ресурс', 'Область', 'Ревизия', 'Задача-источник']}>
          {snapshot.versions.map((version) => <tr key={version.resourceKey + (version.scope.date || '')}>
            <td className="mono">{version.resourceKey}</td><td><Scope scope={version.scope} /></td>
            <td>{version.revision}</td><td><JobLink id={version.sourceJobId} /></td>
          </tr>)}
        </Table></>}
      <details><summary>{typograph('Снимок в JSON')}</summary><pre>{JSON.stringify(snapshot, null, 2)}</pre></details>
    </>}</QueryState>
  </Panel>;
}

function JobEvents({ id, finished }) {
  const cache = useRef({ cursor: 0, events: new Map() });
  const query = usePoll(async (signal) => {
    let page;
    do {
      const params = new URLSearchParams({ jobId: id, afterEventId: String(cache.current.cursor), limit: '100' });
      page = await api('/events?' + params, { signal });
      if (signal.aborted) return [];
      for (const event of page.items) cache.current.events.set(event.eventId, event);
      cache.current.cursor = page.nextAfterEventId;
    } while (page.items.length === 100);
    return [...cache.current.events.values()].sort((a, b) => a.eventId - b.eventId);
  }, [id], { interval: finished ? 0 : 2500 });
  return <Panel title="История событий">
    <QueryState query={query}>{(events) => events.length ? <ol className="events">
      {events.map((event) => <li key={event.eventId}>
        <div className="event-entry">
          <div className="event-head"><strong>{typograph(eventNames[event.type]) || event.type}</strong><Status value={event.status} /></div>
          <div className="muted event-meta"><span className="mono">#{event.eventId}</span> · {formatDate(event.occurredAt)}</div>
          {event.payload?.waitReason && <WaitDetails reason={event.payload.waitReason} />}
          {event.payload?.attempt != null && <p className="muted">Попытка {event.payload.attempt}</p>}
          {event.payload?.retryAt && <p>Повтор в{'\u00a0'}{formatDate(event.payload.retryAt)}</p>}
          {Object.keys(event.payload ?? {}).length > 0 && <details><summary>Данные события</summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}
        </div>
      </li>)}
    </ol> : <Empty>Для задачи пока нет событий.</Empty>}</QueryState>
  </Panel>;
}
