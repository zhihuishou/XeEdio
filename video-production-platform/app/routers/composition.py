"""Video composition API endpoints."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import User, get_db
from app.schemas.composition import (
    ComposeRequest,
    ComposeResponse,
    CompositionStatusResponse,
)
from app.services.composition_service import CompositionService
from app.utils.auth import require_role
from app.utils.errors import AppError, ErrorCode, NotFoundError, StateTransitionError

logger = logging.getLogger("app.ffmpeg")

router = APIRouter(prefix="/api/composition", tags=["composition"])


@router.post("/{task_id}/compose", response_model=ComposeResponse)
def compose_video(
    task_id: str,
    body: ComposeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Trigger video composition for a task.

    Only allowed when task.status == 'tts_done'.
    Composes A-roll and B-roll assets with TTS audio using FFmpeg.
    After composition, task status transitions: video_done -> pending_review.
    """
    service = CompositionService(db)

    try:
        task = service.compose(
            task_id=task_id,
            a_roll_asset_ids=body.a_roll_assets,
            b_roll_asset_ids=body.b_roll_assets,
            transition=body.transition,
            resolution=body.resolution,
            bitrate=body.bitrate,
        )
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise NotFoundError(message=error_msg)
        if "status must be" in error_msg.lower():
            raise StateTransitionError(message=error_msg)
        raise AppError(
            message=error_msg,
            error_code=ErrorCode.FFMPEG_ERROR,
        )
    except RuntimeError as e:
        raise AppError(
            message=str(e),
            error_code=ErrorCode.FFMPEG_ERROR,
        )

    return ComposeResponse(
        task_id=task.id,
        status=task.status,
        video_path=task.video_path,
        video_resolution=task.video_resolution,
        video_duration=task.video_duration,
        video_file_size=task.video_file_size,
    )


@router.get("/{task_id}/status", response_model=CompositionStatusResponse)
def get_composition_status(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Query composition status for a task."""
    service = CompositionService(db)

    try:
        status = service.get_status(task_id)
    except ValueError as e:
        raise NotFoundError(message=str(e))

    return CompositionStatusResponse(**status)
