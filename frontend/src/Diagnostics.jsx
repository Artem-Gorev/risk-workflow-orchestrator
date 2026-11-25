import { useId, useRef, useState } from 'react'
import {
  api, BackButton, Button, Empty, ErrorText, Field, formatDate, formatNumber, go, href, JobLink, modes,
  Panel, QueryState, RefreshButton, Scope, Status, Table, terminal, usePoll, Value,
} from './ui.jsx'
import { typograph } from './typography.js'
import './diagnostics.css'

const processName = (processes, id) => processes.find(item => item.processId === id)?.name ?? id
const eventNames = {
  JOB_QUEUED: 'Постановка в\u00a0очередь',
  JOB_WAITING: 'Начало ожидания',
  JOB_WAIT_REASON_CHANGED: 'Причина ожидания изменилась',
  JOB_RESERVED: 'Резервирование',
  JOB_STARTED: 'Фактический старт',
  JOB_RETRY_SCHEDULED: 'Запланирован повтор',
  JOB_SUCCEEDED: 'Расчёт завершён, ресурсы освобождены',
  JOB_FAILED: 'Ошибка расчёта',
  JOB_CANCELLED: 'Задача отменена',
  JOB_TRIGGER_JOINED: 'Заявка присоединена',
}
const waitReasons = {
  NO_WORKER_SLOT: 'Все места исполнителей заняты',
  QUEUE_FAIRNESS: 'Ожидание очередности',
}
const demoRoles = [
  { letter: 'A', title: 'Кредитный риск', source: 'Ручной запуск' },
  { letter: 'B', title: 'Кредитный риск', source: 'По\u00a0расписанию' },
  { letter: 'C', title: 'Ликвидность', source: 'Ручной запуск' },
]
const percent = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', {
  style: 'percent', maximumFractionDigits: 1,
}).format(value)
const clock = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit',
})
const loadNumber = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 6 })
const loadDuration = (value, unit) => `${loadNumber.format(value)}\u00a0${unit}`
const valueLabels = {
  id: 'Задача', jobId: 'Задача', process: 'Процесс', blocker: 'Блокирующий процесс',
  jobs: 'Запуски', minutes: 'Минуты', blocked: 'Конфликты', failures: 'Ошибки',
  runtime: 'Время расчёта,\u00a0с', date: 'Дата', compatible: 'Совместимые процессы',
  resourceKey: 'Ресурс', waitSeconds: 'Ожидание,\u00a0с', shareOfWait: 'Доля ожидания', reason: 'Причина',
}

function DateField({ value, onChange, cases, required }) {
  return (
    <Field label="Расчётная дата">
      <input
        type="date"
        required={required}
        min={cases?.startDate}
        max={cases?.endDate}
        value={value}
        onChange={event => onChange(event.target.value)}
      />
    </Field>
  )
}

function ConflictList({ conflicts }) {
  if (!conflicts.length) return <Empty>Сейчас конфликтов ресурсов нет.</Empty>
  return (
    <Table headers={['Ресурс', 'Область', 'Запрошен', 'Занят', 'Блокирующая задача']} mobile={{ title: 0 }}>
      {conflicts.map((conflict, index) => (
        <tr key={`${conflict.resourceKey}-${conflict.blockingJobId}-${index}`}>
          <td className="mono">{conflict.resourceKey}</td>
          <td><Scope scope={conflict.scope} /></td>
          <td>{modes[conflict.requestedMode]} ({conflict.requestedMode})</td>
          <td>{modes[conflict.activeMode]} ({conflict.activeMode})</td>
          <td><JobLink id={conflict.blockingJobId} /></td>
        </tr>
      ))}
    </Table>
  )
}

function workloadScale(peak) {
  const multiplier = peak < 1 ? 1000 : 1
  const half = peak * multiplier / 2
  const magnitude = 10 ** Math.floor(Math.log10(half))
  const step = ([1, 2, 2.5, 5, 10].find(value => value >= half / magnitude) ?? 10) * magnitude
  return { multiplier, unit: multiplier === 1000 ? 'мс' : 'с', ticks: [2 * step, step, 0] }
}

