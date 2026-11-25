import { useRef, useState } from 'react';
import { api, BackButton, Button, Empty, ErrorText, Field, formatDate, JobLink, Loading, Panel, RefreshButton, Table, usePoll } from './ui.jsx';
import { typograph } from './typography.js';
import './management.css';

const codePattern = String.raw`[a-z][a-z0-9_\-]{0,63}`;
const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const weekdayLabels = ['понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям'];
const auditLabels = {
  PROCESS_SAVED: 'Сохранена версия процесса',
  SCHEDULE_SAVED: 'Сохранено расписание',
  RESOURCE_CREATED: 'Создан ресурс',
};
const deliveries = { DELIVERED: 'Доставлено', MISSED: 'Пропущено', REJECTED: 'Отклонено' };

function scopeLabel(scope) {
  if (scope === 'GLOBAL' || scope?.type === 'GLOBAL') return 'Глобальная область';
  if (scope?.date) return `Дата ${formatDate(scope.date)}`;
  return 'По\u00a0расчётной дате';
}

function modeLabel(mode) {
  return mode === 'WRITE' ? 'Запись (WRITE)' : 'Чтение (READ)';
}

function ManagementHeading({ title, compact, children }) {
  return (
    <div className={'page-heading' + (compact ? '' : ' management-heading-with-button')}>
      <h1 className="page-title">{title}</h1>
      {children}
    </div>
  );
}

function ManagementDate({ value }) {
  return value ? <time className="management-time" dateTime={value}>{formatDate(value)}</time> : '—';
}

function ResourceState({ active, children }) {
  return <span className="management-resource-state" data-active={active || undefined}>{children}</span>;
}

function LoadState({ poll }) {
  return (
    <>
      {poll.loading && <Loading />}
      <ErrorText error={poll.error} retry={poll.refresh} stale={poll.data != null} />
    </>
  );
}

function useSave() {
  const pendingRef = useRef(false);
  const [state, setState] = useState({ pending: false, error: null, uncertain: false, message: '' });

  async function execute(request, success, verify = false) {
    if (pendingRef.current || (state.uncertain && !verify)) return;
    pendingRef.current = true;
    setState({ pending: true, error: null, uncertain: state.uncertain, message: '' });
    try {
      const result = await request();
      setState({ pending: false, error: null, uncertain: false, message: success(result) });
    } catch (error) {
      setState({
        pending: false,
        error,
        uncertain: verify ? state.uncertain : Boolean(error.uncertain),
        message: '',
      });
    } finally {
      pendingRef.current = false;
    }
  }

  return { ...state, execute };
}

function SaveState({ save, verify }) {
  return (
    <>
      {save.error && <ErrorText error={save.error} />}
      {save.message && <p role="status">{typograph(save.message)}</p>}
      {save.uncertain && (
        <div className="stack">
          <p>{typograph('Проверьте, было\u00a0ли сохранено изменение, прежде\u00a0чем отправлять запрос ещё\u00a0раз')}</p>
          <div className="actions">
            <Button type="button" onClick={verify} disabled={save.pending}>Проверить состояние</Button>
          </div>
        </div>
      )}
    </>
  );
}

function blankProcess() {
  return {
    code: '',
    handler: '',
    name: '',
    description: '',
    priority: 5,
    executionSeconds: 0,
    isActive: true,
    joinActive: false,
    bindings: {},
    expectedVersion: 0,
  };
}

function processDraft(process) {
  return {
    code: process.processId,
    handler: process.handler,
    name: process.name,
    description: process.description,
    priority: process.priority,
    executionSeconds: process.executionSeconds,
    isActive: process.isActive,
    joinActive: process.joinActive,
    bindings: { ...process.bindings },
    expectedVersion: process.processVersion,
  };
}

