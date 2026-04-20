"""Logging configuration and request ID middleware for the Video Production Platform."""

import logging
import os
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Context variable to store request ID across async boundaries
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

LOGS_DIR = "logs"


class RequestIdFilter(logging.Filter):
    """Inject request_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("-")
        return True


def _ensure_logs_dir() -> None:
    """Create logs directory if it doesn't exist."""
    os.makedirs(LOGS_DIR, exist_ok=True)


def _create_file_handler(
    filename: str, level: int = logging.DEBUG
) -> RotatingFileHandler:
    """Create a rotating file handler."""
    _ensure_logs_dir()
    handler = RotatingFileHandler(
        os.path.join(LOGS_DIR, filename),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [request_id=%(request_id)s] "
        "%(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(formatter)
    return handler


def setup_logging() -> None:
    """Configure application logging with file handlers for each domain."""
    _ensure_logs_dir()

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [request_id=%(request_id)s] "
        "%(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.addFilter(RequestIdFilter())
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Error log - all errors
    error_logger = logging.getLogger("app.error")
    error_logger.addHandler(_create_file_handler("error.log", logging.ERROR))
    error_logger.propagate = True

    # LLM log - LLM API calls
    llm_logger = logging.getLogger("app.llm")
    llm_logger.addHandler(_create_file_handler("llm.log", logging.DEBUG))
    llm_logger.propagate = True

    # FFmpeg log - FFmpeg execution
    ffmpeg_logger = logging.getLogger("app.ffmpeg")
    ffmpeg_logger.addHandler(_create_file_handler("ffmpeg.log", logging.DEBUG))
    ffmpeg_logger.propagate = True

    # Review log - review operations
    review_logger = logging.getLogger("app.review")
    review_logger.addHandler(_create_file_handler("review.log", logging.DEBUG))
    review_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Get a named logger."""
    return logging.getLogger(name)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that generates a unique request ID for each request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request_id_var.set(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