function LoadChart({ points }) {
  if (!points.length) return <Empty>Данных о&nbsp;загрузке пока нет.</Empty>
  const minutes = points.map(point => clock.format(new Date(point.minute)))
  const activeMinutes = points.filter(point => point.busySeconds > 0).length
  return (
    <>
      {activeMinutes ? (
        <ActiveLoadChart points={points} minutes={minutes} activeMinutes={activeMinutes} />
      ) : (
        <Empty>{typograph(`С ${minutes[0]} по ${minutes.at(-1)}\u00a0МСК загрузка исполнителей составила 0\u00a0с`)}</Empty>
      )}
      <details className="diagnostic-details">
        <summary>Показания по&nbsp;минутам</summary>
        <Table headers={['Минута,\u00a0МСК', 'Суммарное время работы,\u00a0с']} mobile={false}>
          {points.map((point, index) => (
            <tr key={point.minute}><td>{minutes[index]}</td><td>{loadNumber.format(point.busySeconds)}</td></tr>
          ))}
        </Table>
      </details>
    </>
  )
}

function ActiveLoadChart({ points, minutes, activeMinutes }) {
  const end = points.length - 1
  const peak = Math.max(...points.map(point => point.busySeconds))
  const { multiplier, unit, ticks } = workloadScale(peak)
  const timeLabelCount = Math.min(5, points.length)
  const timeTicks = Array.from({ length: timeLabelCount }, (_, index) =>
    Math.round(index * end / Math.max(1, timeLabelCount - 1)),
  )
  return (
    <>
      <p className="diagnostic-load-summary muted">
        <span>Минут с&nbsp;расчётами <strong>{activeMinutes}&nbsp;из&nbsp;{points.length}</strong></span>
        <span>Пик <strong>{loadDuration(peak * multiplier, unit)}</strong></span>
      </p>
      <div className="diagnostic-load" role="img" aria-label={typograph(`Занятость исполнителей по минутам, с ${minutes[0]} по ${minutes.at(-1)}\u00a0МСК`)}>
        <div className="diagnostic-axis muted">
          {ticks.map(value => <span key={value}>{loadDuration(value, unit)}</span>)}
        </div>
        <div className="diagnostic-plot" style={{ '--load-points': points.length }}>
          <div className="diagnostic-load-guides">
            {ticks.map(value => <span key={value} />)}
          </div>
          {points.map((point, index) => (
            <div className="diagnostic-bar-slot" key={point.minute}
              title={`${minutes[index]}\u00a0МСК — ${loadDuration(point.busySeconds * multiplier, unit)}`}>
              {point.busySeconds > 0 && (
                <div className="diagnostic-bar" style={{ height: `${point.busySeconds * multiplier * 100 / ticks[0]}%` }} />
              )}
            </div>
          ))}
        </div>
        <div className="diagnostic-axis-dates muted">
          {timeTicks.map(index => (
            <span key={index} style={{ '--tick-position': `${(index + 0.5) * 100 / points.length}%` }}>
              {minutes[index]}{index === end && '\u00a0МСК'}
            </span>
          ))}
        </div>
      </div>
    </>
  )
}

function RecentEvents({ events }) {
  const [expanded, setExpanded] = useState(false)
  const listId = useId()
  return (
    <Panel title="Последние события">
      {events.length ? (
        <>
          <div className="diagnostic-recent-list" id={listId} data-expanded={expanded}>
            <Table headers={['Событие', 'Задача', 'Время']} mobile={{ title: 0 }}>
              {events.map(event => (
                <tr key={event.id}>
                  <td>{eventNames[event.type] ?? event.type}</td>
                  <td><JobLink id={event.job_id} /></td>
                  <td>{formatDate(event.occurred)}</td>
                </tr>
              ))}
            </Table>
          </div>
          {events.length > 4 && (
            <Button className="diagnostic-recent-toggle" variant="ghost"
              aria-controls={listId} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
              {expanded ? 'Свернуть события' : `Все события (${events.length})`}
            </Button>
          )}
        </>
      ) : <Empty>В&nbsp;журнале пока нет событий.</Empty>}
    </Panel>
  )
}

