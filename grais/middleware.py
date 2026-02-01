"""Middleware components for GRAIS API."""
from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict
from typing import Callable, Dict, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("grais")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Add request context (ID, timing) to all requests."""

    def __init__(self, app, time_func: Callable[[], float] = time.perf_counter):
        super().__init__(app)
        self.time_func = time_func

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.start_time = self.time_func()

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id

        duration_ms = (self.time_func() - request.state.start_time) * 1000
        logger.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "client_ip": self._get_client_ip(request),
            },
        )
        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Get client IP, considering proxy headers."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiting middleware."""

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        burst_size: int = 10,
        enabled: bool = True,
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.enabled = enabled
        self.request_counts: Dict[str, list] = defaultdict(list)
        self.window_seconds = 60

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.enabled:
            return await call_next(request)

        # Skip rate limiting for health checks
        if request.url.path in {"/health", "/metrics"}:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        current_time = time.time()

        # Clean old entries
        self.request_counts[client_ip] = [
            t for t in self.request_counts[client_ip]
            if current_time - t < self.window_seconds
        ]

        # Check rate limit
        if len(self.request_counts[client_ip]) >= self.requests_per_minute:
            from grais.errors import create_error_response

            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "request_count": len(self.request_counts[client_ip]),
                    "path": request.url.path,
                },
            )
            retry_after = int(
                self.window_seconds - (current_time - self.request_counts[client_ip][0])
            )
            response = create_error_response(
                error_code="rate_limit_exceeded",
                message="Rate limit exceeded. Please try again later.",
                status_code=429,
                details={"retry_after_seconds": max(1, retry_after)},
            )
            response.headers["Retry-After"] = str(max(1, retry_after))
            return response

        # Record request
        self.request_counts[client_ip].append(current_time)
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = max(0, self.requests_per_minute - len(self.request_counts[client_ip]))
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(current_time + self.window_seconds))
        
        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Get client IP, considering proxy headers."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Add structured logging context to requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Add logging context that can be used by handlers
        request.state.logging_context = {
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
        }
        return await call_next(request)


def setup_logging(level: str = "INFO", json_format: bool = False) -> None:
    """Configure structured logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    if json_format:
        # JSON format for production
        import json

        class JSONFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                log_data = {
                    "timestamp": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                # Add extra fields
                if hasattr(record, "request_id"):
                    log_data["request_id"] = record.request_id
                if hasattr(record, "duration_ms"):
                    log_data["duration_ms"] = record.duration_ms
                if hasattr(record, "status_code"):
                    log_data["status_code"] = record.status_code
                if hasattr(record, "path"):
                    log_data["path"] = record.path
                if hasattr(record, "method"):
                    log_data["method"] = record.method
                if hasattr(record, "client_ip"):
                    log_data["client_ip"] = record.client_ip
                # Include exception info
                if record.exc_info:
                    log_data["exception"] = self.formatException(record.exc_info)
                return json.dumps(log_data)

        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
    else:
        # Human-readable format for development
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s "
                "[%(filename)s:%(lineno)d]"
            )
        )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [handler]

    # Configure GRAIS logger
    grais_logger = logging.getLogger("grais")
    grais_logger.setLevel(log_level)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_cors_origins() -> list:
    """Get CORS origins from environment or defaults."""
    env_origins = os.getenv("CORS_ORIGINS", "")
    if env_origins:
        return [origin.strip() for origin in env_origins.split(",")]
    
    # Default: allow localhost for development
    if os.getenv("ENVIRONMENT", "development") == "production":
        # In production, require explicit CORS_ORIGINS
        return []
    
    # Development defaults
    return [
        "http://localhost:3000",
        "http://localhost:4173",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ]
