const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const btn = (id, text) =>
  `<button class="link" data-job="${esc(id)}">${esc(text)}</button>`;
const table = (heads, rows) =>
  rows.length
    ? `<table><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>").join("")}</tbody></table>`
    : "Нет данных";
const tabs = {
  overview: "Обзор",
  tasks: "Задачи",
  catalog: "Процессы",
  schedule: "Расписания",
  map: "Ресурсы",
  analysis: "Аналитика",
  demo: "Демо",
};
let batchId = new URLSearchParams(location.search).get("batch"),
  slotBefore = null,
  slotNext = null;
let tab = "overview",
  processes = [],
  syntheticData = null,
  contracts = { items: [], resources: [] },
  schedules = [],
  cursor = null,
  next = null,
  demoBatch = new URLSearchParams(location.search).get("demo"),
  selected = null,
  revision = 0,
  bindings = {},
  edited = null;
const api = async (path, method = "GET", body, key) => {
  const r = await fetch("/api/v1" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(key ? { "Idempotency-Key": key } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok) throw Error(data.message ?? r.statusText);
  return data;
};
const safe =
  (fn) =>
  async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      $("notice").textContent = e.message;
    }
  };
function selectOptions(el, list) {
  const old = el.value,
    html = list
      .map((p) => `<option value="${esc(p.processId)}">${esc(p.name)}</option>`)
      .join("");
  if (el.innerHTML !== html) {
    el.innerHTML = html;
    if (list.some((p) => p.processId === old)) el.value = old;
  }
}
function bindingFields() {
  const handler = contracts.items.find(
    (h) => h.processId === $("processForm").elements.handler.value,
  );
  const html = (handler?.accessTemplates ?? [])
    .map(
      (a) =>
        `<label>${esc(a.mode)} · ${esc(a.resourceKey)}<select data-binding="${esc(a.resourceKey)}">${contracts.resources
          .filter((r) => r.kind === a.resourceKey)
          .map(
            (r) =>
              `<option value="${esc(r.resource_key)}" ${r.resource_key === (bindings[a.resourceKey] ?? a.resourceKey) ? "selected" : ""}>${esc(r.resource_key)}</option>`,
          )
          .join("")}</select></label>`,
    )
    .join("");
  if ($("bindingFields").dataset.markup !== html) {
    $("bindingFields").innerHTML = html;
    $("bindingFields").dataset.markup = html;
  }
}
$("processForm").elements.handler.onchange = () => {
  bindings = {};
  bindingFields();
};
$("bindingFields").onchange = (e) => {
  const k = e.target.dataset.binding;
  if (k) {
    if (k === e.target.value) delete bindings[k];
    else bindings[k] = e.target.value;
  }
};
function jobsTable(items) {
  return table(
    ["Процесс", "Дата", "Источник", "Приоритет", "Состояние", "Причина"],
    items.map((j) => [
      btn(j.jobId, j.processId),
      esc(j.calculationDate),
      esc(j.trigger.type),
      esc(j.priority),
      esc(j.status),
      esc(j.waitReason?.code),
    ]),
  );
}
const inputLabels = {
  credit_exposures: "Кредитные суммы",
  market_positions: "Рыночные позиции",
  cash_flows: "Поступления и выплаты",
  incident_losses: "Потери по инцидентам",
  parameter_inputs: "Кредитный и рыночный коэффициенты, %",
};