function Report({ processes }) {
  const query = usePoll(signal => api('/analytics', { signal }), [])
  return (
    <QueryState query={query}>
      {report => (
        <div className="diagnostic-flow">
          <dl className="diagnostic-stats">
            <div><dt>Всего задач</dt><dd>{report.totalJobs}</dd></div>
            <div><dt>Завершились ошибкой</dt><dd>{report.failedJobs}</dd></div>
            <div><dt>С&nbsp;конфликтом за&nbsp;{report.days}&nbsp;дн.</dt><dd>{report.conflictedJobs}</dd></div>
            <div><dt>Ожидание за&nbsp;{report.days}&nbsp;дн.</dt><dd>{formatNumber(report.waitSeconds)}&nbsp;с</dd></div>
          </dl>
          <Panel title="Загрузка исполнителей" subtitle="Суммарное время работы всех исполнителей по минутам" actions={
            <RefreshButton onClick={query.refresh} label="Обновить аналитику" />
          }>
            <LoadChart points={report.load} />
          </Panel>
          <div className="grid-2 diagnostic-columns">
            <Panel title="Статистика процессов" subtitle={`Запуски за\u00a0${report.days}\u00a0дн.`} className="diagnostic-processes">
              {report.processes.length ? (
                <Table headers={['Процесс', 'Запуски', 'Ошибки', 'Ждали ресурс', 'Средний расчёт,\u00a0с']} mobile={{ title: 0 }}>
                  {report.processes.map(row => (
                    <tr key={row.process}>
                      <td>{typograph(processName(processes, row.process))}</td>
                      <td>{row.jobs}</td><td>{row.failures}</td><td>{row.blocked}</td><td>{formatNumber(row.runtime)}</td>
                    </tr>
                  ))}
                </Table>
              ) : <Empty>За&nbsp;этот период запусков не&nbsp;было.</Empty>}
            </Panel>
            <Panel title="Ожидание по ресурсам" subtitle={`Наблюдения за\u00a0${report.days}\u00a0дн.`}>
              {report.resources.length ? (
                <Table headers={['Ресурс', 'Ожидание,\u00a0с', 'Доля времени ожидания']} mobile={{ title: 0 }}>
                  {report.resources.map(row => (
                    <tr key={row.resourceKey}>
                      <td className="mono">{row.resourceKey}</td><td>{formatNumber(row.waitSeconds)}</td><td>{percent(row.shareOfWait)}</td>
                    </tr>
                  ))}
                </Table>
              ) : <Empty>Ожидание ресурсов за&nbsp;этот период не&nbsp;зафиксировано.</Empty>}
            </Panel>
          </div>
          <RecentEvents events={report.recentEvents} />
        </div>
      )}
    </QueryState>
  )
}

function Forecast({ processes, cases }) {
  const [process, setProcess] = useState('')
  const [day, setDay] = useState(null)
  const [request, setRequest] = useState(null)
  const processId = process || processes[0]?.processId || ''
  const calculationDate = day ?? cases?.startDate ?? ''
  const query = usePoll(signal => api(`/analytics/forecast?${new URLSearchParams(request)}`, { signal }), [request], {
    enabled: Boolean(request),
  })

  function showForecast(event) {
    event.preventDefault()
    setRequest({ processId, calculationDate })
  }

  return (
    <Panel title="Прогноз конфликтов">
      <form onSubmit={showForecast} className="stack diagnostic-form">
        <div className="form-grid">
          <Field label="Процесс">
            <select required value={processId} onChange={event => setProcess(event.target.value)}>
              {!processes.length && <option value="">Список процессов пуст</option>}
              {processes.map(item => (
                <option value={item.processId} key={item.processId}>
                  {typograph(item.name)}{!item.isActive && ' (выключен)'}
                </option>
              ))}
            </select>
          </Field>
          <DateField value={calculationDate} onChange={setDay} cases={cases} required />
        </div>
        <div className="actions"><Button type="submit" disabled={!processId || !calculationDate}>Показать прогноз</Button></div>
      </form>
      {request && (
        <div className="stack diagnostic-section">
          {(request.processId !== processId || request.calculationDate !== calculationDate) && (
            <p className="muted">{typograph(processName(processes, request.processId))} за&nbsp;{formatDate(request.calculationDate)}</p>
          )}
          <QueryState query={query}>
            {forecast => (
              <>
                <dl className="diagnostic-meta">
                  <div><dt>Запуски в&nbsp;выборке</dt><dd>{forecast.samples}</dd></div>
                  <div><dt>Частота конфликтов</dt><dd>{forecast.historicalConflictRate == null ? 'Нет данных' : percent(forecast.historicalConflictRate)}</dd></div>
                </dl>
                <ConflictList conflicts={forecast.conflictsNow} />
              </>
            )}
          </QueryState>
        </div>
      )}
    </Panel>
  )
}

