"""Unified error handling and response format for the Video Production Platform."""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Application error codes."""

    AUTH_FAILED = "AUTH_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    MISSING_FIELD = "MISSING_FIELD"
    COPY_LOCKED = "COPY_LOCKED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    REJECTION_REASON_REQUIRED = "REJECTION_REASON_REQUIRED"
    LLM_API_ERROR = "LLM_API_ERROR"
    LLM_API_TIMEOUT = "LLM_API_TIMEOUT"
    TTS_SYNTHESIS_FAILED = "TTS_SYNTHESIS_FAILED"
    FFMPEG_ERROR = "FFMPEG_ERROR"
    SUBTASK_FAILED = "SUBTASK_FAILED"
    MIXING_ERROR = "MIXING_ERROR"


class ErrorDetail(BaseModel):
    """Error detail model."""

    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    """Unified error response model."""

    error: ErrorDetail


# --- Custom Exception Classes ---


class AppError(Exception):
    """Base application error."""

    status_code: int = 500
    error_code: ErrorCode = ErrorCode.LLM_API_ERROR
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.message = message or self.__class__.message
        if error_code:
            self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


class AuthError(AppError):
    """Authentication failed (401)."""

    status_code = 401
    error_code = ErrorCode.AUTH_FAILED
    message = "Authentication failed"


class PermissionDeniedError(AppError):
    """Permission denied (403)."""

    status_code = 403
    error_code = ErrorCode.PERMISSION_DENIED
    message = "Permission denied"


class NotFoundError(AppError):
    """Resource not found (404)."""

    status_code = 404
    error_code = ErrorCode.NOT_FOUND
    message = "Resource not found"


class ValidationError(AppError):
    """Validation error (400)."""

    status_code = 400
    error_code = ErrorCode.MISSING_FIELD
    message = "Validation error"


class StateTransitionError(AppError):
    """Invalid state transition (409)."""

    status_code = 409
    error_code = ErrorCode.INVALID_STATE_TRANSITION
    message = "Invalid state transition"


# --- Exception Handlers ---


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle AppError and subclasses."""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=exc.error_code.value,
                message=exc.message,
                details=exc.details,
            )
        ).model_dump(),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred",
                details={},
            )
        ).model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
