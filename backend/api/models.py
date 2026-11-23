from datetime import date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Header
from pydantic import BaseModel, ConfigDict, Field, field_validator

Status = Literal["QUEUED", "WAITING", "STARTING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
Mode = Literal["READ", "WRITE"]
Source = Literal["MANUAL", "SCHEDULED"]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", pattern=r"^[!-~]{1,128}$")]


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Submission(Empty):
    priority: int | None = Field(default=None, ge=1, le=9)
    processId: str
    calculationDate: date

    @field_validator("calculationDate", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError("Ожидается дата YYYY-MM-DD")
        return value


class Scope(BaseModel):
    type: Literal["DATE", "GLOBAL"]
    date: str | None = None


class Access(BaseModel):
    resourceKey: str
    scope: Scope
    mode: Mode


class Conflict(BaseModel):
    resourceKey: str
    scope: Scope
    requestedMode: Mode
    activeMode: Mode
    blockingJobId: UUID


class Reason(BaseModel):
    code: Literal["RESOURCE_CONFLICT", "NO_WORKER_SLOT", "QUEUE_FAIRNESS"]
    conflicts: list[Conflict]


class Trigger(BaseModel):
    type: Source
    initiatorId: str
    restartedFrom: UUID | None = None
    demoId: UUID | None = None
    scheduleId: str | None = None
    scheduledFor: datetime | None = None


class Failure(BaseModel):
    code: str
    message: str


class Cancellation(BaseModel):
    jobId: UUID
    status: Literal["CANCELLED"]


class Receipt(BaseModel):
    jobId: UUID
    status: Status
    queuedAt: datetime


class Revision(BaseModel):
    resourceKey: str
    scope: Scope
    revision: int


class InputVersion(Revision):
    sourceJobId: UUID | None


class Output(Revision):
    value: str | dict[str, str]


class Result(BaseModel):
    outputs: list[Output]
    inputVersions: list[InputVersion]


class InputLineage(InputVersion):
    sourceProcessId: str | None
    coefficients: InputVersion | None


class Provenance(BaseModel):
    inputs: list[InputLineage]
    coefficientStatus: Literal["SAME_REVISION", "MIXED_REVISIONS", "UNAVAILABLE", "NOT_APPLICABLE"]


class Job(Receipt):
    hasSnapshot: bool
    attempt: int
    retryAt: datetime | None
    batchId: UUID | None
    replayOf: UUID | None
    priority: int
    leaseUntil: datetime | None
    processId: str
    processVersion: int
    calculationDate: date
    trigger: Trigger
    queueSequence: int
    accesses: list[Access]
    startedAt: datetime | None
    finishedAt: datetime | None
    result: Result | None
    error: Failure | None
    waitReason: Reason | None


class JobPage(BaseModel):
    items: list[Job]
    nextCursor: str | None


class Event(BaseModel):
    eventId: int
    jobId: UUID
    type: str
    occurredAt: datetime
    status: Status
    payload: dict


class EventPage(BaseModel):
    items: list[Event]
    nextAfterEventId: int


class ProcessInfo(BaseModel):
    joinActive: bool
    handler: str
    priority: int
    executionSeconds: float
    isActive: bool
    bindings: dict[str, str]
    processId: str
    processVersion: int
    name: str
    description: str
    accessTemplates: list[dict[str, str]]


class Catalog(BaseModel):
    items: list[ProcessInfo]


class Overview(BaseModel):
    serverTime: datetime
    countsByStatus: dict[str, int]
    occupiedSlots: int
    maxSlots: int
    resourceWaitingCount: int
    slotWaitingCount: int


class ScheduleRecord(BaseModel):
    id: str
    process: str
    due: datetime | None
    day: date | None
    daily: bool
    local_time: time
    weekday: int | None
    enabled: bool


class SimulationRun(BaseModel):
    durationTicks: int
    conflictingPairs: int
    queueTicks: int
    p95WaitTicks: int


class SimulatedRequest(BaseModel):
    id: str
    process: str
    user: str
    arrival: int
    duration: int
    access: Access


class SimulationResult(BaseModel):
    seed: int
    requests: list[SimulatedRequest]
    fifo: SimulationRun
    managed: SimulationRun
    unmanaged: SimulationRun


class Forecast(BaseModel):
    conflictsNow: list[Conflict]
    samples: int
    historicalConflictRate: float | None


class DemoBatch(BaseModel):
    demoId: UUID
    jobs: list[UUID]


class ScheduleSlot(BaseModel):
    schedule_id: str
    planned: datetime
    observed: datetime
    status: str
    reason: str | None
    job_id: UUID | None


class DemoStatus(DemoBatch):
    scheduleSlot: ScheduleSlot | None
    items: list[Job]
    status: Literal["PENDING", "DELIVERED", "FAILED"]
    error: str | None


class Insight(BaseModel):
    answer: str
    evidence: dict | list


class Hotspot(BaseModel):
    resourceKey: str
    waitSeconds: float
    shareOfWait: float


class ProcessStatistics(BaseModel):
    process: str
    jobs: int
    failures: int
    runtime: float | None
    blocked: int


class LoadPoint(BaseModel):
    minute: datetime
    busySeconds: float


class AnalyticsReport(BaseModel):
    days: int
    totalJobs: int
    failedJobs: int
    meanRuntimeSeconds: float | None
    waitSeconds: float
    resources: list[Hotspot]
    processes: list[ProcessStatistics]
    load: list[LoadPoint]
    recentEvents: list[dict]
    conflictedJobs: int


class SyntheticCaseInfo(BaseModel):
    calculationDate: date
    name: str
    description: str
    inputs: dict[str, list[int]]
    expected: dict[str, str | dict[str, str]]
    expectedErrors: dict[str, str]


class SyntheticCaseCatalog(BaseModel):
    startDate: date
    endDate: date
    factors: dict[str, str]
    factorRevision: int
    items: list[SyntheticCaseInfo]
