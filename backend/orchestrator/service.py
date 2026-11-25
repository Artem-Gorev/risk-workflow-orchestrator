from uuid import uuid4

from backend.workers.result import valid_output
from backend.workers.synthetic import in_synthetic_period, period_label

from .catalog import latest
from .resources import conflicts, resolve_accesses
from .storage import DomainError, encode, load_inputs, query


class JobService:
    def __init__(self, storage, slots, fifo=False):
        self.fifo = fifo
        self.storage = storage
        self.slots = slots

    @staticmethod
    def event(conn, job, kind, payload):
        occurred = {"JOB_QUEUED": job.queued, "JOB_STARTED": job.started}.get(kind)
        occurred = occurred or job.finished
        query(
            conn,
            """INSERT INTO job_events(job_id,type,status,occurred,payload)
                       VALUES (:id,:kind,:status,coalesce(:at,clock_timestamp()),CAST(:payload AS jsonb))""",
            id=job.id,
            kind=kind,
            status=job.status,
            at=occurred,
            payload=encode(payload),
        )

    def submit(
        self,
        conn,
        process,
        day,
        trigger,
        key=None,
        priority=None,
        *,
        job_id=None,
        duration=None,
    ):
        delivery = encode([trigger["type"], trigger["initiatorId"], key]) if key else None
        if delivery:
            existing = query(
                conn,
                """SELECT j.*,d.priority AS original_priority,d.restarted_from FROM deliveries d
                               JOIN jobs j ON j.id=d.job_id WHERE d.key=:key""",
                key=delivery,
            ).first()
            if existing:
                if (
                    existing.process != process
                    or existing.day != day
                    or existing.original_priority != priority
                    or existing.restarted_from != trigger.get("restartedFrom")
                ):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Ключ уже связан с другими параметрами")
                return existing, False
        if not in_synthetic_period(day):
            raise DomainError(
                "DATE_OUTSIDE_SYNTHETIC_RANGE",
                f"Выберите расчётную дату за {period_label()} включительно.",
                422,
            )
        definition = latest(conn, process)
        if not definition.active:
            raise DomainError("PROCESS_DISABLED", "Процесс выключен")
        if (
            definition.join_active
            and not trigger.get("restartedFrom")
            and not trigger.get("demoId")
            and not trigger.get("batchId")
        ):
            ongoing = query(
                conn,
                """SELECT * FROM jobs WHERE process=:p AND day=:d AND version=:v
                            AND priority=:priority AND finished IS NULL AND batch_id IS NULL
                            AND replay_of IS NULL AND trigger->>'type'=:trigger_type ORDER BY seq LIMIT 1""",
                p=process,
                d=day,
                v=definition.version,
                priority=priority or definition.priority,
                trigger_type=trigger["type"],
            ).first()
            if ongoing:
                self.remember(conn, delivery, ongoing.id, priority, trigger)
                self.event(conn, ongoing, "JOB_TRIGGER_JOINED", {"trigger": trigger})
                return ongoing, False
        accesses = resolve_accesses(definition.access_templates, day)
        job = query(
            conn,
            """INSERT INTO jobs(id,process,day,trigger,accesses,delivery,status,version,priority,
                requested_priority,handler,duration,bindings)
                    VALUES (:id,:process,:day,CAST(:trigger AS jsonb),CAST(:access AS jsonb),:key,'QUEUED',
                    :version,:priority,:requested,:handler,:duration,CAST(:bindings AS jsonb))
                    RETURNING *""",
            id=job_id or uuid4(),
            process=process,
            day=day,
            trigger=encode(trigger),
            access=encode(accesses),
            version=definition.version,
            priority=priority or definition.priority,
            requested=priority,
            handler=definition.handler or process,
            duration=definition.duration if duration is None else duration,
            bindings=encode(definition.bindings),
            key=delivery,
        ).one()
        self.event(
            conn,
            job,
            "JOB_QUEUED",
            {
                "processId": process,
                "calculationDate": str(day),
                "trigger": trigger,
                "queueSequence": job.seq,
            },
        )
        self.remember(conn, delivery, job.id, priority, trigger)
        return job, True

    @staticmethod
    def remember(conn, delivery, job_id, priority, trigger):
        if delivery:
            query(
                conn,
                "INSERT INTO deliveries VALUES (:key,:id,:priority,:source)",
                key=delivery,
                id=job_id,
                priority=priority,
                source=trigger.get("restartedFrom"),
            )

    def reserve(self):
        with self.storage.transaction() as conn:
            for expired in query(
                conn,
                """SELECT * FROM jobs WHERE status IN ('STARTING','RUNNING')
                                 AND lease_until<=clock_timestamp()""",
            ).all():
                self.fail(
                    conn,
                    expired,
                    "LEASE_EXPIRED",
                    "Исполнитель перестал продлевать резервирование",
                )
            owners = query(
                conn,
                "SELECT id,accesses FROM jobs WHERE status IN ('STARTING','RUNNING') ORDER BY seq",
            ).all()
            active = [dict(access, jobId=str(row.id)) for row in owners for access in row.accesses]
            candidates = query(
                conn,
                """SELECT *, extract(epoch FROM now()-queued)>=240 AS aged FROM jobs
                        WHERE status IN ('QUEUED','WAITING') AND (retry_at IS NULL OR
                        retry_at<=clock_timestamp()) ORDER BY greatest(1,priority-floor(extract(epoch FROM
                        now()-queued)/30)),seq""",
            ).all()
            protected: list[dict] = []
            for job in candidates:
                blocked = conflicts(job.accesses, active)
                ahead = conflicts(job.accesses, protected) if not blocked else []
                if job.aged:
                    protected.extend(dict(a, jobId=str(job.id)) for a in job.accesses)
                if blocked or ahead or len(owners) >= self.slots:
                    reason = {
                        "code": "RESOURCE_CONFLICT"
                        if blocked
                        else "QUEUE_FAIRNESS"
                        if ahead
                        else "NO_WORKER_SLOT",
                        "conflicts": blocked or ahead,
                    }
                    if job.reason != reason:
                        updated = query(
                            conn,
                            """UPDATE jobs SET status='WAITING',reason=CAST(:reason AS jsonb)
                                                WHERE id=:id RETURNING *""",
                            id=job.id,
                            reason=encode(reason),
                        ).one()
                        event = "JOB_WAITING" if job.status == "QUEUED" else "JOB_WAIT_REASON_CHANGED"
                        self.event(conn, updated, event, {"waitReason": reason})
                    if self.fifo:
                        return None
                    continue
                reserved = query(
                    conn,
                    """UPDATE jobs SET status='STARTING',attempt=attempt+1,retry_at=NULL,execution=:execution,
                        reason=NULL,lease_until=clock_timestamp()+interval '15 seconds'
                                         WHERE id=:id RETURNING *""",
                    id=job.id,
                    execution=uuid4(),
                ).one()
                self.event(
                    conn,
                    reserved,
                    "JOB_RESERVED",
                    {"reservedAccesses": job.accesses, "attempt": reserved.attempt},
                )
                return reserved
        return None

    def start(self, job):
        with self.storage.transaction() as conn:
            started = query(
                conn,
                """UPDATE jobs SET status='RUNNING',started=clock_timestamp()
                        WHERE id=:id AND execution=:execution AND status='STARTING' AND
                        lease_until>clock_timestamp() RETURNING *""",
                id=job.id,
                execution=job.execution,
            ).first()
            if started:
                self.event(conn, started, "JOB_STARTED", {})
            return bool(started)

    def cancel(self, job_id, actor):
        with self.storage.transaction() as conn:
            job = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job_id).first()
            if not job:
                raise DomainError("JOB_NOT_FOUND", "Задача не найдена", 404)
            if job.status == "CANCELLED":
                return
            if job.status not in ("QUEUED", "WAITING"):
                raise DomainError("JOB_STATE_CONFLICT", "Резервирование уже состоялось")
            job = query(
                conn,
                """UPDATE jobs SET status='CANCELLED',reason=NULL,finished=clock_timestamp()
                                 WHERE id=:id RETURNING *""",
                id=job.id,
            ).one()
            self.event(conn, job, "JOB_CANCELLED", {"initiatorId": actor})

    def fail(self, conn, job, code, message):
        error = {"code": code, "message": message}
        if (
            code
            in {
                "WORKER_LOST",
                "LEASE_EXPIRED",
                "EXECUTION_INTERRUPTED",
                "DISPATCH_ERROR",
            }
            and job.attempt < 3
        ):
            retry = query(
                conn,
                """UPDATE jobs SET status='QUEUED',execution=NULL,lease_until=NULL,
                reason=NULL,started=NULL,retry_at=clock_timestamp()+make_interval(secs=>:delay)
                WHERE id=:id AND execution=:execution AND status IN ('STARTING','RUNNING') RETURNING *""",
                delay=2 ** max(0, job.attempt - 1),
                id=job.id,
                execution=job.execution,
            ).first()
            if retry:
                self.event(
                    conn,
                    retry,
                    "JOB_RETRY_SCHEDULED",
                    {
                        "attempt": job.attempt,
                        "error": error,
                        "retryAt": retry.retry_at.isoformat(),
                        "releasedAccesses": job.accesses,
                    },
                )
            return
        failed = query(
            conn,
            """UPDATE jobs SET status='FAILED',lease_until=NULL,error=CAST(:error AS jsonb),
                finished=clock_timestamp()
                      WHERE id=:id AND execution=:execution AND status IN ('STARTING','RUNNING') RETURNING
                      *""",
            id=job.id,
            execution=job.execution,
            error=encode(error),
        ).first()
        if failed:
            self.event(
                conn,
                failed,
                "JOB_FAILED",
                {"error": error, "releasedAccesses": job.accesses},
            )

    def heartbeat(self, job):
        with self.storage.transaction() as conn:
            return (
                query(
                    conn,
                    """UPDATE jobs SET lease_until=clock_timestamp()+interval '15 seconds'
                         WHERE id=:id AND execution=:execution AND status='RUNNING'
                         AND lease_until>clock_timestamp() RETURNING id""",
                    id=job.id,
                    execution=job.execution,
                ).first()
                is not None
            )

    def stop_job(self, job_id, actor):
        with self.storage.transaction() as conn:
            row = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job_id).first()
            if not row:
                raise DomainError("JOB_NOT_FOUND", "Задача не найдена", 404)
            if row.status == "CANCELLED":
                return
            if row.finished:
                raise DomainError("JOB_STATE_CONFLICT", "Задача уже завершилась")
            released = row.accesses if row.execution else []
            cancelled = query(
                conn,
                """UPDATE jobs SET status='CANCELLED',lease_until=NULL,reason=NULL,
                              finished=clock_timestamp() WHERE id=:id RETURNING *""",
                id=job_id,
            ).one()
            self.event(
                conn,
                cancelled,
                "JOB_CANCELLED",
                {"initiatorId": actor, "releasedAccesses": released},
            )

    def recover(self):
        with self.storage.transaction() as conn:
            for job in query(
                conn,
                "SELECT * FROM jobs WHERE status IN ('STARTING','RUNNING') ORDER BY seq",
            ).all():
                self.fail(
                    conn,
                    job,
                    "EXECUTION_INTERRUPTED",
                    "Исполнение прервано перезапуском сервера",
                )

    def complete(self, job, outputs, versions):
        with self.storage.transaction() as conn:
            current = query(
                conn,
                "SELECT * FROM jobs WHERE id=:id AND execution=:execution",
                id=job.id,
                execution=job.execution,
            ).first()
            if not current or current.status != "RUNNING":
                return
            if query(conn, "SELECT :until<=clock_timestamp()", until=current.lease_until).scalar_one():
                self.fail(conn, current, "LEASE_EXPIRED", "Срок резервирования истёк")
                return
            logical = {v: k for k, v in current.bindings.items()}
            writes = [access for access in current.accesses if access["mode"] == "WRITE"]
            keys = {access["resourceKey"] for access in writes}
            if (
                not isinstance(outputs, dict)
                or set(outputs) != keys
                or any(not valid_output(logical.get(key, key), value) for key, value in outputs.items())
                or versions
                != (current.snapshot["versions"] if current.snapshot else load_inputs(conn, current)[1])
            ):
                self.fail(
                    conn,
                    current,
                    "INVALID_OUTPUT",
                    "Набор выходов отличается от контракта",
                )
                return
            published = []
            for access in writes:
                key, scope = access["resourceKey"], access["scope"]
                if current.replay_of:
                    original = query(
                        conn,
                        "SELECT result FROM jobs WHERE id=:id",
                        id=current.replay_of,
                    ).scalar_one()
                    previous = next(o for o in original["outputs"] if o["resourceKey"] == key)
                    if outputs[key] != previous["value"]:
                        self.fail(
                            conn,
                            current,
                            "REPLAY_MISMATCH",
                            "Значение отличается от оригинала",
                        )
                        return
                    published.append(previous)
                    continue
                partition = scope.get("date", "*")
                if current.trigger.get("demoId"):
                    partition = f"{current.trigger['demoId']}:{partition}"
                revision = query(
                    conn,
                    """INSERT INTO resource_values(key,partition,value,source)
                            VALUES (:key,:day,CAST(:value AS jsonb),:id) ON CONFLICT(key,partition) DO UPDATE
                            SET
                            value=excluded.value,source=excluded.source,revision=resource_values.revision+1
                            RETURNING revision""",
                    id=job.id,
                    key=key,
                    day=partition,
                    value=encode(outputs[key]),
                ).scalar_one()
                if key == "risk_parameters" and partition == "*":
                    query(
                        conn,
                        "INSERT INTO factor_history VALUES (:r,CAST(:v AS jsonb),:id)",
                        r=revision,
                        v=encode(outputs[key]),
                        id=job.id,
                    )
                published.append(
                    {
                        "resourceKey": key,
                        "scope": scope,
                        "revision": revision,
                        "value": outputs[key],
                    }
                )
            result = {"outputs": published, "inputVersions": versions}
            done = query(
                conn,
                """UPDATE jobs SET result=CAST(:result AS jsonb),status='SUCCEEDED',lease_until=NULL,
                                 finished=clock_timestamp() WHERE id=:id RETURNING *""",
                result=encode(result),
                id=job.id,
            ).one()
            self.event(conn, done, "JOB_SUCCEEDED", {"releasedAccesses": current.accesses})
