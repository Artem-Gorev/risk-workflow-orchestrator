from __future__ import annotations

import datetime as dt
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TriggerType(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"


class PartitioningType(str, Enum):
    BY_DATE = "BY_DATE"
    GLOBAL = "GLOBAL"


class ScopeType(str, Enum):
    DATE = "DATE"
    GLOBAL = "GLOBAL"


class TemplateScopeType(str, Enum):
    JOB_DATE = "JOB_DATE"
    GLOBAL = "GLOBAL"


class AccessMode(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    WAITING = "WAITING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset({
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})

ACTIVE_SLOT_STATUSES = frozenset({
    JobStatus.STARTING,
    JobStatus.RUNNING,
})


class WaitReasonCode(str, Enum):
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    NO_WORKER_SLOT = "NO_WORKER_SLOT"


class EventType(str, Enum):
    JOB_QUEUED = "JOB_QUEUED"
    JOB_WAITING = "JOB_WAITING"
    JOB_WAIT_REASON_CHANGED = "JOB_WAIT_REASON_CHANGED"
    JOB_RESERVED = "JOB_RESERVED"
    JOB_STARTED = "JOB_STARTED"
    JOB_SUCCEEDED = "JOB_SUCCEEDED"
    JOB_FAILED = "JOB_FAILED"
    JOB_CANCELLED = "JOB_CANCELLED"


def parse_strict_iso_date(value: Any) -> str:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value.isoformat()
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError("calculationDate must be a valid date in YYYY-MM-DD format")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid calendar date: {value}") from exc
    return parsed.isoformat()


class ResourceScope(CamelModel):
    type: ScopeType
    date: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> ResourceScope:
        if self.type == ScopeType.DATE:
            if not self.date:
                raise ValueError("date is required for DATE scope")
            self.date = parse_strict_iso_date(self.date)
        else:
            if self.date is not None:
                raise ValueError("date must be omitted for GLOBAL scope")
        return self

    def canonical_key(self) -> str:
        if self.type == ScopeType.GLOBAL:
            return "GLOBAL"
        return f"DATE:{self.date}"


class ResourceDefinition(CamelModel):
    resource_key: str
    description: str
    partitioning: PartitioningType


class AccessTemplate(CamelModel):
    resource_key: str
    scope_template: TemplateScopeType
    mode: AccessMode


class ProcessDefinition(CamelModel):
    process_id: str
    name: str
    worker_handler: str
    access_templates: list[AccessTemplate]


class ResourceAccess(CamelModel):
    resource_key: str
    scope: ResourceScope
    mode: AccessMode


class ConflictDetail(CamelModel):
    resource_key: str
    scope: ResourceScope
    requested_mode: AccessMode
    active_mode: AccessMode
    blocking_job_id: str


class WaitReason(CamelModel):
    code: WaitReasonCode
    conflicts: list[ConflictDetail] = Field(default_factory=list)


class JobTrigger(CamelModel):
    type: TriggerType
    initiator_id: str
    schedule_id: str | None = None
    scheduled_for: dt.datetime | None = None

    @model_validator(mode="after")
    def validate_trigger(self) -> JobTrigger:
        if self.type == TriggerType.SCHEDULED:
            if not self.schedule_id or self.scheduled_for is None:
                raise ValueError(
                    "scheduleId and scheduledFor are required for SCHEDULED trigger"
                )
            if self.scheduled_for.tzinfo is None:
                self.scheduled_for = self.scheduled_for.replace(tzinfo=dt.timezone.utc)
            else:
                self.scheduled_for = self.scheduled_for.astimezone(dt.timezone.utc)
        return self


class OutputItem(CamelModel):
    resource_key: str
    scope: ResourceScope
    value: Any


class JobResult(CamelModel):
    outputs: list[OutputItem]


class JobError(CamelModel):
    code: str
    message: str


class JobDTO(CamelModel):
    job_id: str
    process_id: str
    calculation_date: str
    trigger: JobTrigger
    queue_sequence: int
    queued_at: dt.datetime
    accesses: list[ResourceAccess]
    status: JobStatus
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    wait_reason: WaitReason | None = None
    result: JobResult | None = None
    error: JobError | None = None


class JobEventDTO(CamelModel):
    event_id: int
    job_id: str
    type: EventType
    occurred_at: dt.datetime
    status: JobStatus
    payload: dict[str, Any] = Field(default_factory=dict)


class SubmitJobRequest(CamelModel):
    process_id: str
    calculation_date: str | None = None
    trigger: JobTrigger
    idempotency_key: str | None = None


class SubmitJobResult(CamelModel):
    job: JobDTO
    created: bool  # True -> API отдаёт 201, False (повтор доставки) -> API отдаёт 200


class StartJobCommand(CamelModel):
    job_id: str
    execution_id: str
    process_id: str
    worker_handler: str
    calculation_date: str
    accesses: list[ResourceAccess]