function EvidenceValue({ name, value }) {
  if (value == null) return '—'
  if ((name === 'jobId' || name === 'id') && typeof value === 'string') return <JobLink id={value} />
  if (name === 'shareOfWait') return percent(value)
  if (typeof value === 'number') return formatNumber(value)
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет'
  if (typeof value === 'string') return typograph(value)
  if (name === 'reason') {
    if (value.conflicts?.length) return <ConflictList conflicts={value.conflicts} />
    return waitReasons[value.code] ?? value.code
  }
  if (Array.isArray(value) && value.every(item => typeof item === 'string')) {
    return value.length ? value.join(', ') : 'Нет'
  }
  return <pre className="diagnostic-evidence">{JSON.stringify(value, null, 2)}</pre>
}

function Evidence({ evidence }) {
  const rows = Array.isArray(evidence) ? evidence : Object.keys(evidence).length ? [evidence] : []
  if (!rows.length) return <Empty>Подтверждающих записей нет.</Empty>
  return (
    <div>
      {rows.map((row, index) => (
        <dl className="diagnostic-meta diagnostic-evidence-row" key={row.id ?? row.jobId ?? row.process ?? row.resourceKey ?? index}>
          {Object.entries(row).map(([name, value]) => (
            <div key={name}><dt>{valueLabels[name] ?? name}</dt><dd><EvidenceValue name={name} value={value} /></dd></div>
          ))}
        </dl>
      ))}
    </div>
  )
}

function Ask({ cases }) {
  const [question, setQuestion] = useState('')
  const [jobId, setJobId] = useState('')
  const [calculationDate, setCalculationDate] = useState('')
  const [reply, setReply] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const busy = useRef(false)

  async function ask(event) {
    event.preventDefault()
    if (busy.current || !question.trim()) return
    busy.current = true
    setPending(true)
    setError(null)
    const body = {
      question: question.trim(),
      ...(jobId.trim() ? { jobId: jobId.trim() } : {}),
      ...(calculationDate ? { calculationDate } : {}),
    }
    try {
      setReply({ ...await api('/analytics/ask', { method: 'POST', body }), question: body.question })
    } catch (failure) {
      setError(failure)
    } finally {
      busy.current = false
      setPending(false)
    }
  }

  return (
    <Panel title="Вопрос по данным">
      <form onSubmit={ask} className="stack diagnostic-form">
        <Field label="Вопрос">
          <textarea required maxLength={500} rows={3} value={question} onChange={event => setQuestion(event.target.value)} placeholder="Почему задача ждёт?" />
        </Field>
        <div className="form-grid">
          <Field label="ID задачи" hint="Необязательно">
            <input value={jobId} onChange={event => setJobId(event.target.value)} />
          </Field>
          <DateField value={calculationDate} onChange={setCalculationDate} cases={cases} />
        </div>
        <div className="actions"><Button type="submit" disabled={pending || !question.trim()}>{pending ? 'Запрашиваем…' : 'Получить ответ'}</Button></div>
      </form>
      {error && <ErrorText error={error} />}
      {reply && (
        <div className="diagnostic-section stack" aria-live="polite">
          {reply.question !== question.trim() && <p className="muted">«{typograph(reply.question)}»</p>}
          <p>{typograph(reply.answer)}</p>
          <h3>Данные ответа</h3>
          <Evidence evidence={reply.evidence} />
        </div>
      )}
    </Panel>
  )
}

