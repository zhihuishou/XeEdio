"""TTS (Text-to-Speech) API endpoints for voice synthesis."""

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import User, get_db
from app.schemas.tts import (
    TTSPreviewRequest,
    TTSPreviewResponse,
    TTSSynthesizeRequest,
    TTSSynthesizeResponse,
    TTSVoiceItem,
    TTSVoicesResponse,
)
from app.services.tts_service import TTSService
from app.utils.auth import require_role
from app.utils.errors import AppError, ErrorCode, NotFoundError, StateTransitionError

logger = logging.getLogger("app.tts")

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/voices", response_model=TTSVoicesResponse)
def list_voices(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Get available TTS voice options."""
    service = TTSService(db)
    voices = service.get_voices()
    return TTSVoicesResponse(
        voices=[
            TTSVoiceItem(
                name=v["name"],
                display_name=v["display_name"],
                gender=v["gender"],
                language=v["language"],
            )
            for v in voices
        ]
    )


@router.post("/preview", response_model=TTSPreviewResponse)
async def preview_voice(
    body: TTSPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Preview a voice with a short text snippet."""
    service = TTSService(db)
    try:
        result = await service.preview(text=body.text, voice=body.voice)
    except RuntimeError as e:
        raise AppError(
            message=str(e),
            error_code=ErrorCode.TTS_SYNTHESIS_FAILED,
        )

    return TTSPreviewResponse(
        audio_path=result["audio_path"],
        duration=result["duration"],
        voice=result["voice"],
    )


@router.post("/{task_id}/synthesize", response_model=TTSSynthesizeResponse)
def synthesize_tts(
    task_id: str,
    body: TTSSynthesizeRequest = TTSSynthesizeRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Trigger TTS synthesis for a task.

    Only allowed when task.status == 'copy_confirmed'.
    Synthesizes the confirmed copywriting text and updates the task with
    audio path, duration, and transitions status to 'tts_done'.
    """
    service = TTSService(db)

    try:
        task = service.synthesize(task_id=task_id, voice=body.voice)
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise NotFoundError(message=error_msg)
        # State transition error
        raise StateTransitionError(message=error_msg)
    except RuntimeError as e:
        raise AppError(
            message=str(e),
            error_code=ErrorCode.TTS_SYNTHESIS_FAILED,
        )

    return TTSSynthesizeResponse(
        task_id=task.id,
        status=task.status,
        tts_audio_path=task.tts_audio_path,
        tts_duration=task.tts_duration,
        tts_voice=task.tts_voice,
    )
