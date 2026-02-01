"""Structured error handling for GRAIS API."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("grais.errors")


class GRAISError(Exception):
    """Base exception for GRAIS application errors."""

    def __init__(
        self,
        message: str,
        error_code: str = "internal_error",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)

    def to_response(self) -> Dict[str, Any]:
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ConfigNotFoundError(GRAISError):
    """Configuration file not found."""

    def __init__(self, config_name: str):
        super().__init__(
            message=f"Configuration '{config_name}' not found",
            error_code="config_not_found",
            status_code=404,
            details={"config_name": config_name},
        )


class ValidationError(GRAISError):
    """Input validation failed."""

    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Dict] = None):
        super().__init__(
            message=message,
            error_code="validation_error",
            status_code=422,
            details={**(details or {}), "field": field} if field else (details or {}),
        )


class OptimizationError(GRAISError):
    """Optimization solver failed."""

    def __init__(self, message: str, solver_status: Optional[str] = None):
        super().__init__(
            message=message,
            error_code="optimization_failed",
            status_code=500,
            details={"solver_status": solver_status} if solver_status else {},
        )


class DataLoadError(GRAISError):
    """Failed to load data from source."""

    def __init__(self, message: str, source: Optional[str] = None):
        super().__init__(
            message=message,
            error_code="data_load_error",
            status_code=502,
            details={"source": source} if source else {},
        )


class RateLimitError(GRAISError):
    """Rate limit exceeded."""

    def __init__(self, retry_after: int = 60):
        super().__init__(
            message="Rate limit exceeded. Please try again later.",
            error_code="rate_limit_exceeded",
            status_code=429,
            details={"retry_after_seconds": retry_after},
        )


class ServiceUnavailableError(GRAISError):
    """Service temporarily unavailable."""

    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(
            message=message,
            error_code="service_unavailable",
            status_code=503,
        )


def create_error_response(
    error_code: str,
    message: str,
    status_code: int = 500,
    details: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """Create a standardized error response."""
    content = {
        "error": error_code,
        "message": message,
        "details": details or {},
    }
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=content)


async def grais_exception_handler(request: Request, exc: GRAISError) -> JSONResponse:
    """Handle GRAIS-specific exceptions."""
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "GRAIS error: %s",
        exc.message,
        extra={
            "error_code": exc.error_code,
            "status_code": exc.status_code,
            "request_id": request_id,
            "details": exc.details,
        },
    )
    return create_error_response(
        error_code=exc.error_code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        request_id=request_id,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle Pydantic validation exceptions."""
    from pydantic import ValidationError as PydanticValidationError

    request_id = getattr(request.state, "request_id", None)

    if isinstance(exc, PydanticValidationError):
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            })
        return create_error_response(
            error_code="validation_error",
            message="Request validation failed",
            status_code=422,
            details={"errors": errors},
            request_id=request_id,
        )

    return create_error_response(
        error_code="validation_error",
        message=str(exc),
        status_code=422,
        request_id=request_id,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception",
        extra={"request_id": request_id, "path": request.url.path},
    )
    # Don't expose internal error details in production
    return create_error_response(
        error_code="internal_error",
        message="An unexpected error occurred. Please try again later.",
        status_code=500,
        request_id=request_id,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTP exceptions."""
    request_id = getattr(request.state, "request_id", None)
    return create_error_response(
        error_code="http_error",
        message=str(exc.detail),
        status_code=exc.status_code,
        request_id=request_id,
    )