function Analytics({ reference }) {
  return (
    <div className="diagnostic-flow">
      <Report processes={reference.processes} />
      <div className="grid-2 diagnostic-columns">
        <Forecast processes={reference.processes} cases={reference.cases} />
        <Ask cases={reference.cases} />
      </div>
    </div>
  )
}

async function readEventHistory(jobId, previous, signal) {
  const items = new Map(previous?.items)
  let afterEventId = previous?.cursor ?? 0
  const limit = 100
  for (;;) {
    const params = new URLSearchParams({ jobId, afterEventId, limit })
    const page = await api(`/events?${params}`, { signal })
    page.items.forEach(event => items.set(event.eventId, event))
    if (page.items.length < limit || page.nextAfterEventId <= afterEventId) {
      return { items, cursor: page.nextAfterEventId }
    }
    afterEventId = page.nextAfterEventId
  }
}

function useDemo(id) {
  const eventCache = useRef(null)
  return usePoll(async signal => {
    const prior = eventCache.current?.id === id ? eventCache.current.histories : null
    const batch = await api(`/demo/${encodeURIComponent(id)}`, { signal })
    const historyEntries = await Promise.all(batch.items.map(async job => [
      job.jobId, await readEventHistory(job.jobId, prior?.get(job.jobId), signal),
    ]))
    const events = historyEntries.flatMap(([, history]) => [...history.items.values()])
      .sort((left, right) => left.eventId - right.eventId)
    if (!signal.aborted) eventCache.current = { id, histories: new Map(historyEntries) }
    return { batch, events }
  }, [id], {
    interval: 2500,
    enabled: Boolean(id),
    until: ({ batch }) => batch.status !== 'PENDING' && batch.items.every(job => terminal(job.status)),
  })
}

const demoStoreKey = 'riskflow:lastDemo'
function lastDemo() {
  try { return sessionStorage.getItem(demoStoreKey) } catch { return null }
}

function rememberDemo(id) {
  try { sessionStorage.setItem(demoStoreKey, id) } catch { /* navigation still opens the result */ }
}

function DemoLaunch({ id, active }) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [uncertain, setUncertain] = useState(false)
  const busy = useRef(false)
  const recent = lastDemo()

  async function launch() {
    if (busy.current || uncertain) return
    busy.current = true
    setPending(true)
    setError(null)
    const originHash = location.hash
    try {
      const result = await api('/demo', { method: 'POST', body: {} })
      rememberDemo(result.demoId)
      if (location.hash === originHash) go('demo', result.demoId)
    } catch (failure) {
      setError(failure)
      setUncertain(Boolean(failure.uncertain))
    } finally {
      busy.current = false
      setPending(false)
    }
  }

  async function checkConnection() {
    if (busy.current) return
    busy.current = true
    setPending(true)
    try {
      await api('/overview')
      setUncertain(false)
      setError(null)
    } catch (failure) {
      setError(failure)
    } finally {
      busy.current = false
      setPending(false)
    }
  }

  return (
    <div className="diagnostic-controls">
      {!id && <p>{typograph('Два кредитных расчёта на одной дате и независимый расчёт ликвидности')}</p>}
      <div className="actions">
        <Button variant="primary" disabled={pending || uncertain || active} onClick={launch}>
          {pending && !uncertain ? 'Запускаем…' : 'Запустить демонстрацию'}
        </Button>
        {recent && recent !== id && <a href={href('demo', recent)}>Последняя демонстрация</a>}
      </div>
      {error && <ErrorText error={error} />}
      {uncertain && (
        <div className="diagnostic-section stack">
          <p>{typograph('Ответ о запуске потерян. Проверьте связь и последнюю доступную демонстрацию перед повтором. Если запуск продолжается, сервер сообщит о занятой демо-очереди.')}</p>
          <div className="actions"><Button onClick={checkConnection} disabled={pending}>Проверить связь</Button></div>
        </div>
      )}
    </div>
  )
}

