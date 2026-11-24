import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Menu, X } from 'lucide-react';
import { typograph } from './typography.js';
import { api, Button, Empty, ErrorText, href, usePoll } from './ui.jsx';
import { Dashboard, Jobs, Batch, BatchLaunch } from './Workspace.jsx';
import { Job } from './Job.jsx';
import { Management } from './Management.jsx';
import { Diagnostics } from './Diagnostics.jsx';
import './styles.css';

const navigation = [
  { title: 'Расчёты', pages: [
    ['overview', 'Обзор'], ['queue', 'Очередь'], ['active', 'Активные задачи'],
    ['history', 'История'], ['batches', 'Сводка рисков'],
  ] },
  { title: 'Управление', pages: [
    ['processes', 'Процессы'], ['resources', 'Карта ресурсов'], ['schedules', 'Расписания'], ['audit', 'Аудит'],
  ] },
  { title: 'Диагностика', pages: [
    ['analytics', 'Аналитика'], ['demo', 'Демо конкуренции'], ['simulation', 'Сравнение политик'],
  ] },
];
const titles = {
  ...Object.fromEntries(navigation.flatMap(({ pages }) => pages)),
  overview: 'Обзор расчётов',
  queue: 'Очередь расчётов',
  history: 'История расчётов',
  job: 'Карточка задачи',
  batch: 'Серия расчётов',
};
const managementPages = ['processes', 'resources', 'schedules', 'audit'];

async function checkReadiness(signal) {
  const response = await fetch('/api/v1/health/ready', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
    signal: AbortSignal.any([signal, AbortSignal.timeout(4500)]),
  });
  if (!response.ok || (await response.json())?.status !== 'ready') {
    throw new Error('API недоступен');
  }
  return true;
}

function getRoute() {
  const [page, raw] = window.location.hash.replace(/^#\/?/, '').split('/');
  try { return { page: page || 'overview', id: raw ? decodeURIComponent(raw) : undefined }; }
  catch { return { page: 'missing' }; }
}

function useReference() {
  const processes = usePoll((signal) => api('/processes', { signal }), [], { interval: 15000 });
  const cases = usePoll((signal) => api('/synthetic-cases', { signal }), [], { interval: 30000 });
  const revisions = usePoll((signal) => api('/factor-revisions', { signal }), [], { interval: 15000 });
  return {
    processes: processes.data?.items ?? [], cases: cases.data, revisions: revisions.data?.items ?? [],
    loading: processes.loading || cases.loading || revisions.loading,
    error: processes.error || cases.error || revisions.error,
    stale: !!(processes.data || cases.data || revisions.data),
    refresh: () => { processes.refresh(); cases.refresh(); revisions.refresh(); },
  };
}

function App() {
  const [route, setRoute] = useState(getRoute);
  const [menuOpen, setMenuOpen] = useState(false);
  const readiness = usePoll(checkReadiness, [], { interval: 5000 });
  const reference = useReference();
  const { page, id } = route;
  const title = Object.hasOwn(titles, page) ? titles[page] : 'Страница не найдена';
  const selected = page === 'job' ? 'queue' : page === 'batch' ? 'batches' : page;
  const managed = managementPages.includes(page);

  useEffect(() => {
    const update = () => {
      setRoute(getRoute());
      setMenuOpen(false);
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  useEffect(() => { document.title = title + ' · RiskFlow'; }, [title]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event) => { if (event.key === 'Escape') setMenuOpen(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [menuOpen]);

  let content;
  if (page === 'overview') content = <Dashboard reference={reference} />;
  else if (['queue', 'active', 'history'].includes(page)) content = <Jobs view={page} initialStatus={id} reference={reference} />;
  else if (page === 'job' && id) content = <Job id={id} reference={reference} />;
  else if (page === 'batches') content = <BatchLaunch reference={reference} />;
  else if (page === 'batch' && id) content = <Batch id={id} reference={reference} />;
  else if (managed) content = <Management page={page} reference={reference} />;
  else if (['analytics', 'demo', 'simulation'].includes(page)) content = <Diagnostics page={page} id={id} reference={reference} />;
  else content = <Empty><a href={href('overview')}>{typograph('Вернуться к обзору')}</a></Empty>;

  return <div className="app-layout">
    <a className="skip-link" href="#workspace-content" onClick={(event) => {
      event.preventDefault(); document.getElementById('workspace-content')?.focus();
    }}>{typograph('К содержанию')}</a>
    {menuOpen && <button className="menu-scrim" aria-label="Закрыть меню" onClick={() => setMenuOpen(false)} />}
    <aside className={'sidebar' + (menuOpen ? ' sidebar-open' : '')} id="main-navigation">
      <div className="brand-row"><a className="brand" href={href('overview')} onClick={() => setMenuOpen(false)}>
        Risk<span className="brand-accent">Flow</span>
      </a><Button className="sidebar-close" variant="ghost" aria-label="Закрыть меню" onClick={() => setMenuOpen(false)}><X size={20} /></Button></div>
      <nav aria-label="Основная навигация">
        {navigation.map((group) => <div className="nav-group" key={group.title}>
          <div className="nav-title">{group.title}</div>
          {group.pages.map(([item, label]) => <a key={item} href={href(item)} onClick={() => setMenuOpen(false)}
            aria-current={selected === item ? 'page' : undefined} className="nav-link">
            {label}
          </a>)}
        </div>)}
      </nav>
    </aside>
    <div className="app-main">
      <header className="topbar">
        <div className="mobile-brand">
          <Button className="menu-toggle" variant="ghost" aria-label="Открыть меню" aria-controls="main-navigation"
            aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}><Menu size={21} /></Button>
          <a className="brand" href={href('overview')}>Risk<span className="brand-accent">Flow</span></a>
        </div>
        <div className={'connection' + (readiness.error ? ' connection-error' : '')} role="status">
          {readiness.error ? 'API недоступен' : readiness.data ? 'API подключён' : 'Проверяем API'}
        </div>
      </header>
      <main id="workspace-content" className="workspace" tabIndex={-1}>
        {!managed && (page !== 'job' || !id) && <h1 className="page-title">{typograph(title)}</h1>}
        {reference.error && <ErrorText error={reference.error} retry={reference.refresh} stale={reference.stale} />}
        <div key={page + '/' + (id ?? '')}>{content}</div>
      </main>
    </div>
  </div>;
}

createRoot(document.getElementById('root')).render(<App />);
