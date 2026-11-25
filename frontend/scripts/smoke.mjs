import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

const origin = new URL(process.argv[2] ?? 'http://localhost:5173').origin;
const processId = 'liquidity-risk';

async function request(path, options) {
  const response = await fetch(`${origin}/api/v1${path}`, {
    signal: AbortSignal.timeout(5000),
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) {
    const detail = [payload?.code, payload?.message].filter(Boolean).join(' — ');
    throw new Error(`${path}: HTTP ${response.status}${detail ? ` ${detail}` : ''}`);
  }
  return { status: response.status, payload };
}

async function run() {
  const { payload: cases } = await request('/synthetic-cases');
  const scenario = cases.items.find((item) => typeof item.expected[processId] === 'string');
  assert.ok(scenario, 'В каталоге нет успешного сценария расчёта ликвидности.');
  const { calculationDate } = scenario;
  const submission = {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': randomUUID(),
      Origin: origin,
    },
    body: JSON.stringify({ processId, calculationDate }),
  };
  const accepted = await request('/jobs', submission);
  assert.ok([200, 201].includes(accepted.status));
  const { jobId } = accepted.payload;
  assert.ok(jobId, 'Сервер не вернул задачу.');

  const filters = new URLSearchParams({ processId, calculationDate, triggerType: 'MANUAL' });
  const { payload: queue } = await request(`/jobs?view=queue&${filters}`);
  assert.ok(Array.isArray(queue.items), 'Сервер не вернул список очереди.');

  const repeated = await request('/jobs', submission);
  assert.equal(repeated.status, 200);
  assert.equal(repeated.payload.jobId, jobId, 'Повтор с тем же ключом вернул другую задачу.');

  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const { payload: job } = await request(`/jobs/${jobId}`);
    if (['FAILED', 'CANCELLED'].includes(job.status)) {
      throw new Error(`${jobId} ${job.status} ${job.error?.message ?? ''}`.trim());
    }
    if (job.status === 'SUCCEEDED') {
      const output = job.result?.outputs.find((item) => item.resourceKey === 'liquidity_risk');
      assert.equal(output?.value, scenario.expected[processId]);

      // Check the event history because a fast job may leave the queue before the first poll.
      const { payload: events } = await request(`/events?jobId=${encodeURIComponent(jobId)}`);
      assert.ok(events.items.some((event) => event.type === 'JOB_QUEUED'));
      assert.ok(events.items.some((event) => event.type === 'JOB_SUCCEEDED'));
      console.log(`${job.jobId} · ${calculationDate} · ${job.status} · ${output.resourceKey} = ${output.value}`);
      return;
    }
    await delay(500);
  }
  throw new Error(`Истекло время ожидания результата ${jobId}.`);
}

run().catch((error) => {
  const cause = error.cause?.message ? ` (${error.cause.message})` : '';
  console.error(`Проверка ${origin}: ${error.message}${cause}`);
  process.exitCode = 1;
});