function DemoJobs({ batch }) {
  return (
    <div className="diagnostic-demo-jobs">
      <Table headers={['Задача', 'Источник', 'Статус', 'Результат']} mobile={{ title: 0, status: 2 }}>
        {batch.jobs.map((jobId, index) => {
          const job = batch.items.find(item => item.jobId === jobId)
          const role = demoRoles[index]
          const label = `${role.letter} — ${role.title}`
          return (
            <tr key={jobId}>
              <th scope="row">{job ? <JobLink id={jobId}>{label}</JobLink> : label}</th>
              <td>{role.source}</td>
              <td>{job ? <Status value={job.status} /> : batch.status === 'FAILED' ? 'Задача не\u00a0поступила' : 'Ожидает постановки'}</td>
              <td>
                {job?.error && <ErrorText error={job.error} />}
                {job?.result?.outputs.map(output => (
                  <div className="diagnostic-output" key={output.resourceKey}>
                    <span className="mono muted">{output.resourceKey}</span>
                    <div className="diagnostic-output-value"><Value value={output.value} digits={2} /></div>
                  </div>
                ))}
                {!job?.error && !job?.result && '—'}
              </td>
            </tr>
          )
        })}
      </Table>
    </div>
  )
}

function DemoTimeline({ jobs, events }) {
  if (!events.length) return <Empty>События появятся после постановки задач.</Empty>
  const seconds = value => Date.parse(value) / 1000
  const min = Math.min(...events.map(event => seconds(event.occurredAt)))
  const max = Math.max(...events.map(event => seconds(event.occurredAt)))
  const domain = Math.max(max - min, 1)
  const place = time => `${(time - min) * 100 / domain}%`
  const lanes = jobs.map((jobId, index) => {
    const jobEvents = events.filter(event => event.jobId === jobId)
    const bars = []
    let phase
    for (const event of jobEvents) {
      const at = seconds(event.occurredAt)
      if (phase && (event.type === 'JOB_RESERVED' || event.type === 'JOB_RETRY_SCHEDULED' || terminal(event.status))) {
        bars.push({ ...phase, to: at })
        phase = null
      }
      if (event.type === 'JOB_WAITING' && !phase) phase = { type: 'waiting', from: at }
      if (event.type === 'JOB_STARTED') phase = { type: 'running', from: at }
    }
    if (phase) bars.push({ ...phase, to: max })
    return { role: demoRoles[index], bars, jobEvents }
  })

  return (
    <div className="diagnostic-timeline">
      <div className="diagnostic-legend muted">
        <span><i className="diagnostic-waiting" /> Ожидание</span>
        <span><i className="diagnostic-running" /> Выполнение</span>
        <span><i className="diagnostic-marker" /> Событие</span>
      </div>
      <div className="diagnostic-lanes" aria-hidden="true">
        {lanes.map(({ role, bars, jobEvents }) => (
          <div className="diagnostic-lane" key={role.letter}>
            <span>{role.letter}</span>
            <div className="diagnostic-track">
              {bars.map((bar, index) => (
                <div
                  className={`diagnostic-segment diagnostic-${bar.type}`}
                  key={index}
                  style={{ left: place(bar.from), width: `${Math.max(0, bar.to - bar.from) * 100 / domain}%` }}
                />
              ))}
              {jobEvents.map(event => (
                <div
                  key={event.eventId}
                  className="diagnostic-point diagnostic-marker"
                  title={`Событие №\u00a0${event.eventId} — ${eventNames[event.type] ?? event.type}`}
                  style={{ left: place(seconds(event.occurredAt)) }}
                />
              ))}
            </div>
          </div>
        ))}
        <div className="diagnostic-lane">
          <span />
          <div className="diagnostic-timeline-scale muted">
            <span>0&nbsp;с</span><span>{formatNumber(domain / 2)}&nbsp;с</span><span>{formatNumber(domain)}&nbsp;с</span>
          </div>
        </div>
      </div>
      <p className="muted">Начало отсчёта {formatDate(events[0].occurredAt)}</p>
    </div>
  )
}

