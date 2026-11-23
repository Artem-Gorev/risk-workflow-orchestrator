from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator_core.models import ProcessModel, ResourceModel
from orchestrator_core.resource_manager import ResourceManager
from orchestrator_core.schemas import (
    AccessMode,
    AccessTemplate,
    PartitioningType,
    ProcessDefinition,
    ResourceDefinition,
    TemplateScopeType,
)

DEFAULT_RESOURCES: list[ResourceDefinition] = [
    ResourceDefinition(
        resource_key="credit_exposures",
        description="Просроченные суммы по кредитному портфелю",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="market_positions",
        description="Суммы по рыночным позициям",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="cash_flows",
        description="Поступления и выплаты (денежные потоки)",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="parameter_inputs",
        description="Исходные проценты для расчёта коэффициентов",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="risk_parameters",
        description="Общие коэффициенты расчёта рисков",
        partitioning=PartitioningType.GLOBAL,
    ),
    ResourceDefinition(
        resource_key="credit_risk",
        description="Оценка кредитного риска",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="market_risk",
        description="Оценка рыночного риска",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="liquidity_risk",
        description="Оценка дефицита ликвидности",
        partitioning=PartitioningType.BY_DATE,
    ),
    ResourceDefinition(
        resource_key="risk_summary",
        description="Сводный показатель рисков (сумма трёх оценок)",
        partitioning=PartitioningType.BY_DATE,
    ),
]

DEFAULT_PROCESSES: list[ProcessDefinition] = [
    ProcessDefinition(
        process_id="credit-risk",
        name="Расчёт кредитного риска",
        worker_handler="credit_risk",
        access_templates=[
            AccessTemplate(
                resource_key="credit_exposures",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="risk_parameters",
                scope_template=TemplateScopeType.GLOBAL,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="credit_risk",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.WRITE,
            ),
        ],
    ),
    ProcessDefinition(
        process_id="market-risk",
        name="Расчёт рыночного риска",
        worker_handler="market_risk",
        access_templates=[
            AccessTemplate(
                resource_key="market_positions",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="risk_parameters",
                scope_template=TemplateScopeType.GLOBAL,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="market_risk",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.WRITE,
            ),
        ],
    ),
    ProcessDefinition(
        process_id="liquidity-risk",
        name="Расчёт риска ликвидности",
        worker_handler="liquidity_risk",
        access_templates=[
            AccessTemplate(
                resource_key="cash_flows",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="liquidity_risk",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.WRITE,
            ),
        ],
    ),
    ProcessDefinition(
        process_id="risk-summary",
        name="Расчёт сводного показателя",
        worker_handler="risk_summary",
        access_templates=[
            AccessTemplate(
                resource_key="credit_risk",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="market_risk",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="liquidity_risk",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="risk_summary",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.WRITE,
            ),
        ],
    ),
    ProcessDefinition(
        process_id="refresh-parameters",
        name="Обновление общих коэффициентов",
        worker_handler="refresh_parameters",
        access_templates=[
            AccessTemplate(
                resource_key="parameter_inputs",
                scope_template=TemplateScopeType.JOB_DATE,
                mode=AccessMode.READ,
            ),
            AccessTemplate(
                resource_key="risk_parameters",
                scope_template=TemplateScopeType.GLOBAL,
                mode=AccessMode.WRITE,
            ),
        ],
    ),
]


def register_resource(session: Session, resource: ResourceDefinition) -> None:
    existing = session.get(ResourceModel, resource.resource_key)
    if existing is None:
        session.add(
            ResourceModel(
                resource_key=resource.resource_key,
                description=resource.description,
                partitioning=resource.partitioning.value,
            )
        )
    else:
        existing.description = resource.description
        existing.partitioning = resource.partitioning.value
    session.flush()


def register_process(session: Session, process: ProcessDefinition) -> None:
    resources = {
        row.resource_key: ResourceDefinition(
            resource_key=row.resource_key,
            description=row.description,
            partitioning=PartitioningType(row.partitioning),
        )
        for row in session.execute(select(ResourceModel)).scalars().all()
    }
    ResourceManager.validate_templates(process.access_templates, resources)

    serialized_templates = [
        tpl.model_dump(mode="json", by_alias=True) for tpl in process.access_templates
    ]
    existing = session.get(ProcessModel, process.process_id)
    if existing is None:
        session.add(
            ProcessModel(
                process_id=process.process_id,
                name=process.name,
                worker_handler=process.worker_handler,
                access_templates_json=serialized_templates,
            )
        )
    else:
        existing.name = process.name
        existing.worker_handler = process.worker_handler
        existing.access_templates_json = serialized_templates
    session.flush()


def seed_default_registry(session: Session) -> None:
    for res in DEFAULT_RESOURCES:
        register_resource(session, res)
    for proc in DEFAULT_PROCESSES:
        register_process(session, proc)