function renderSyntheticCase() {
  if (!syntheticData) return;
  const { calculationDate, processId } = $("launch").elements;
  const sample = syntheticData.items.find(
    (item) => item.calculationDate === calculationDate.value,
  );
  const process = processes.find((item) => item.processId === processId.value);
  selectOptions($("syntheticCase"), [
    { processId: "", name: "Своя дата" },
    ...syntheticData.items.map((item) => ({
      processId: item.calculationDate,
      name: item.name + " · " + item.calculationDate,
    })),
  ]);
  $("syntheticCase").value = sample?.calculationDate ?? "";
  for (const id of [
    "scenarioDescription",
    "scenarioExpected",
    "scenarioInputs",
  ])
    $(id).hidden = !sample;
  if (!sample) return;

  const description = sample.description;
  const reference = sample.expected[processId.value];
  const error = sample.expectedErrors[processId.value];
  const standard =
    process &&
    process.handler === process.processId &&
    !Object.keys(process.bindings).length;
  let message = "";
  if (standard && error) message = "Ожидаемая ошибка · " + error;
  if (standard && typeof reference === "string") {
    message = "Ожидаемое значение · " + reference;
    if (
      ["credit-risk", "market-risk", "risk-summary"].includes(processId.value)
    )
      message += " при ревизии коэффициентов " + syntheticData.factorRevision;
    if (processId.value === "risk-summary")
      message +=
        ". Сначала пересчитайте кредитный риск, рыночный риск и ликвидность за эту дату.";
  }
  if (standard && reference && typeof reference === "object")
    message =
      "Ожидаемые коэффициенты · " +
      reference.creditFactor +
      " и " +
      reference.marketFactor +
      ". Обновление действует на все даты.";

  if ($("scenarioExpected").textContent !== message)
    $("scenarioExpected").textContent = message;
  $("scenarioExpected").hidden = !message;
  $("scenarioDescription").textContent = description;
  $("scenarioValues").innerHTML = table(
    ["Источник", "Значения"],
    Object.entries(inputLabels).map(([key, label]) => [
      label,
      esc(
        key in sample.inputs
          ? sample.inputs[key].join(", ") || "Нет записей"
          : "Источник отсутствует",
      ),
    ]),
  );
}
$("syntheticCase").onchange = () => {
  $("launch").elements.calculationDate.value = $("syntheticCase").value;
  renderSyntheticCase();
};
$("launch").addEventListener("change", renderSyntheticCase);

function applyPeriod(cases) {
  syntheticData = cases;
  for (const input of document.querySelectorAll("input[type=date]")) {
    input.min = cases.startDate;
    input.max = cases.endDate;
    if (input.dataset.period !== cases.startDate + "/" + cases.endDate) {
      const day = new Intl.DateTimeFormat("sv-SE", {
        timeZone: "Europe/Moscow",
      }).format(new Date());
      input.value = day >= input.min && day <= input.max ? day : input.min;
      input.dataset.period = cases.startDate + "/" + cases.endDate;
    }
  }
}

