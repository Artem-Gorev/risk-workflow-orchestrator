from __future__ import annotations

from typing import Any


class OrchestratorError(Exception):
    """Базовая ошибка ядра оркестратора с кодом и HTTP-статусом для API."""

    http_status: int = 500

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ValidationError(OrchestratorError):
    """Неизвестный процесс, некорректная дата или неверные параметры (422)."""

    http_status = 422


class RegistryValidationError(OrchestratorError):
    """Ошибка конфигурации реестра процессов/ресурсов (например, DATE для GLOBAL)."""

    http_status = 422


class JobNotFoundError(OrchestratorError):
    """Задача с указанным jobId не найдена (404)."""

    http_status = 404


class InvalidStateTransitionError(OrchestratorError):
    """Недопустимый переход состояния (например, отмена после резервирования — 409)."""

    http_status = 409


class IdempotencyConflictError(OrchestratorError):
    """Повторный ключ идемпотентности с отличающимися параметрами заявки (409)."""

    http_status = 409


class ExecutionMismatchError(OrchestratorError):
    """Несовпадение executionId при отчёте от Worker (409)."""

    http_status = 409