function DemoEvidence({ batch, events }) {
  const [first, second, independent] = batch.jobs
  const waiting = events.find(event => event.jobId === second &&
    event.payload.waitReason?.conflicts?.some(conflict => conflict.blockingJobId === first))
  const independentStart = events.find(event => event.jobId === independent && event.type === 'JOB_STARTED')
  const firstFinish = events.find(event => event.jobId === first && terminal(event.status))
  const secondReserve = events.find(event => event.jobId === second && event.type === 'JOB_RESERVED')
  const milestones = [
    [waiting, 'B ждёт A'],
    [independentStart, 'C стартовала'],
    [firstFinish, 'A завершилась'],
    [secondReserve, 'B получила ресурс'],
  ].filter(([event]) => event).sort(([left], [right]) => left.eventId - right.eventId)
  return (
    <>
      {milestones.length > 0 && (
        <ol className="diagnostic-milestones" aria-label="Ключевые события демонстрации">
          {milestones.map(([event, text]) => (
            <li key={event.eventId}><strong>{text}</strong><span className="muted">Событие №&nbsp;{event.eventId}</span></li>
          ))}
        </ol>
      )}
      {waiting && (
        <div className="stack diagnostic-section">
          <p>Задача&nbsp;<JobLink id={second}>B</JobLink> ожидала освобождения ресурса задачей&nbsp;<JobLink id={first}>A</JobLink>.</p>
          <ConflictList conflicts={waiting.payload.waitReason.conflicts} />
        </div>
      )}
    </>
  )
}

function Demo({ id }) {
  const query = useDemo(id)
  const batch = query.data?.batch
  const active = query.loading || Boolean(batch && (batch.status === 'PENDING' || batch.items.some(job => !terminal(job.status))))
  return (
    <div className="diagnostic-flow">
      <div className="detail-page">
        {id && <BackButton to={href('demo')} />}
        <DemoLaunch id={id} active={active} />
      </div>
      {id && (
        <QueryState query={query}>
          {({ batch, events }) => (
            <>
              <div className="diagnostic-demo-summary">
                <dl className="diagnostic-meta diagnostic-demo-meta">
                  <div><dt>ID демонстрации</dt><dd className="mono">{batch.demoId}</dd></div>
                  <div><dt>Доставка по&nbsp;расписанию</dt><dd>{{
                    PENDING: 'Ожидается',
                    DELIVERED: 'Доставлена',
                    FAILED: 'Ошибка доставки',
                  }[batch.status] ?? batch.status}</dd></div>
                  {batch.scheduleSlot && (
                    <>
                      <div><dt>Плановое время</dt><dd>{formatDate(batch.scheduleSlot.planned)}</dd></div>
                      <div><dt>Фактическое время</dt><dd>{formatDate(batch.scheduleSlot.observed)}</dd></div>
                    </>
                  )}
                </dl>
                {batch.error && <ErrorText error={new Error(batch.error)} />}
                <DemoJobs batch={batch} />
              </div>
              <Panel title="Общая шкала времени">
                <DemoTimeline jobs={batch.jobs} events={events} />
                <DemoEvidence batch={batch} events={events} />
              </Panel>
              <Panel title="Журнал демонстрации" className="diagnostic-demo-log">
                {events.length ? (
                  <Table headers={['№', 'Время', 'Задача', 'Событие', 'Статус']}
                    mobile={{ title: 3, status: 4, wide: [1], labels: ['№ события', 'Время', 'Задача', 'Событие', 'Статус'] }}>
                    {events.map(event => (
                      <tr key={event.eventId}>
                        <td className="mono">{event.eventId}</td>
                        <td>{formatDate(event.occurredAt)}</td>
                        <td><JobLink id={event.jobId}>{demoRoles[batch.jobs.indexOf(event.jobId)]?.letter ?? 'Задача'}</JobLink></td>
                        <td>
                          {eventNames[event.type] ?? event.type}
                          {event.payload.waitReason && (
                            <div className="muted">{event.payload.waitReason.conflicts?.length
                              ? event.payload.waitReason.conflicts.map(conflict => conflict.resourceKey).join(', ')
                              : waitReasons[event.payload.waitReason.code] ?? event.payload.waitReason.code}</div>
                          )}
                        </td>
                        <td><Status value={event.status} /></td>
                      </tr>
                    ))}
                  </Table>
                ) : <Empty>Журнал пока пуст.</Empty>}
              </Panel>
            </>
          )}
        </QueryState>
      )}
    </div>
  )
}