async function render() {
  const current = tab;
  const defs = (await api("/processes")).items;
  if (current !== tab) return;
  processes = defs;
  document.querySelectorAll(".processes").forEach((el) =>
    selectOptions(
      el,
      defs.filter((p) => p.isActive),
    ),
  );
  if (current === "overview") {
    const [stats, state, cases] = await Promise.all([
      api("/analytics"),
      api("/overview"),
      api("/synthetic-cases"),
    ]);
    applyPeriod(cases);
    const revisions = (await api("/factor-revisions")).items;
    selectOptions(
      $("factorRevision"),
      revisions.map((r) => ({
        processId: String(r.revision),
        name:
          r.revision +
          " · кредит " +
          r.value.creditFactor +
          " · рынок " +
          r.value.marketFactor,
      })),
    );
    if (batchId) await showBatch();
    renderSyntheticCase();
    $("stats").innerHTML = Object.entries({
      "Всего задач": stats.totalJobs,
      "Занято мест": state.occupiedSlots + "/" + state.maxSlots,
      "В очереди": state.countsByStatus.QUEUED + state.countsByStatus.WAITING,
      "Ждут ресурс": state.resourceWaitingCount,
      Ошибки: stats.failedJobs,
      "Среднее время":
        stats.meanRuntimeSeconds == null
          ? "—"
          : Number(stats.meanRuntimeSeconds).toFixed(2) + " с",
    })
      .map(
        ([k, v]) =>
          `<div><small>${esc(k)}</small><strong>${esc(v)}</strong></div>`,
      )
      .join("");
    const maximum = Math.max(
      1,
      ...stats.load.map((x) => Number(x.busySeconds)),
    );
    $("chart").innerHTML =
      '<svg viewBox="0 0 600 90" preserveAspectRatio="none" aria-label="Загрузка по минутам">' +
      stats.load
        .map(
          (x, i) =>
            `<rect x="${10 * i}" y="${85 - (80 * x.busySeconds) / maximum}" width="7" height="${1 + (80 * x.busySeconds) / maximum}" fill="#368b7e"><title>${esc(x.minute)} · ${Number(x.busySeconds).toFixed(2)} с</title></rect>`,
        )
        .join("") +
      "</svg>";
    $("recent").innerHTML =
      stats.recentEvents
        .map(
          (e) =>
            `<div class="row">${btn(e.job_id, e.type)} <small>${esc(new Date(e.occurred).toLocaleTimeString())}</small></div>`,
        )
        .join("") || "Событий пока нет";
  } else if (current === "tasks") {
    const data = await api(
      "/jobs?view=" +
        $("view").value +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    $("jobs").innerHTML = jobsTable(data.items);
    next = data.nextCursor;
    $("more").hidden = !next;
  } else if (current === "catalog") {
    contracts = await api("/handlers");
    selectOptions($("processForm").elements.handler, contracts.items);
    bindingFields();
    $("catalogList").innerHTML = table(
      ["Процесс", "Версия", "Приоритет", "Включён"],
      defs.map((p) => [
        `<button class="link" data-process="${esc(p.processId)}">${esc(p.name)}</button>`,
        p.processVersion,
        p.priority,
        p.isActive ? "Да" : "Нет",
      ]),
    );
  } else if (current === "schedule") {
    const history = (
      await api(
        "/schedule-history" + (slotBefore ? "?before=" + slotBefore : ""),
      )
    ).items;
    slotNext = history.at(-1)?.id;
    $("olderSlots").hidden = history.length < 100;
    $("latestSlots").hidden = !slotBefore;
    $("slotHistory").innerHTML = table(
      ["Регламент", "План", "Обработка", "Итог", "Задача / причина"],
      history.map((r) => [
        esc(r.schedule_id),
        esc(new Date(r.planned).toLocaleString()),
        esc(new Date(r.observed).toLocaleString()),
        esc(
          r.status === "DELIVERED"
            ? "Доставлено · задержка " +
                Math.max(
                  0,
                  Math.round(
                    (Date.parse(r.observed) - Date.parse(r.planned)) / 1000,
                  ),
                ) +
                " с"
            : r.status,
        ),
        r.job_id ? btn(r.job_id, "Карточка") : esc(r.reason),
      ]),
    );
    schedules = (await api("/schedules")).items;
    $("scheduleList").innerHTML = table(
      ["Код", "Процесс", "Следующий запуск"],
      schedules.map((s) => [
        s.daily && s.id !== "credit-daily"
          ? `<button class="link" data-schedule="${esc(s.id)}">${esc(s.id)}</button>`
          : esc(s.id),
        esc(s.process),
        esc(s.due ? new Date(s.due).toLocaleString() : "—"),
      ]),
    );
  } else if (current === "map") {
    const g = await api("/dependencies"),
      keys = g.resources.map((r) => r.resource_key),
      owners = new Set(g.owners.map((a) => a.resource_key)),
      waits = new Set(
        g.waiting.flatMap((j) => j.reason.conflicts.map((c) => c.resourceKey)),
      );
    const y = (i) => 25 + i * 26;
    const edges = g.processes.flatMap((p, i) =>
      p.accesses.map(
        (a) =>
          `<line x1="230" y1="${y(i)}" x2="475" y2="${y(keys.indexOf(a.resourceKey))}" stroke="${a.mode === "WRITE" ? "#337d74" : "#c4d4d2"}"><title>${esc(p.processId + " " + a.mode + " " + a.resourceKey)}</title></line>`,
      ),
    );
    $("graph").innerHTML =
      `<svg viewBox="0 0 760 ${y(Math.max(g.processes.length, keys.length))}">${edges.join("")}${g.processes.map((p, i) => `<text x="8" y="${y(i) + 4}" fill="${g.owners.some((o) => g.activeProcesses?.[o.job_id] === p.processId) ? "#3068ba" : "#193d40"}">${esc(p.processId)}</text>`).join("")}${keys.map((k, i) => `<circle cx="476" cy="${y(i)}" r="5" fill="${waits.has(k) ? "#c08824" : owners.has(k) ? "#c45151" : "#33897a"}"/><text x="492" y="${y(i) + 4}">${esc(k)}</text>`).join("")}</svg>`;
    selectOptions(
      $("resourceForm").elements.kind,
      g.resources
        .filter((r) => !r.kind)
        .map((r) => ({ processId: r.resource_key, name: r.resource_key })),
    );
    $("locks").innerHTML =
      table(
        ["Ресурс", "Режим", "Владелец"],
        g.owners.map((o) => [
          esc(o.resource_key),
          esc(o.mode),
          btn(o.job_id, o.job_id),
        ]),
      ) +
      g.waiting
        .map(
          (w) =>
            '<div class="row">' +
            btn(w.id, w.process) +
            " " +
            esc(w.reason.code) +
            "</div>",
        )
        .join("");
  } else if (current === "analysis") {
    applyPeriod(await api("/synthetic-cases"));
    const stats = await api("/analytics");
    $("hotspots").innerHTML = table(
      ["Ресурс", "Секунды", "Доля ожидания"],
      stats.resources.map((r) => [
        esc(r.resourceKey),
        r.waitSeconds,
        (100 * r.shareOfWait).toFixed(1) + "%",
      ]),
    );
  } else if (current === "demo") {
    const sim = await api("/simulation?seed=" + $("simulationSeed").value);
    $("comparison").innerHTML = table(
      [
        "Политика",
        "Время завершения, такты",
        "p95 ожидания, такты",
        "Конфликтующие пары",
      ],
      [
        ["Безопасная FIFO", sim.fifo],
        ["RiskFlow · первая совместимая задача", sim.managed],
        ["Исполнение без блокировок", sim.unmanaged],
      ].map(([label, result]) => [
        label,
        result.durationTicks,
        result.p95WaitTicks,
        result.conflictingPairs,
      ]),
    );
    $("comparisonResult").textContent =
      `Безопасная FIFO → RiskFlow. Завершение ${sim.fifo.durationTicks} → ${sim.managed.durationTicks}, p95 ожидания ${sim.fifo.p95WaitTicks} → ${sim.managed.p95WaitTicks} тактов. p95 — время ожидания, в которое уложились 95% заявок.`;
    const report = (await api("/benchmarks?compact=true")).items[0];
    $("benchmarks").innerHTML = report
      ? table(
          [
            "Повтор",
            "Политика",
            "Завершение, с",
            "p95 ожидания, с",
            "Пересечения",
          ],
          report.report.pairs.flatMap((pair, i) =>
            Object.entries(pair).map(([policy, r]) => [
              i + 1,
              esc(policy),
              r.makespanSeconds.toFixed(3),
              r.p95WaitSeconds.toFixed(3),
              r.conflictingPairs,
            ]),
          ),
        )
      : "Для первого замера выполните docker compose exec backend python -m backend.analytics.benchmark";
    if (demoBatch) {
      const batch = await api("/demo/" + demoBatch);
      const complete =
        batch.items.length === 3 && batch.items.every((job) => job.finishedAt);
      $("demoStatus").textContent =
        batch.error ??
        (batch.status === "PENDING"
          ? "Планировщик ждёт старта первого кредитного расчёта."
          : complete
            ? "Задачи завершены. Порядок переходов виден в журнале."
            : "Планировщик подал регламентную заявку.");
      if (batch.scheduleSlot) {
        const slot = batch.scheduleSlot;
        $("demoStatus").textContent +=
          " План · " +
          new Date(slot.planned).toLocaleTimeString() +
          ", обработка · " +
          new Date(slot.observed).toLocaleTimeString() +
          ".";
      }
      $("runDemo").disabled = batch.status !== "FAILED" && !complete;
      $("demoJobs").innerHTML = jobsTable(batch.items);
      const histories = await Promise.all(
        batch.items.map((job) => api("/events?jobId=" + job.jobId)),
      );
      const events = histories
        .flatMap((h) => h.items)
        .sort((a, b) => a.eventId - b.eventId);
      $("demoBars").innerHTML = timeline(batch, events);
      $("demoTimeline").innerHTML = table(
        ["Порядок", "Задача", "Событие", "Причина ожидания"],
        events.map((e) => [
          e.eventId,
          btn(e.jobId, String(batch.jobs.indexOf(e.jobId) + 1)),
          esc(e.type),
          (e.payload.waitReason?.conflicts ?? [])
            .map(
              (c) =>
                esc(c.resourceKey) + " · " + btn(c.blockingJobId, "Владелец"),
            )
            .join("<br>"),
        ]),
      );
    }
  }
  if (selected && $("details").open) await detail(selected);
}
function activate(id) {
  tab = id;
  document
    .querySelectorAll("main>section")
    .forEach((s) => (s.hidden = s.id !== id));
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
  safe(render)();
}
$("tabs").innerHTML = Object.entries(tabs)
  .map(([id, text]) => `<button data-tab="${id}">${text}</button>`)
  .join("");
async function detail(id) {
  const [job, history] = await Promise.all([
    api("/jobs/" + id),
    api("/events?jobId=" + id + "&limit=200"),
  ]);
  const provenance = job.result
    ? await api("/jobs/" + id + "/provenance")
    : null;
  if (id !== selected) return;
  const versions = provenance?.inputs.length
    ? "<h3>Версии входов</h3>" +
      table(
        ["Ресурс", "Ревизия", "Источник", "Коэффициенты"],
        provenance.inputs.map((i) => [
          esc(i.resourceKey),
          i.revision,
          i.sourceJobId
            ? btn(i.sourceJobId, i.sourceProcessId ?? "Задача")
            : "Исходные данные",
          i.coefficients
            ? esc(
                i.coefficients.resourceKey +
                  " · ревизия " +
                  i.coefficients.revision,
              ) +
              (i.coefficients.sourceJobId
                ? " " + btn(i.coefficients.sourceJobId, "Обновление")
                : "")
            : "—",
        ]),
      )
    : "";
  const coverage = {
    MIXED_REVISIONS:
      "Оценки в этой сводке использовали разные ревизии коэффициентов.",
    SAME_REVISION:
      "Кредитная и рыночная оценки использовали одну ревизию коэффициентов.",
    UNAVAILABLE: "История коэффициентов доступна частично.",
  }[provenance?.coefficientStatus];
  $("jobDetail").innerHTML =
    "<p>" +
    esc(job.processId + " · " + job.status + " · " + job.calculationDate) +
    "</p><pre>" +
    esc(
      JSON.stringify(
        job.result ?? job.error ?? job.waitReason ?? job.accesses,
        null,
        2,
      ),
    ) +
    "</pre>" +
    (job.batchId ? "<p>Серия · " + esc(job.batchId) + "</p>" : "") +
    (job.replayOf
      ? "<p>Воспроизведение · " + btn(job.replayOf, "Исходная задача") + "</p>"
      : "") +
    (job.retryAt
      ? "<p>Повтор после " +
        esc(new Date(job.retryAt).toLocaleTimeString()) +
        "</p>"
      : "") +
    versions +
    (coverage ? "<p>" + esc(coverage) + "</p>" : "") +
    table(
      ["Событие", "Время", "Попытка / причина"],
      history.items.map((e) => [
        esc(e.type),
        esc(new Date(e.occurredAt).toLocaleTimeString()),
        esc(
          [
            e.payload.attempt,
            e.payload.error?.message,
            e.payload.retryAt &&
              new Date(e.payload.retryAt).toLocaleTimeString(),
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      ]),
    ) +
    (job.hasSnapshot && job.result && !job.trigger.demoId
      ? '<button data-action="replay">Воспроизвести</button>'
      : "") +
    (job.trigger.demoId
      ? ""
      : `<button data-action="${job.finishedAt ? "restart" : "stop"}">${job.finishedAt ? "Запустить снова" : "Остановить"}</button>`);
}
document.addEventListener(
  "click",
  safe(async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.tab) activate(b.dataset.tab);
    if (b.dataset.job) {
      selected = b.dataset.job;
      await detail(selected);
      if (!$("details").open) $("details").showModal();
    }
    if (b.dataset.action) {
      const a = b.dataset.action;
      const r = await api(
        "/jobs/" + selected + "/" + a,
        "POST",
        {},
        crypto.randomUUID(),
      );
      selected = r.jobId;
      await detail(selected);
    }
    if (b.dataset.process) {
      const p = processes.find((x) => x.processId === b.dataset.process),
        f = $("processForm");
      revision = p.processVersion;
      bindings = { ...p.bindings };
      edited = p.processId;
      for (const k of [
        "name",
        "handler",
        "description",
        "priority",
        "executionSeconds",
        "isActive",
        "joinActive",
      ])
        f.elements[k].value = String(p[k]);
      f.elements.code.value = p.processId;
      bindingFields();
    }
    if (b.dataset.schedule) {
      const s = schedules.find((x) => x.id === b.dataset.schedule),
        f = $("scheduleForm");
      for (const [k, v] of Object.entries({
        code: s.id,
        processId: s.process,
        frequency: s.weekday == null ? "DAILY" : "WEEKLY",
        weekday: s.weekday ?? 0,
        localTime: s.local_time.slice(0, 5),
        enabled: s.enabled,
      }))
        f.elements[k].value = String(v);
    }
  }),
);
$("close").onclick = () => {
  $("details").close();
  selected = null;
};
$("more").onclick = () => {
  cursor = next;
  safe(render)();
};
$("view").onchange = () => {
  cursor = null;
  safe(render)();
};
$("processForm").onreset = () => {
  edited = null;
  revision = 0;
  bindings = {};
};
document.addEventListener(
  "submit",
  safe(async (e) => {
    e.preventDefault();
    const f = e.target,
      body = Object.fromEntries(new FormData(f));
    const button = f.querySelector("button");
    button.disabled = true;
    try {
      if (f.id === "launch") {
        if (body.priority) body.priority = Number(body.priority);
        else delete body.priority;
        const signature = JSON.stringify(body);
        if (f.dataset.signature !== signature) {
          f.dataset.key = crypto.randomUUID();
          f.dataset.signature = signature;
        }
        const j = await api("/jobs", "POST", body, f.dataset.key);
        delete f.dataset.signature;
        selected = j.jobId;
        await detail(selected);
        $("details").showModal();
      }
      if (f.id === "processForm") {
        const code = body.code;
        delete body.code;
        const p = await api("/processes/" + encodeURIComponent(code), "PUT", {
          ...body,
          expectedVersion: edited === code ? revision : 0,
          priority: +body.priority,
          executionSeconds: +body.executionSeconds,
          joinActive: body.joinActive === "true",
          isActive: body.isActive === "true",
          bindings,
        });
        revision = p.processVersion;
        edited = p.processId;
        $("notice").textContent = "Процесс сохранён";
      }
      if (f.id === "resourceForm") {
        await api("/resources", "POST", {
          ...body,
          copyValues: body.copyValues === "true",
        });
        $("notice").textContent = "Ресурс создан";
      }
      if (f.id === "scheduleForm") {
        const code = body.code;
        delete body.code;
        await api("/schedules/" + encodeURIComponent(code), "PUT", {
          ...body,
          enabled: body.enabled === "true",
          weekday: body.frequency === "WEEKLY" ? +body.weekday : null,
        });
        $("notice").textContent = "Расписание сохранено";
      }
      if (f.id === "ask") {
        if (!body.jobId) delete body.jobId;
        $("answer").textContent = (
          await api("/analytics/ask", "POST", body)
        ).answer;
      }
      if (f.id === "predict") {
        const p = await api("/analytics/forecast?" + new URLSearchParams(body));
        $("prediction").textContent =
          `Пересечений сейчас: ${p.conflictsNow.length}. Запусков за 7 дней: ${p.samples}. Доля с конфликтом: ${p.samples ? (100 * p.historicalConflictRate).toFixed(1) + "%" : "нет наблюдений"}.`;
      }
      await render();
    } finally {
      button.disabled = false;
    }
  }),
);
async function showBatch() {
  const b = await api("/batches/" + batchId);
  $("batchStatus").textContent =
    b.calculationDate +
    " · ревизия " +
    b.factorRevision +
    " · " +
    (b.error || b.status);
  $("batchSteps").innerHTML = table(
    ["Процесс", "Ожидание", "Факт", "Проверка"],
    b.steps.map((s) => [
      s.jobId ? btn(s.jobId, s.processId) : esc(s.processId),
      esc(s.expected),
      esc(s.actual ?? "—"),
      esc(
        s.error?.message ??
          (s.passed === null ? s.status : s.passed ? "Совпало" : "Расхождение"),
      ),
    ]),
  );
}
async function launchBatch(control) {
  const buttons = [$("systemCheck"), $("runBatch")];
  buttons.forEach((b) => (b.disabled = true));
  try {
    const r = await api("/batches", "POST", {
      calculationDate: control
        ? syntheticData.startDate
        : $("launch").elements.calculationDate.value,
      factorRevision: control ? 1 : Number($("factorRevision").value),
    });
    batchId = r.batchId;
    const url = new URL(location.href);
    url.searchParams.set("batch", batchId);
    history.replaceState(null, "", url);
    await showBatch();
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}
$("systemCheck").onclick = safe(() => launchBatch(true));
$("runBatch").onclick = safe(() => launchBatch(false));
$("olderSlots").onclick = safe(async () => {
  slotBefore = slotNext;
  await render();
});
$("latestSlots").onclick = safe(async () => {
  slotBefore = null;
  await render();
});

function timeline(batch, events) {
  const jobs = batch.items;
  if (!jobs.length) return "";
  const start = Math.min(...jobs.map((j) => Date.parse(j.queuedAt)));
  const end = Math.max(
    start + 1,
    ...jobs.map((j) => (j.finishedAt ? Date.parse(j.finishedAt) : Date.now())),
  );
  const x = (t) => 225 + (500 * (t - start)) / (end - start);
  const bars = jobs.map((j, i) => {
    const queued = x(Date.parse(j.queuedAt)),
      running = x(j.startedAt ? Date.parse(j.startedAt) : end);
    const finish = x(j.finishedAt ? Date.parse(j.finishedAt) : end),
      y = 25 + i * 48;
    const label =
      (j.trigger.type === "SCHEDULED" ? "Регламент" : "Вручную") +
      " · " +
      j.processId;
    return `<text x="4" y="${y + 12}">${esc(label)}</text><rect x="${queued}" y="${y}" width="${Math.max(0, running - queued)}" height="16" fill="#d6a336"/><rect x="${running}" y="${y}" width="${Math.max(0, finish - running)}" height="16" fill="#328678"/>`;
  });
  const blocked = events.find((e) => e.payload.waitReason?.conflicts?.length);
  if (blocked) {
    const owner = blocked.payload.waitReason.conflicts[0].blockingJobId;
    const lineX = x(Date.parse(blocked.occurredAt));
    bars.push(
      `<path d="M${lineX} ${25 + 48 * batch.jobs.indexOf(blocked.jobId)} V${43 + 48 * batch.jobs.indexOf(owner)}" stroke="#304861" stroke-width="2" marker-end="url(#arrow)"/>`,
    );
  }
  return (
    "<p>Золотой — ожидание · зелёный — выполнение · стрелка указывает на владельца ресурса</p>" +
    '<svg role="img" aria-label="Таймлайн ожидания и выполнения" viewBox="0 0 745 165"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 L10 5 L0 10z"/></marker></defs>' +
    bars.join("") +
    "</svg>" +
    (blocked
      ? btn(blocked.jobId, "Регламентный расчёт") +
        " → " +
        btn(
          blocked.payload.waitReason.conflicts[0].blockingJobId,
          "Владелец ресурса",
        )
      : "")
  );
}
$("runDemo").onclick = safe(async () => {
  $("runDemo").disabled = true;
  try {
    demoBatch = (await api("/demo", "POST", {})).demoId;
    const url = new URL(location.href);
    url.searchParams.set("demo", demoBatch);
    history.replaceState(null, "", url);
    await render();
  } catch (error) {
    $("runDemo").disabled = false;
    throw error;
  }
});
$("simulationSeed").onchange = safe(render);
activate(demoBatch ? "demo" : "overview");
async function poll() {
  await safe(render)();
  setTimeout(poll, 2500);
}
setTimeout(poll, 2500);
