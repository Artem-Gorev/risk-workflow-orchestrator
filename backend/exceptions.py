from __future__ import annotations

from typing import Any


class OrchestratorError(Exception):
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
    http_status = 422


class RegistryValidationError(OrchestratorError):
    http_status = 422


class JobNotFoundError(OrchestratorError):
    http_status = 404


class InvalidStateTransitionError(OrchestratorError):
    http_status = 409


class IdempotencyConflictError(OrchestratorError):
    http_status = 409


class ExecutionMismatchError(OrchestratorError):
    http_status = 409
