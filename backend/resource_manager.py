from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from orchestrator_core.exceptions import RegistryValidationError
from orchestrator_core.schemas import (
    AccessMode,
    AccessTemplate,
    ConflictDetail,
    PartitioningType,
    ResourceAccess,
    ResourceDefinition,
    ResourceScope,
    ScopeType,
    TemplateScopeType,
)


@dataclass(frozen=True)
class ActiveReservation:
    job_id: str
    resource_key: str
    scope: ResourceScope
    mode: AccessMode


class ResourceManager:
    """
    Чистая логика области доступа и проверки совместимости READ/WRITE.
    Атомарность записи/снятия резервирований обеспечивается транзакцией в OrchestratorCore.
    """

    @staticmethod
    def intersect_scopes(first: ResourceScope, second: ResourceScope) -> ResourceScope | None:
        """
        Возвращает область пересечения для одного resourceKey или None:
        - GLOBAL ∩ GLOBAL -> GLOBAL
        - GLOBAL ∩ DATE(d) -> DATE(d)
        - DATE(d1) ∩ DATE(d2) -> DATE(d1) при d1 == d2, иначе None
        """
        if first.type == ScopeType.GLOBAL and second.type == ScopeType.GLOBAL:
            return ResourceScope(type=ScopeType.GLOBAL)
        if first.type == ScopeType.GLOBAL and second.type == ScopeType.DATE:
            return ResourceScope(type=ScopeType.DATE, date=second.date)
        if first.type == ScopeType.DATE and second.type == ScopeType.GLOBAL:
            return ResourceScope(type=ScopeType.DATE, date=first.date)
        if first.date == second.date:
            return ResourceScope(type=ScopeType.DATE, date=first.date)
        return None

    @staticmethod
    def is_compatible(requested_mode: AccessMode, active_mode: AccessMode) -> bool:
        """Матрица совместимости FR-03: совместима только пара READ / READ."""
        return requested_mode == AccessMode.READ and active_mode == AccessMode.READ

    @classmethod
    def validate_templates(
        cls,
        templates: Iterable[AccessTemplate],
        resources_by_key: dict[str, ResourceDefinition],
    ) -> None:
        """
        Проверяет корректность шаблонов процесса:
        - неизвестный resourceKey запрещён;
        - дневной шаблон JOB_DATE для GLOBAL-ресурса запрещён.
        """
        for tpl in templates:
            resource = resources_by_key.get(tpl.resource_key)
            if resource is None:
                raise RegistryValidationError(
                    code="UNKNOWN_RESOURCE_KEY",
                    message=f"Unknown resource key in process template: {tpl.resource_key}",
                    details={"resourceKey": tpl.resource_key},
                )
            if (
                resource.partitioning == PartitioningType.GLOBAL
                and tpl.scope_template == TemplateScopeType.JOB_DATE
            ):
                raise RegistryValidationError(
                    code="INVALID_SCOPE_FOR_GLOBAL_RESOURCE",
                    message=(
                        f"JOB_DATE scope cannot be applied to GLOBAL resource "
                        f"'{tpl.resource_key}'"
                    ),
                    details={"resourceKey": tpl.resource_key},
                )

    @classmethod
    def expand_accesses(
        cls,
        templates: Iterable[AccessTemplate],
        resources_by_key: dict[str, ResourceDefinition],
        calculation_date: str,
    ) -> list[ResourceAccess]:
        """
        Разворачивает шаблоны доступов процесса в конкретный снимок ResourceAccess[].
        Если для одной пары (resourceKey, scope) указаны и READ, и WRITE — фиксируется WRITE.
        """
        cls.validate_templates(templates, resources_by_key)

        merged: dict[tuple[str, str], ResourceAccess] = {}
        for tpl in templates:
            if tpl.scope_template == TemplateScopeType.JOB_DATE:
                scope = ResourceScope(type=ScopeType.DATE, date=calculation_date)
            else:
                scope = ResourceScope(type=ScopeType.GLOBAL)

            key = (tpl.resource_key, scope.canonical_key())
            existing = merged.get(key)
            if existing is None:
                merged[key] = ResourceAccess(
                    resource_key=tpl.resource_key,
                    scope=scope,
                    mode=tpl.mode,
                )
            elif tpl.mode == AccessMode.WRITE and existing.mode == AccessMode.READ:
                merged[key] = ResourceAccess(
                    resource_key=tpl.resource_key,
                    scope=scope,
                    mode=AccessMode.WRITE,
                )

        return sorted(
            merged.values(),
            key=lambda a: (a.resource_key, a.scope.type.value, a.scope.date or "", a.mode.value),
        )

    @classmethod
    def find_conflicts(
        cls,
        requested: Iterable[ResourceAccess],
        active: Iterable[ActiveReservation],
    ) -> list[ConflictDetail]:
        """
        Находит все несовместимые пересечения запрошенного набора с активными резервированиями.
        Результат отсортирован и дедуплицирован для стабильного сравнения waitReason.
        """
        conflicts: list[ConflictDetail] = []
        seen: set[tuple[str, str, str, str, str]] = set()

        active_list = list(active)
        for req in requested:
            for res in active_list:
                if req.resource_key != res.resource_key:
                    continue
                overlap = cls.intersect_scopes(req.scope, res.scope)
                if overlap is None:
                    continue
                if cls.is_compatible(req.mode, res.mode):
                    continue

                dedup_key = (
                    req.resource_key,
                    overlap.canonical_key(),
                    req.mode.value,
                    res.mode.value,
                    res.job_id,
                )
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                conflicts.append(
                    ConflictDetail(
                        resource_key=req.resource_key,
                        scope=overlap,
                        requested_mode=req.mode,
                        active_mode=res.mode,
                        blocking_job_id=res.job_id,
                    )
                )

        conflicts.sort(
            key=lambda c: (
                c.resource_key,
                c.scope.type.value,
                c.scope.date or "",
                c.blocking_job_id,
                c.requested_mode.value,
                c.active_mode.value,
            )
        )
        return conflicts