const policies = [
  ['fifo', 'FIFO (строгая очередь)'],
  ['managed', 'RiskFlow (управление ресурсами)'],
  ['unmanaged', 'Без управления ресурсами'],
]

function Simulation() {
  const [values, setValues] = useState({ seed: '42', count: '40', slots: '2' })
  const [request, setRequest] = useState(values)
  const query = usePoll(signal => api(`/simulation?${new URLSearchParams(request)}`, { signal }), [request], { interval: 0 })
  const fields = [
    { key: 'seed', label: 'Зерно генератора' },
    { key: 'count', label: 'Число задач', min: 2, max: 200 },
    { key: 'slots', label: 'Места исполнителей', min: 1, max: 20 },
  ]
  return (
    <Panel title="Симуляция">
      <form className="stack diagnostic-form diagnostic-simulation-form" onSubmit={event => { event.preventDefault(); setRequest({ ...values }) }}>
        <div className="form-grid">
          {fields.map(({ key, label, min, max }) => (
            <Field key={key} label={label}>
              <input
                type="number"
                required
                step={1}
                min={min}
                max={max}
                value={values[key]}
                onChange={event => setValues({ ...values, [key]: event.target.value })}
              />
            </Field>
          ))}
        </div>
        <div className="actions"><Button type="submit" variant="primary">Сравнить политики</Button></div>
      </form>
      <div className="stack diagnostic-section">
        <QueryState query={query}>
          {result => (
            <>
              {fields.some(({ key }) => values[key] !== request[key]) && (
                <p className="muted">Зерно {result.seed}, задач {result.requests.length}, мест {request.slots}</p>
              )}
              <Table headers={['Политика', 'Длительность, такты', 'Суммарное ожидание, такты', '95-й процентиль ожидания, такты', 'Конфликтующие пары']}
                mobile={{ title: 0 }}>
                {policies.map(([key, label]) => (
                  <tr key={key}>
                    <th scope="row">{label}</th>
                    <td>{result[key].durationTicks}</td>
                    <td>{result[key].queueTicks}</td>
                    <td>{result[key].p95WaitTicks}</td>
                    <td>{result[key].conflictingPairs}</td>
                  </tr>
                ))}
              </Table>
            </>
          )}
        </QueryState>
      </div>
    </Panel>
  )
}

function Benchmarks() {
  const query = usePoll(signal => api('/benchmarks?compact=true', { signal }), [])
  return (
    <Panel title="Измерения на PostgreSQL" actions={
      <RefreshButton onClick={query.refresh} label="Обновить измерения" />
    }>
      <QueryState query={query}>
        {({ items }) => items.length ? (
          <div className="stack">
            {items.map(result => (
              <div key={result.id} className="stack">
                <p className="muted">
                  {formatDate(result.created)}, задач {result.report.workload.length}, мест {result.report.slots}, базовая задержка {formatNumber(result.report.delaySeconds)}&nbsp;с
                </p>
                <Table headers={['Прогон', 'Политика', 'Длительность,\u00a0с', '95-й процентиль ожидания,\u00a0с', 'Конфликтующие пары']}
                  mobile={{ title: 1 }}>
                  {result.report.pairs.flatMap((pair, index) => Object.entries(pair).map(([policy, metrics]) => (
                    <tr key={`${index}-${policy}`}>
                      <td>{index + 1}</td>
                      <th scope="row">{policy === 'fifo' ? 'FIFO' : 'RiskFlow'}</th>
                      <td>{formatNumber(metrics.makespanSeconds)}</td>
                      <td>{formatNumber(metrics.p95WaitSeconds)}</td>
                      <td>{metrics.conflictingPairs}</td>
                    </tr>
                  )))}
                </Table>
              </div>
            ))}
          </div>
        ) : <Empty>Измерения появятся после первого эксперимента на&nbsp;стенде.</Empty>}
      </QueryState>
    </Panel>
  )
}

export function Diagnostics({ page, id, reference }) {
  if (page === 'analytics') return <Analytics reference={reference} />
  if (page === 'demo') return <Demo id={id} />
  return <div className="diagnostic-flow"><Simulation /><Benchmarks /></div>
}