function ProcessEditor({ process, catalog, resources, onSaved }) {
  const [draft, setDraft] = useState(() => process ? processDraft(process) : blankProcess());
  const [latest, setLatest] = useState(null);
  const [revisionConflict, setRevisionConflict] = useState(false);
  const save = useSave();
  const handler = catalog.find((item) => item.processId === draft.handler);

  function set(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function verify() {
    const wasConflict = revisionConflict;
    save.execute(() => api('/processes'), ({ items }) => {
      const fresh = items.find((item) => item.processId === draft.code);
      setLatest(fresh ?? null);
      setRevisionConflict(false);
      if (fresh) {
        return wasConflict
          ? `Загружена версия\u00a0${fresh.processVersion}. Введённые поля сохранены в форме`
          : `На сервере сохранена версия\u00a0${fresh.processVersion}. Сверьте её с формой`;
      }
      return 'Процесс с таким кодом отсутствует на сервере';
    }, true);
  }

  function submit(event) {
    event.preventDefault();
    const { code, ...definition } = draft;
    definition.priority = Number(definition.priority);
    definition.executionSeconds = Number(definition.executionSeconds);
    save.execute(
      async () => {
        try {
          return await api(`/processes/${encodeURIComponent(code)}`, { method: 'PUT', body: definition });
        } catch (error) {
          if (error.code === 'VERSION_CONFLICT') {
            setRevisionConflict(true);
            setLatest(null);
          }
          throw error;
        }
      },
      (record) => {
        setDraft(processDraft(record));
        setLatest(null);
        onSaved();
        return `Сохранена версия\u00a0${record.processVersion}`;
      },
    );
  }

  return (
    <Panel
      title={draft.expectedVersion ? `Настройки процесса, версия\u00a0${draft.expectedVersion}` : 'Новый процесс'}
    >
      <form className="stack management-form" onSubmit={submit}>
        <div className="form-grid">
          <Field label="Код процесса" hint="Строчные латинские буквы, числа, «_» и «-»">
            <input required maxLength={64} pattern={codePattern} value={draft.code}
              readOnly={draft.expectedVersion > 0 || save.pending || save.uncertain || revisionConflict || Boolean(latest)}
              onChange={(event) => set('code', event.target.value)} />
          </Field>
          <Field label="Название">
            <input required maxLength={120} disabled={save.pending} value={draft.name} onChange={(event) => set('name', event.target.value)} />
          </Field>
          <Field label="Обработчик">
            <select required disabled={save.pending} value={draft.handler} onChange={(event) => setDraft({ ...draft, handler: event.target.value, bindings: {} })}>
              <option value="">Выберите обработчик</option>
              {catalog.map((item) => <option value={item.processId} key={item.processId}>{item.name} ({item.processId})</option>)}
            </select>
          </Field>
          <Field label="Приоритет">
            <input type="number" min={1} max={9} step={1} required disabled={save.pending} value={draft.priority}
              onChange={(event) => set('priority', event.target.value)} />
          </Field>
          <Field label="Длительность, с">
            <input type="number" min={0} max={300} step="any" required disabled={save.pending} value={draft.executionSeconds}
              onChange={(event) => set('executionSeconds', event.target.value)} />
          </Field>
          <Field label="Описание">
            <textarea maxLength={1000} rows={3} disabled={save.pending} value={draft.description} onChange={(event) => set('description', event.target.value)} />
          </Field>
        </div>
        {handler && (
          <div className="stack">
            <strong>Привязки ресурсов</strong>
            <div className="form-grid">
              {handler.accessTemplates.map((access) => {
                const key = access.resourceKey;
                return (
                  <Field key={key} label={key} hint={`${modeLabel(access.mode)} — ${scopeLabel(access.scope)}`}>
                    <select disabled={save.pending} value={draft.bindings[key] ?? key} onChange={(event) => {
                      const bindings = { ...draft.bindings };
                      if (event.target.value === key) delete bindings[key];
                      else bindings[key] = event.target.value;
                      set('bindings', bindings);
                    }}>
                      {resources.filter((item) => item.kind === key).map((item) => (
                        <option value={item.resource_key} key={item.resource_key}>{item.resource_key}</option>
                      ))}
                    </select>
                  </Field>
                );
              })}
            </div>
          </div>
        )}
        <div className="actions management-checks">
          <label className="management-check"><input type="checkbox" disabled={save.pending} checked={draft.isActive} onChange={(event) => set('isActive', event.target.checked)} /> <span>Активен</span></label>
          <label className="management-check"><input type="checkbox" disabled={save.pending} checked={draft.joinActive} onChange={(event) => set('joinActive', event.target.checked)} /> <span>Объединять активные заявки</span></label>
        </div>
        {revisionConflict && (
          <Button type="button" onClick={verify} disabled={save.pending}>Загрузить актуальную версию</Button>
        )}
        {latest && (
          <div className="stack">
            <strong>{`На\u00a0сервере версия\u00a0${latest.processVersion}`}</strong>
            <Table headers={['Поле', 'Актуальное значение', 'Ваше значение']} mobile={{ title: 0 }}>
              {[
                ['Название', latest.name, draft.name],
                ['Описание', typograph(latest.description), typograph(draft.description)],
                ['Обработчик', latest.handler, draft.handler],
                ['Приоритет', latest.priority, draft.priority],
                ['Длительность, с', latest.executionSeconds, draft.executionSeconds],
                ['Активность', latest.isActive ? 'Активен' : 'Выключен', draft.isActive ? 'Активен' : 'Выключен'],
                ['Объединение заявок', latest.joinActive ? 'Да' : 'Нет', draft.joinActive ? 'Да' : 'Нет'],
                ['Привязки ресурсов', bindingsLabel(latest.bindings), bindingsLabel(draft.bindings)],
              ].map(([label, server, local]) => (
                <tr key={label}><th scope="row">{label}</th><td>{comparisonValue(server)}</td><td>{comparisonValue(local)}</td></tr>
              ))}
            </Table>
            <div className="actions management-compare-actions">
              <Button type="button" onClick={() => {
                setDraft((current) => ({ ...current, expectedVersion: latest.processVersion }));
                setLatest(null);
              }}>Оставить мои значения</Button>
              <Button type="button" variant="ghost" onClick={() => {
                setDraft(processDraft(latest));
                setLatest(null);
              }}>Заполнить форму с сервера</Button>
            </div>
          </div>
        )}
        <SaveState save={save} verify={verify} />
        <div className="actions">
          <Button type="submit" variant="primary" disabled={save.pending || save.uncertain || revisionConflict || Boolean(latest)}>
            {save.pending ? 'Выполняется запрос…' : 'Сохранить версию'}
          </Button>
        </div>
      </form>
    </Panel>
  );
}

function bindingsLabel(bindings) {
  return Object.entries(bindings).map(([key, value]) => `${key} → ${value}`).join(', ') || 'По\u00a0умолчанию';
}

function comparisonValue(value) {
  return value === '' ? '—' : value;
}

function Processes({ reference }) {
  const [editor, setEditor] = useState(null);
  const poll = usePoll(async (signal) => {
    const [processes, handlers] = await Promise.all([
      api('/processes', { signal }),
      api('/handlers', { signal }),
    ]);
    return { processes: processes.items, handlers: handlers.items, resources: handlers.resources };
  }, []);

  function edit(process) {
    setEditor({ process, key: process?.processId ?? 'new' });
    requestAnimationFrame(() => document.getElementById('process-editor')?.scrollIntoView({ block: 'start' }));
  }

  return (
    <div className="stack management-page">
      <ManagementHeading title="Процессы">
        <Button variant="primary" onClick={() => edit(null)} disabled={!poll.data}>Новый процесс</Button>
      </ManagementHeading>
      {editor && poll.data && (
        <div id="process-editor" className="detail-page">
          <BackButton onClick={() => setEditor(null)} />
          <ProcessEditor key={editor.key} process={editor.process} catalog={poll.data.handlers} resources={poll.data.resources}
            onSaved={() => { poll.refresh(); reference.refresh(); }} />
        </div>
      )}
      <Panel>
        <LoadState poll={poll} />
        {poll.data?.processes.length === 0 && <Empty>Процессы ещё не созданы</Empty>}
        {poll.data?.processes.length > 0 && (
          <Table headers={['Процесс', 'Версия', 'Приоритет', 'Длительность', 'Состояние', 'Действие']} mobile={{ status: 4, action: [5] }}>
            {poll.data.processes.map((process) => (
              <tr key={process.processId}>
                <td><strong>{process.name}</strong><div className="muted mono">{process.processId}</div></td>
                <td>{process.processVersion}</td>
                <td>{process.priority}</td>
                <td>{`${process.executionSeconds}\u00a0с`}</td>
                <td>{process.isActive ? 'Активен' : 'Выключен'}</td>
                <td>
                  <Button variant="ghost" className="management-edit" onClick={() => edit(process)}
                    aria-label={`Изменить процесс «${process.name}»`}><span>Изменить</span></Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Panel>
    </div>
  );
}

function ResourceEditor({ kinds, onSaved }) {
  const [key, setKey] = useState('');
  const [kind, setKind] = useState('');
  const [copyValues, setCopyValues] = useState(false);
  const save = useSave();

  function submit(event) {
    event.preventDefault();
    save.execute(
      () => api('/resources', { method: 'POST', body: { key, kind, copyValues } }),
      () => {
        const message = `Ресурс ${key} создан`;
        setKey('');
        onSaved();
        return message;
      },
    );
  }

  function verify() {
    save.execute(() => api('/dependencies'), ({ resources }) => {
      const exists = resources.some((resource) => resource.resource_key === key);
      onSaved();
      if (exists) {
        setKey('');
        return `Ресурс ${key} существует на сервере`;
      }
      return `Ресурс ${key} пока отсутствует на сервере`;
    }, true);
  }

  return (
    <form className="stack management-form" onSubmit={submit}>
      <div className="form-grid">
        <Field label="Ключ ресурса">
          <input required maxLength={64} pattern={codePattern} value={key} readOnly={save.uncertain || save.pending} onChange={(event) => setKey(event.target.value)} />
        </Field>
        <Field label="Тип ресурса">
          <select required disabled={save.pending} value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">Выберите тип</option>
            {kinds.map((item) => <option key={item.resource_key} value={item.resource_key}>{item.resource_key}</option>)}
          </select>
        </Field>
      </div>
      <label className="management-check"><input type="checkbox" disabled={save.pending} checked={copyValues} onChange={(event) => setCopyValues(event.target.checked)} /> <span>Скопировать исходные значения</span></label>
      <SaveState save={save} verify={verify} />
      <div className="actions">
        <Button type="submit" variant="primary" disabled={save.pending || save.uncertain || !kinds.length}>
          {save.pending ? 'Выполняется запрос…' : 'Создать ресурс'}
        </Button>
      </div>
    </form>
  );
}

function Resources({ reference }) {
  const poll = usePoll((signal) => api('/dependencies', { signal }), []);
  const names = new Map(reference.processes.map((process) => [process.processId, process.name]));
  const data = poll.data;
  const kinds = data?.resources.filter((resource) => !resource.kind) ?? [];

  return (
    <div className="stack management-page">
      <ManagementHeading title="Карта ресурсов">
        <Button id="resource-list" variant="primary" onClick={() => document.getElementById('resource-editor')?.scrollIntoView({ block: 'start' })}>Новый ресурс</Button>
      </ManagementHeading>
      <Panel>
        <LoadState poll={poll} />
        {data?.resources.length === 0 && <Empty>Ресурсы ещё не настроены</Empty>}
        {data?.resources.length > 0 && (
          <Table headers={['Ресурс и область', 'Чтение (READ)', 'Запись (WRITE)', 'Удерживают', 'Ожидают']} mobile={{ wide: [3, 4] }}>
            {data.resources.map((resource) => {
              const key = resource.resource_key;
              const users = { READ: [], WRITE: [] };
              for (const process of data.processes) {
                for (const access of process.accesses) {
                  if (access.resourceKey === key) users[access.mode].push(process);
                }
              }
              const owners = data.owners.filter((owner) => owner.resource_key === key);
              const waiting = data.waiting.flatMap((job) => (job.reason?.conflicts ?? [])
                .filter((conflict) => conflict.resourceKey === key).map((conflict) => ({ job, conflict })));
              return (
                <tr key={key}>
                  <td>
                    <strong className="mono">{key}</strong>
                    <div className="muted">{scopeLabel(resource.partitioning)}</div>
                    {resource.kind && <div className="muted">Тип {resource.kind}</div>}
                  </td>
                  {['READ', 'WRITE'].map((mode) => (
                    <td key={mode}>
                      {users[mode].length ? users[mode].map((process) => (
                        <div className="management-resource-entry" key={process.processId}>
                          {names.get(process.processId) ?? process.processId}
                          {!process.isActive && <small className="muted"> (выключен)</small>}
                        </div>
                      )) : <span className="muted">—</span>}
                    </td>
                  ))}
                  <td>
                    <ResourceState active={owners.length > 0}>{owners.length ? 'Занят' : 'Свободен'}</ResourceState>
                    {owners.map((owner) => (
                      <div className="management-resource-entry" key={owner.job_id}>
                        <JobLink id={owner.job_id}>{names.get(data.activeProcesses[owner.job_id]) ?? data.activeProcesses[owner.job_id] ?? owner.job_id}</JobLink>
                        <div className="muted">{modeLabel(owner.mode)} — {scopeLabel(owner.scope)}</div>
                      </div>
                    ))}
                  </td>
                  <td>
                    <ResourceState active={waiting.length > 0}>{waiting.length ? 'Есть ожидание' : 'Нет ожидания'}</ResourceState>
                    {waiting.map(({ job, conflict }) => (
                      <div className="management-resource-entry" key={`${job.id}-${conflict.blockingJobId}-${conflict.scope?.date ?? 'global'}`}>
                        <JobLink id={job.id}>{names.get(job.process) ?? job.process}</JobLink>
                        <div className="muted">{modeLabel(conflict.requestedMode)} — {scopeLabel(conflict.scope)}</div>
                        <small>{'Владелец:\u00a0'}<JobLink id={conflict.blockingJobId}>{conflict.blockingJobId.slice(0, 8)}</JobLink></small>
                      </div>
                    ))}
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Panel>
      <div id="resource-editor" className="detail-page">
        <BackButton onClick={() => document.getElementById('resource-list')?.focus()} />
        <Panel title="Создание ресурса">
          <ResourceEditor kinds={kinds} onSaved={poll.refresh} />
        </Panel>
      </div>
    </div>
  );
}

function scheduleDraft(row) {
  return {
    code: row?.id ?? '',
    processId: row?.process ?? '',
    frequency: row?.weekday == null ? 'DAILY' : 'WEEKLY',
    localTime: row?.local_time.slice(0, 5) ?? '02:00',
    weekday: row?.weekday ?? 0,
    enabled: row?.enabled ?? true,
  };
}

function scheduleFrequency(row) {
  if (!row.daily) return 'Однократно';
  return row.weekday == null ? 'Ежедневно' : `По\u00a0${weekdayLabels[row.weekday]}`;
}

function ScheduleEditor({ row, processes, onSaved }) {
  const [draft, setDraft] = useState(() => scheduleDraft(row));
  const [stored, setStored] = useState(row);
  const save = useSave();

  function set(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    const { code, ...definition } = draft;
    definition.weekday = definition.frequency === 'WEEKLY' ? Number(definition.weekday) : null;
    save.execute(
      () => api(`/schedules/${encodeURIComponent(code)}`, { method: 'PUT', body: definition }),
      ({ scheduleId, nextFireAt }) => {
        setStored({ id: scheduleId });
        onSaved();
        return nextFireAt ? `Расписание сохранено. Следующий запуск ${formatDate(nextFireAt)}` : 'Расписание сохранено и выключено';
      },
    );
  }

  function verify() {
    save.execute(() => api('/schedules'), ({ items }) => {
      const record = items.find((item) => item.id === draft.code);
      onSaved();
      if (record) {
        setStored(record);
        const titles = processes.map((process) => [process.processId, process.name]);
        const name = new Map(titles).get(record.process) ?? record.process;
        return `Расписание на сервере — ${name}, ${scheduleFrequency(record).toLocaleLowerCase('ru')}, ${record.local_time.slice(0, 5)}\u00a0МСК, ${record.enabled ? 'включено' : 'выключено'}`;
      }
      return `Расписание ${draft.code} пока отсутствует на сервере`;
    }, true);
  }

  return (
    <Panel title={stored ? `Расписание ${stored.id}` : 'Новое расписание'}>
      <form className="stack management-form" onSubmit={submit}>
        <div className="form-grid">
          <Field label="Код расписания">
            <input required maxLength={64} pattern={codePattern} value={draft.code} readOnly={Boolean(stored) || save.uncertain || save.pending}
              onChange={(event) => set('code', event.target.value)} />
          </Field>
          <Field label="Процесс">
            <select required disabled={save.pending} value={draft.processId} onChange={(event) => set('processId', event.target.value)}>
              <option value="">Выберите процесс</option>
              {processes.map((process) => (
                <option key={process.processId} value={process.processId}>{process.name}{process.isActive ? '' : ' (выключен)'}</option>
              ))}
            </select>
          </Field>
          <Field label="Периодичность">
            <select disabled={save.pending} value={draft.frequency} onChange={(event) => set('frequency', event.target.value)}>
              <option value="DAILY">Ежедневно</option>
              <option value="WEEKLY">Еженедельно</option>
            </select>
          </Field>
          <Field label="Время запуска, МСК" hint="Формат ЧЧ:ММ">
            <input inputMode="numeric" placeholder="ЧЧ:ММ" maxLength={5} pattern="(?:[01][0-9]|2[0-3]):[0-5][0-9]"
              required disabled={save.pending} value={draft.localTime}
              onChange={(event) => set('localTime', event.target.value.replace(/^(\d{2})(\d{1,2})$/, '$1:$2'))} />
          </Field>
          {draft.frequency === 'WEEKLY' && (
            <Field label="День недели">
              <select disabled={save.pending} value={draft.weekday} onChange={(event) => set('weekday', Number(event.target.value))}>
                {weekdays.map((name, index) => <option key={name} value={index}>{name}</option>)}
              </select>
            </Field>
          )}
        </div>
        <label className="management-check"><input type="checkbox" disabled={save.pending} checked={draft.enabled} onChange={(event) => set('enabled', event.target.checked)} /> <span>Расписание включено</span></label>
        <SaveState save={save} verify={verify} />
        <div className="actions">
          <Button type="submit" variant="primary" disabled={save.pending || save.uncertain}>
            {save.pending ? 'Выполняется запрос…' : 'Сохранить расписание'}
          </Button>
        </div>
      </form>
    </Panel>
  );
}

function Schedules({ reference }) {
  const poll = usePoll((signal) => api('/schedules', { signal }), []);
  const [before, setBefore] = useState(null);
  const history = usePoll((signal) => api(`/schedule-history${before == null ? '' : `?before=${before}`}`, { signal }), [before]);
  const [editor, setEditor] = useState(null);
  const names = new Map(reference.processes.map((process) => [process.processId, process.name]));
  const entries = history.data?.items ?? [];

  function edit(row) {
    setEditor({ row, key: row?.id ?? 'new' });
    requestAnimationFrame(() => document.getElementById('schedule-editor')?.scrollIntoView({ block: 'start' }));
  }

  return (
    <div className="stack management-page">
      <ManagementHeading title="Расписания">
        <Button variant="primary" onClick={() => edit(null)}>Новое расписание</Button>
      </ManagementHeading>
      {editor && (
        <div id="schedule-editor" className="detail-page">
          <BackButton onClick={() => setEditor(null)} />
          <ScheduleEditor key={editor.key} row={editor.row} processes={reference.processes}
            onSaved={() => { poll.refresh(); history.refresh(); }} />
        </div>
      )}
      <Panel>
        <LoadState poll={poll} />
        {poll.data?.items.length === 0 && <Empty>Расписания ещё не настроены</Empty>}
        {poll.data?.items.length > 0 && (
          <Table headers={['Расписание', 'Процесс', 'Периодичность', 'Время, МСК', 'Следующий запуск', 'Состояние', 'Действие']}
            mobile={{ status: 5, wide: [1, 4], action: [6] }}>
            {poll.data.items.map((row) => {
              const managed = row.id === 'credit-daily' || row.id.startsWith('demo-') && !row.daily;
              return (
                <tr key={row.id}>
                  <td className="mono">{row.id}</td>
                  <td>{names.get(row.process) ?? row.process}</td>
                  <td>{scheduleFrequency(row)}</td>
                  <td>{row.local_time.slice(0, 5)}</td>
                  <td><ManagementDate value={row.due} /></td>
                  <td>{row.enabled ? 'Включено' : 'Выключено'}</td>
                  <td>{managed ? <span className="muted">Управляется стендом</span> :
                    <Button variant="ghost" className="management-edit" onClick={() => edit(row)}
                      aria-label={`Изменить расписание ${row.id}`}><span>Изменить</span></Button>}</td>
                </tr>
              );
            })}
          </Table>
        )}
      </Panel>
      <Panel title="История доставки" actions={before != null && <Button type="button" onClick={() => setBefore(null)}>К последним</Button>}>
        <LoadState poll={history} />
        {history.data && entries.length === 0 && <Empty>Доставки по расписанию ещё не зарегистрированы</Empty>}
        {entries.length > 0 && (
          <Table headers={['Расписание', 'Плановое время', 'Фактическое время', 'Исход', 'Причина', 'Задача']}
            mobile={{ status: 3, wide: [4] }}>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td className="mono">{entry.schedule_id}</td>
                <td><ManagementDate value={entry.planned} /></td>
                <td><ManagementDate value={entry.observed} /></td>
                <td>{deliveries[entry.status] ?? entry.status}</td>
                <td>{entry.reason ? typograph(entry.reason) : '—'}</td>
                <td>{entry.job_id ? <JobLink id={entry.job_id}>{entry.job_id.slice(0, 8)}</JobLink> : '—'}</td>
              </tr>
            ))}
          </Table>
        )}
        {entries.length === 100 && (
          <div className="actions">
            <Button onClick={() => setBefore(entries.at(-1).id)}>Более ранние доставки</Button>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Audit() {
  const records = useRef([]);
  const poll = usePoll(async (signal) => {
    const after = records.current.at(-1)?.id ?? 0;
    const { items } = await api(`/audit/configuration?afterId=${after}`, { signal });
    if (!signal.aborted && items.length) {
      const seen = new Map(records.current.map((event) => [event.id, event]));
      for (const item of items) seen.set(item.id, item);
      records.current = [...seen.values()].sort((a, b) => a.id - b.id);
    }
    return records.current;
  }, []);

  return (
    <div className="stack management-page">
      <ManagementHeading title="Аудит" compact>
        <RefreshButton onClick={poll.refresh} label="Обновить аудит" />
      </ManagementHeading>
      <Panel>
        <LoadState poll={poll} />
        {poll.data?.length === 0 && <Empty>Изменений конфигурации пока нет</Empty>}
        {poll.data?.length > 0 && (
          <Table headers={['№', 'Время', 'Действие', 'Объект', 'Инициатор', 'Данные']} mobile={{ title: 2, action: [5] }}>
            {[...poll.data].reverse().map((event) => (
              <tr key={event.id}>
                <td className="mono">{event.id}</td>
                <td><ManagementDate value={event.occurred} /></td>
                <td>{auditLabels[event.operation] ?? event.operation}</td>
                <td className="mono">{event.object_id}</td>
                <td>{event.actor}</td>
                <td><details><summary>Параметры</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details></td>
              </tr>
            ))}
          </Table>
        )}
      </Panel>
    </div>
  );
}

export function Management({ page, reference }) {
  switch (page) {
    case 'processes': return <Processes reference={reference} />;
    case 'resources': return <Resources reference={reference} />;
    case 'schedules': return <Schedules reference={reference} />;
    case 'audit': return <Audit />;
    default: return null;
  }
}
