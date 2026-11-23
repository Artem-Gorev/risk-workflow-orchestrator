from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from orchestrator_core.db import Database
from orchestrator_core.exceptions import (
    ExecutionMismatchError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    JobNotFoundError,
    OrchestratorError,
    ValidationError,
)
from orchestrator_core.models import (
    IdempotencyRecordModel,
    JobEventModel,
    JobModel,
    OrchestratorControl,
    ProcessModel,
    ResourceModel,
    ResourceReservationModel,
    ResourceValueModel,
)
from orchestrator_core.resource_manager import ActiveReservation, ResourceManager
from orchestrator_core.schemas import (
    ACTIVE_SLOT_STATUSES,
    AccessMode,
    AccessTemplate,
    EventType,
    JobDTO,
    JobError,
    JobEventDTO,
    JobResult,
    JobStatus,
    JobTrigger,
    OutputItem,
    PartitioningType,
    ProcessDefinition,
    ResourceAccess,
    ResourceDefinition,
    ResourceScope,
    ScopeType,
    StartJobCommand,
    SubmitJobRequest,
    SubmitJobResult,
    TriggerType,
    WaitReason,
    WaitReasonCode,
    parse_strict_iso_date,
)

PublishHook = Callable[[Session, str, list[OutputItem]], None]
WakeupCallback = Callable[[], None]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ensure_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


