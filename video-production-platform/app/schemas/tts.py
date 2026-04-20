"""Pydantic schemas for TTS (Text-to-Speech) service."""

from typing import Optional

from pydantic import BaseModel, Field


class TTSSynthesizeRequest(BaseModel):
    """Request body for triggering TTS synthesis."""

    voice: Optional[str] = Field(None, description="Voice name (e.g., zh-CN-XiaoxiaoNeural)")


class TTSSynthesizeResponse(BaseModel):
    """Response for TTS synthesis."""

    task_id: str
    status: str
    tts_audio_path: str
    tts_duration: float
    tts_voice: str
    message: str = "语音合成完成"


class TTSVoiceItem(BaseModel):
    """A single voice option."""

    name: str
    display_name: str
    gender: str
    language: str


class TTSVoicesResponse(BaseModel):
    """Response for listing available voices."""

    voices: list[TTSVoiceItem]


class TTSPreviewRequest(BaseModel):
    """Request body for TTS preview."""

    text: str = Field(..., min_length=1, max_length=200, description="Short text to preview")
    voice: Optional[str] = Field(None, description="Voice name for preview")


class TTSPreviewResponse(BaseModel):
    """Response for TTS preview."""

    audio_path: str
    duration: float
    voice: str