class OrchestratorCore:
    """
    Ядро оркестратора (Блок 03).
    Инкапсулирует хранение, очередь, блокировки ресурсов, журнал событий и переходы состояний.
    """

    def __init__(
        self,
        db: Database,
        *,
        max_worker_slots: int = 2,
        on_state_change: WakeupCallback | None = None,
        publish_hook: PublishHook | None = None,
    ) -> None:
        if max_worker_slots < 1:
            raise ValueError("max_worker_slots must be >= 1")
        self.db = db
        self.max_worker_slots = max_worker_slots
        self._on_state_change = on_state_change
        self._publish_hook = publish_hook

    def set_wakeup_callback(self, callback: WakeupCallback | None) -> None:
        self._on_state_change = callback

    def _notify_dispatcher(self) -> None:
        if self._on_state_change is not None:
            self._on_state_change()

    @staticmethod
    def _next_queue_sequence(control: OrchestratorControl) -> int:
        control.last_queue_sequence += 1
        return control.last_queue_sequence

    @staticmethod
    def _next_event_id(control: OrchestratorControl) -> int:
        control.last_event_id += 1
        return control.last_event_id

    def _append_event(
        self,
        session: Session,
        control: OrchestratorControl,
        *,
        job_id: str,
        event_type: EventType,
        occurred_at: dt.datetime,
        status: JobStatus,
        payload: dict[str, Any],
    ) -> JobEventModel:
        event = JobEventModel(
            event_id=self._next_event_id(control),
            job_id=job_id,
            type=event_type.value,
            occurred_at=occurred_at,
            status=status.value,
            payload_json=payload,
        )
        session.add(event)
        return event

    @staticmethod
    def _job_to_dto(job: JobModel) -> JobDTO:
        trigger = JobTrigger(
            type=TriggerType(job.trigger_type),
            initiator_id=job.initiator_id,
            schedule_id=job.schedule_id,
            scheduled_for=_ensure_utc(job.scheduled_for),
        )
        accesses = [ResourceAccess.model_validate(a) for a in job.accesses_json]
        wait_reason = (
            WaitReason.model_validate(job.wait_reason_json)
            if job.wait_reason_json is not None
            else None
        )
        result = (
            JobResult.model_validate(job.result_json) if job.result_json is not None else None
        )
        error = JobError.model_validate(job.error_json) if job.error_json is not None else None
        return JobDTO(
            job_id=job.job_id,
            process_id=job.process_id,
            calculation_date=job.calculation_date,
            trigger=trigger,
            queue_sequence=job.queue_sequence,
            queued_at=_ensure_utc(job.queued_at),  # type: ignore[arg-type]
            accesses=accesses,
            status=JobStatus(job.status),
            started_at=_ensure_utc(job.started_at),
            finished_at=_ensure_utc(job.finished_at),
            wait_reason=wait_reason,
            result=result,
            error=error,
        )

    @staticmethod
    def _event_to_dto(ev: JobEventModel) -> JobEventDTO:
        return JobEventDTO(
            event_id=ev.event_id,
            job_id=ev.job_id,
            type=EventType(ev.type),
            occurred_at=_ensure_utc(ev.occurred_at),  # type: ignore[arg-type]
            status=JobStatus(ev.status),
            payload=ev.payload_json or {},
        )

    @staticmethod
    def _load_resources_map(session: Session) -> dict[str, ResourceDefinition]:
        rows = session.execute(select(ResourceModel)).scalars().all()
        return {
            row.resource_key: ResourceDefinition(
                resource_key=row.resource_key,
                description=row.description,
                partitioning=PartitioningType(row.partitioning),
            )
            for row in rows
        }

    @staticmethod
    def _resolve_idempotency_tuple(request: SubmitJobRequest) -> tuple[str, str, str] | None:
        if request.trigger.type == TriggerType.MANUAL:
            if not request.idempotency_key:
                return None
            return (
                TriggerType.MANUAL.value,
                request.trigger.initiator_id,
                request.idempotency_key,
            )
        assert request.trigger.schedule_id is not None
        assert request.trigger.scheduled_for is not None
        return (
            TriggerType.SCHEDULED.value,
            request.trigger.schedule_id,
            request.trigger.scheduled_for.astimezone(dt.timezone.utc).isoformat(),
        )

    def submit_job(self, request: SubmitJobRequest) -> SubmitJobResult:
        """
        Принимает ручную или регламентную заявку, валидирует процесс и дату,
        проверяет идемпотентность, вычисляет доступы и ставит задачу в QUEUED.
        """
        try:
            calc_date = parse_strict_iso_date(request.calculation_date)
        except ValueError as exc:
            raise ValidationError(
                code="INVALID_CALCULATION_DATE",
                message=str(exc),
                details={"calculationDate": request.calculation_date},
            ) from exc

        idem_tuple = self._resolve_idempotency_tuple(request)
        created = False

        with self.db.serialized_transaction() as (session, control):
            process = session.get(ProcessModel, request.process_id)
            if process is None:
                raise ValidationError(
                    code="UNKNOWN_PROCESS",
                    message=f"Unknown processId: {request.process_id}",
                    details={"processId": request.process_id},
                )

            if idem_tuple is not None:
                t_type, actor, key = idem_tuple
                existing_rec = session.execute(
                    select(IdempotencyRecordModel).where(
                        IdempotencyRecordModel.trigger_type == t_type,
                        IdempotencyRecordModel.scope_actor == actor,
                        IdempotencyRecordModel.idempotency_key == key,
                    )
                ).scalar_one_or_none()

                if existing_rec is not None:
                    if (
                        existing_rec.process_id != request.process_id
                        or existing_rec.calculation_date != calc_date
                    ):
                        raise IdempotencyConflictError(
                            code="IDEMPOTENCY_KEY_CONFLICT",
                            message=(
                                "Idempotency key was already used with different "
                                "processId or calculationDate"
                            ),
                            details={
                                "existingJobId": existing_rec.job_id,
                                "existingProcessId": existing_rec.process_id,
                                "existingCalculationDate": existing_rec.calculation_date,
                            },
                        )
                    existing_job = session.get(JobModel, existing_rec.job_id)
                    assert existing_job is not None
                    return SubmitJobResult(job=self._job_to_dto(existing_job), created=False)

            resources_map = self._load_resources_map(session)
            templates = [
                AccessTemplate.model_validate(t) for t in process.access_templates_json
            ]
            accesses = ResourceManager.expand_accesses(templates, resources_map, calc_date)

            now = _utc_now()
            job_id = str(uuid.uuid4())
            queue_seq = self._next_queue_sequence(control)

            job = JobModel(
                job_id=job_id,
                process_id=process.process_id,
                calculation_date=calc_date,
                trigger_type=request.trigger.type.value,
                initiator_id=request.trigger.initiator_id,
                schedule_id=request.trigger.schedule_id,
                scheduled_for=request.trigger.scheduled_for,
                queue_sequence=queue_seq,
                queued_at=now,
                accesses_json=[a.model_dump(mode="json", by_alias=True) for a in accesses],
                status=JobStatus.QUEUED.value,
            )
            session.add(job)
            session.flush()

            if idem_tuple is not None:
                t_type, actor, key = idem_tuple
                session.add(
                    IdempotencyRecordModel(
                        trigger_type=t_type,
                        scope_actor=actor,
                        idempotency_key=key,
                        process_id=process.process_id,
                        calculation_date=calc_date,
                        job_id=job_id,
                        created_at=now,
                    )
                )

            self._append_event(
                session,
                control,
                job_id=job_id,
                event_type=EventType.JOB_QUEUED,
                occurred_at=now,
                status=JobStatus.QUEUED,
                payload={
                    "processId": process.process_id,
                    "calculationDate": calc_date,
                    "trigger": request.trigger.model_dump(mode="json", by_alias=True),
                    "queueSequence": queue_seq,
                },
            )
            created = True
            dto = self._job_to_dto(job)

        if created:
            self._notify_dispatcher()
        return SubmitJobResult(job=dto, created=created)

    def dispatch_single_step(self) -> StartJobCommand | None:
        with self.db.serialized_transaction() as (session, control):
            active_slots = session.execute(
                select(func.count(JobModel.job_id)).where(
                    JobModel.status.in_([s.value for s in ACTIVE_SLOT_STATUSES])
                )
            ).scalar_one()

            res_rows = session.execute(select(ResourceReservationModel)).scalars().all()
            active_reservations = [
                ActiveReservation(
                    job_id=r.job_id,
                    resource_key=r.resource_key,
                    scope=ResourceScope(type=ScopeType(r.scope_type), date=r.scope_date),
                    mode=AccessMode(r.mode),
                )
                for r in res_rows
            ]

            candidates = (
                session.execute(
                    select(JobModel)
                    .where(JobModel.status.in_([JobStatus.QUEUED.value, JobStatus.WAITING.value]))
                    .order_by(JobModel.queue_sequence.asc())
                )
                .scalars()
                .all()
            )

            for job in candidates:
                accesses = [ResourceAccess.model_validate(a) for a in job.accesses_json]
                conflicts = ResourceManager.find_conflicts(accesses, active_reservations)

                if conflicts:
                    desired = WaitReason(
                        code=WaitReasonCode.RESOURCE_CONFLICT,
                        conflicts=conflicts,
                    )
                    self._apply_waiting_state(session, control, job, desired)
                    continue

                if active_slots >= self.max_worker_slots:
                    desired = WaitReason(code=WaitReasonCode.NO_WORKER_SLOT, conflicts=[])
                    self._apply_waiting_state(session, control, job, desired)
                    continue

                now = _utc_now()
                execution_id = str(uuid.uuid4())
                job.status = JobStatus.STARTING.value
                job.execution_id = execution_id
                job.wait_reason_json = None

                for acc in accesses:
                    session.add(
                        ResourceReservationModel(
                            job_id=job.job_id,
                            execution_id=execution_id,
                            resource_key=acc.resource_key,
                            scope_type=acc.scope.type.value,
                            scope_date=acc.scope.date,
                            mode=acc.mode.value,
                            reserved_at=now,
                        )
                    )

                self._append_event(
                    session,
                    control,
                    job_id=job.job_id,
                    event_type=EventType.JOB_RESERVED,
                    occurred_at=now,
                    status=JobStatus.STARTING,
                    payload={
                        "executionId": execution_id,
                        "reservedAccesses": [
                            a.model_dump(mode="json", by_alias=True) for a in accesses
                        ],
                    },
                )

                process = session.get(ProcessModel, job.process_id)
                assert process is not None
                return StartJobCommand(
                    job_id=job.job_id,
                    execution_id=execution_id,
                    process_id=job.process_id,
                    worker_handler=process.worker_handler,
                    calculation_date=job.calculation_date,
                    accesses=accesses,
                )

            return None

    def _apply_waiting_state(
        self,
        session: Session,
        control: OrchestratorControl,
        job: JobModel,
        desired: WaitReason,
    ) -> None:
        desired_json = desired.model_dump(mode="json", by_alias=True)
        now = _utc_now()

        if job.status == JobStatus.QUEUED.value:
            job.status = JobStatus.WAITING.value
            job.wait_reason_json = desired_json
            self._append_event(
                session,
                control,
                job_id=job.job_id,
                event_type=EventType.JOB_WAITING,
                occurred_at=now,
                status=JobStatus.WAITING,
                payload={"waitReason": desired_json},
            )
        elif job.wait_reason_json != desired_json:
            job.wait_reason_json = desired_json
            self._append_event(
                session,
                control,
                job_id=job.job_id,
                event_type=EventType.JOB_WAIT_REASON_CHANGED,
                occurred_at=now,
                status=JobStatus.WAITING,
                payload={"waitReason": desired_json},
            )

    def cancel_job(self, job_id: str, *, initiator_id: str = "user") -> JobDTO:
        with self.db.serialized_transaction() as (session, control):
            job = session.get(JobModel, job_id)
            if job is None:
                raise JobNotFoundError(
                    code="JOB_NOT_FOUND",
                    message=f"Job '{job_id}' not found",
                    details={"jobId": job_id},
                )

            current = JobStatus(job.status)
            if current not in (JobStatus.QUEUED, JobStatus.WAITING):
                raise InvalidStateTransitionError(
                    code="CANCEL_NOT_ALLOWED",
                    message=f"Cannot cancel job in status '{current.value}'",
                    details={"jobId": job_id, "status": current.value},
                )

            now = _utc_now()
            job.status = JobStatus.CANCELLED.value
            job.wait_reason_json = None
            job.finished_at = now

            self._append_event(
                session,
                control,
                job_id=job.job_id,
                event_type=EventType.JOB_CANCELLED,
                occurred_at=now,
                status=JobStatus.CANCELLED,
                payload={"initiatorId": initiator_id},
            )
            dto = self._job_to_dto(job)

        self._notify_dispatcher()
        return dto

    def confirm_started(self, job_id: str, execution_id: str) -> JobDTO:
        with self.db.serialized_transaction() as (session, control):
            job = self._require_job_for_execution(session, job_id, execution_id)
            current = JobStatus(job.status)

            if current == JobStatus.RUNNING:
                return self._job_to_dto(job)

            if current != JobStatus.STARTING:
                raise InvalidStateTransitionError(
                    code="INVALID_TRANSITION",
                    message=f"Cannot confirm start from status '{current.value}'",
                    details={"jobId": job_id, "status": current.value},
                )

            now = _utc_now()
            job.status = JobStatus.RUNNING.value
            job.started_at = now

            self._append_event(
                session,
                control,
                job_id=job.job_id,
                event_type=EventType.JOB_STARTED,
                occurred_at=now,
                status=JobStatus.RUNNING,
                payload={"executionId": execution_id},
            )
            return self._job_to_dto(job)

    def complete_job(
        self,
        job_id: str,
        execution_id: str,
        outputs: list[OutputItem],
    ) -> JobDTO:
        try:
            with self.db.serialized_transaction() as (session, control):
                job = self._require_job_for_execution(session, job_id, execution_id)
                current = JobStatus(job.status)

                if current == JobStatus.SUCCEEDED:
                    return self._job_to_dto(job)

                if current != JobStatus.RUNNING:
                    raise InvalidStateTransitionError(
                        code="INVALID_TRANSITION",
                        message=f"Cannot complete job from status '{current.value}'",
                        details={"jobId": job_id, "status": current.value},
                    )

                self._validate_outputs_against_write_accesses(job, outputs)

                now = _utc_now()
                for item in outputs:
                    scope_key = item.scope.canonical_key()
                    existing_val = session.get(ResourceValueModel, (item.resource_key, scope_key))
                    if existing_val is None:
                        session.add(
                            ResourceValueModel(
                                resource_key=item.resource_key,
                                scope_key=scope_key,
                                scope_type=item.scope.type.value,
                                scope_date=item.scope.date,
                                value_json=item.value,
                                updated_by_job_id=job.job_id,
                                updated_at=now,
                            )
                        )
                    else:
                        existing_val.value_json = item.value
                        existing_val.updated_by_job_id = job.job_id
                        existing_val.updated_at = now

                if self._publish_hook is not None:
                    self._publish_hook(session, job.job_id, outputs)

                released = self._release_reservations(session, job.job_id)
                result_obj = JobResult(outputs=outputs)
                job.status = JobStatus.SUCCEEDED.value
                job.result_json = result_obj.model_dump(mode="json", by_alias=True)
                job.error_json = None
                job.finished_at = now

                self._append_event(
                    session,
                    control,
                    job_id=job.job_id,
                    event_type=EventType.JOB_SUCCEEDED,
                    occurred_at=now,
                    status=JobStatus.SUCCEEDED,
                    payload={
                        "releasedAccesses": released,
                        "resultRef": {"jobId": job.job_id},
                    },
                )
                dto = self._job_to_dto(job)

        except OrchestratorError:
            raise
        except Exception as exc:
            return self.fail_job(
                job_id=job_id,
                execution_id=execution_id,
                error_code="RESULT_WRITE_ERROR",
                error_message=f"Failed to publish job result: {exc}",
            )

        self._notify_dispatcher()
        return dto

    def fail_job(
        self,
        job_id: str,
        execution_id: str,
        error_code: str,
        error_message: str,
    ) -> JobDTO:
        with self.db.serialized_transaction() as (session, control):
            job = self._require_job_for_execution(session, job_id, execution_id)
            current = JobStatus(job.status)

            if current == JobStatus.FAILED:
                return self._job_to_dto(job)

            if current not in (JobStatus.STARTING, JobStatus.RUNNING):
                raise InvalidStateTransitionError(
                    code="INVALID_TRANSITION",
                    message=f"Cannot fail job from status '{current.value}'",
                    details={"jobId": job_id, "status": current.value},
                )

            now = _utc_now()
            released = self._release_reservations(session, job.job_id)
            err = JobError(code=error_code, message=error_message)
            job.status = JobStatus.FAILED.value
            job.error_json = err.model_dump(mode="json", by_alias=True)
            job.result_json = None
            job.finished_at = now

            self._append_event(
                session,
                control,
                job_id=job.job_id,
                event_type=EventType.JOB_FAILED,
                occurred_at=now,
                status=JobStatus.FAILED,
                payload={
                    "releasedAccesses": released,
                    "error": err.model_dump(mode="json", by_alias=True),
                },
            )
            dto = self._job_to_dto(job)

        self._notify_dispatcher()
        return dto

    def fail_dispatch(self, job_id: str, execution_id: str, reason: str) -> JobDTO:
        return self.fail_job(
            job_id=job_id,
            execution_id=execution_id,
            error_code="DISPATCH_ERROR",
            error_message=reason,
        )

    @staticmethod
    def _require_job_for_execution(
        session: Session, job_id: str, execution_id: str
    ) -> JobModel:
        job = session.get(JobModel, job_id)
        if job is None:
            raise JobNotFoundError(
                code="JOB_NOT_FOUND",
                message=f"Job '{job_id}' not found",
                details={"jobId": job_id},
            )
        if job.execution_id != execution_id:
            raise ExecutionMismatchError(
                code="EXECUTION_MISMATCH",
                message="executionId does not match active execution for this job",
                details={"jobId": job_id, "executionId": execution_id},
            )
        return job

    @staticmethod
    def _validate_outputs_against_write_accesses(
        job: JobModel, outputs: list[OutputItem]
    ) -> None:
        declared_writes = {
            (acc.resource_key, acc.scope.canonical_key())
            for acc in (ResourceAccess.model_validate(a) for a in job.accesses_json)
            if acc.mode == AccessMode.WRITE
        }
        provided = [(item.resource_key, item.scope.canonical_key()) for item in outputs]
        if len(provided) != len(set(provided)) or set(provided) != declared_writes:
            raise RuntimeError(
                f"Provided outputs {sorted(provided)} do not match declared WRITE accesses "
                f"{sorted(declared_writes)}"
            )

    @staticmethod
    def _release_reservations(session: Session, job_id: str) -> list[dict[str, Any]]:
        rows = (
            session.execute(
                select(ResourceReservationModel).where(ResourceReservationModel.job_id == job_id)
            )
            .scalars()
            .all()
        )
        released = [
            ResourceAccess(
                resource_key=r.resource_key,
                scope=ResourceScope(type=ScopeType(r.scope_type), date=r.scope_date),
                mode=AccessMode(r.mode),
            ).model_dump(mode="json", by_alias=True)
            for r in rows
        ]
        session.execute(
            delete(ResourceReservationModel).where(ResourceReservationModel.job_id == job_id)
        )
        return released

    def list_processes(self) -> list[ProcessDefinition]:
        with self.db.session() as session:
            rows = (
                session.execute(select(ProcessModel).order_by(ProcessModel.process_id.asc()))
                .scalars()
                .all()
            )
            return [
                ProcessDefinition(
                    process_id=r.process_id,
                    name=r.name,
                    worker_handler=r.worker_handler,
                    access_templates=[
                        AccessTemplate.model_validate(t) for t in r.access_templates_json
                    ],
                )
                for r in rows
            ]

    def get_job(self, job_id: str) -> JobDTO:
        with self.db.session() as session:
            job = session.get(JobModel, job_id)
            if job is None:
                raise JobNotFoundError(
                    code="JOB_NOT_FOUND",
                    message=f"Job '{job_id}' not found",
                    details={"jobId": job_id},
                )
            return self._job_to_dto(job)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        process_id: str | None = None,
        calculation_date: str | None = None,
        trigger_type: TriggerType | None = None,
    ) -> list[JobDTO]:
        with self.db.session() as session:
            stmt = select(JobModel)
            if status is not None:
                stmt = stmt.where(JobModel.status == status.value)
            if process_id is not None:
                stmt = stmt.where(JobModel.process_id == process_id)
            if calculation_date is not None:
                stmt = stmt.where(JobModel.calculation_date == calculation_date)
            if trigger_type is not None:
                stmt = stmt.where(JobModel.trigger_type == trigger_type.value)

            stmt = stmt.order_by(JobModel.queue_sequence.asc())
            rows = session.execute(stmt).scalars().all()
            return [self._job_to_dto(r) for r in rows]

    def list_events(
        self,
        *,
        job_id: str | None = None,
        after_event_id: int | None = None,
        limit: int | None = None,
    ) -> list[JobEventDTO]:
        with self.db.session() as session:
            stmt = select(JobEventModel)
            if job_id is not None:
                stmt = stmt.where(JobEventModel.job_id == job_id)
            if after_event_id is not None:
                stmt = stmt.where(JobEventModel.event_id > after_event_id)
            stmt = stmt.order_by(JobEventModel.event_id.asc())
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [self._event_to_dto(r) for r in rows]

    def count_active_reservations(self, job_id: str | None = None) -> int:
        with self.db.session() as session:
            stmt = select(func.count(ResourceReservationModel.id))
            if job_id is not None:
                stmt = stmt.where(ResourceReservationModel.job_id == job_id)
            return int(session.execute(stmt).scalar_one())

    def get_resource_value(self, resource_key: str, scope: ResourceScope) -> Any | None:
        with self.db.session() as session:
            row = session.get(ResourceValueModel, (resource_key, scope.canonical_key()))
            return row.value_json if row is not None else None

    def set_resource_value_for_seed(
        self, resource_key: str, scope: ResourceScope, value: Any
    ) -> None:
        now = _utc_now()
        scope_key = scope.canonical_key()
        with self.db.serialized_transaction() as (session, _):
            existing = session.get(ResourceValueModel, (resource_key, scope_key))
            if existing is None:
                session.add(
                    ResourceValueModel(
                        resource_key=resource_key,
                        scope_key=scope_key,
                        scope_type=scope.type.value,
                        scope_date=scope.date,
                        value_json=value,
                        updated_by_job_id=None,
                        updated_at=now,
                    )
                )
            else:
                existing.value_json = value
                existing.updated_at = now
