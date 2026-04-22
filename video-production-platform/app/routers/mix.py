"""Smart video mixing API endpoints.

Provides endpoints for creating mix tasks, checking status, submitting reviews,
retrying failed tasks, searching Pexels videos, downloading videos, and
generating keywords via LLM.
"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import User, get_db
from app.schemas.mix import (
    KeywordGenerateRequest,
    KeywordGenerateResponse,
    MixCreateRequest,
    MixCreateResponse,
    MixStatusResponse,
    PexelsDownloadRequest,
    PexelsDownloadResponse,
    PexelsSearchRequest,
    PexelsSearchResponse,
    PexelsVideoItem,
    RetryResponse,
    SubmitReviewResponse,
)
from app.services.external_config import ExternalConfig
from app.services.mixing_service import MixingService
from app.services.pexels_service import PexelsService
from app.utils.auth import require_role
from app.utils.errors import AppError, ErrorCode

logger = logging.getLogger("app.mix")

router = APIRouter(prefix="/api/mix", tags=["mix"])


# ------------------------------------------------------------------
# 6.1  POST /api/mix/create
# ------------------------------------------------------------------

@router.post("/create", response_model=MixCreateResponse)
def create_mix(
    body: MixCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Create a new mixing task and start background processing."""
    service = MixingService(db)
    task = service.create_mix_task(body, current_user.id)
    return MixCreateResponse(
        task_id=task.id,
        status=task.status,
        message="混剪任务已创建",
    )


# ------------------------------------------------------------------
# 6.2  GET /api/mix/{task_id}/status
# ------------------------------------------------------------------

@router.get("/{task_id}/status", response_model=MixStatusResponse)
def get_mix_status(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Query the current status of a mixing task."""
    service = MixingService(db)
    status_data = service.get_status(task_id)
    return MixStatusResponse(**status_data)


# ------------------------------------------------------------------
# 6.3  POST /api/mix/{task_id}/submit-review
# ------------------------------------------------------------------

@router.post("/{task_id}/submit-review", response_model=SubmitReviewResponse)
def submit_review(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Submit a completed mixing task for review."""
    service = MixingService(db)
    task = service.submit_review(task_id)
    return SubmitReviewResponse(
        task_id=task.id,
        status=task.status,
        message="已提交审核",
    )


# ------------------------------------------------------------------
# 6.4  POST /api/mix/{task_id}/retry
# ------------------------------------------------------------------

@router.post("/{task_id}/retry", response_model=RetryResponse)
def retry_mix(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Retry a failed or rejected mixing task."""
    service = MixingService(db)
    task = service.retry(task_id)
    return RetryResponse(
        task_id=task.id,
        status=task.status,
        message="已重新开始混剪",
    )


# ------------------------------------------------------------------
# 6.5  POST /api/mix/pexels/search
# ------------------------------------------------------------------

@router.post("/pexels/search", response_model=PexelsSearchResponse)
def search_pexels(
    body: PexelsSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Search Pexels for stock videos matching keywords and aspect ratio."""
    service = PexelsService(db)
    result = service.search_videos(
        keywords=body.keywords,
        aspect_ratio=body.aspect_ratio,
        per_page=body.per_page,
    )
    return PexelsSearchResponse(
        videos=[PexelsVideoItem(**v) for v in result["videos"]],
        total=result["total"],
    )


# ------------------------------------------------------------------
# 6.6  POST /api/mix/pexels/download
# ------------------------------------------------------------------

@router.post("/pexels/download", response_model=PexelsDownloadResponse)
def download_pexels(
    body: PexelsDownloadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Download a Pexels video and create a local Asset record."""
    service = PexelsService(db)
    asset = service.download_video(body.video_url, current_user.id)
    return PexelsDownloadResponse(
        asset_id=asset.id,
        file_path=asset.file_path,
    )


# ------------------------------------------------------------------
# 6.7  POST /api/mix/keywords/generate
# ------------------------------------------------------------------

KEYWORD_SYSTEM_PROMPT = (
    "You are a professional video production assistant. "
    "Given a video topic, generate exactly 5 English search keywords "
    "suitable for finding B-roll stock footage on Pexels. "
    "Return ONLY a JSON array of 5 strings, no explanation. "
    "Example: [\"sunset cityscape\", \"office workspace\", \"coffee shop\", \"aerial view\", \"people walking\"]"
)


@router.post("/keywords/generate", response_model=KeywordGenerateResponse)
def generate_keywords(
    body: KeywordGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Generate B-roll search keywords using LLM based on a topic."""
    ext = ExternalConfig.get_instance()
    provider_id = ext.get_default_provider()
    provider = ext.get_llm_provider(provider_id)

    if not provider or not provider.get("api_key"):
        raise AppError(
            message="LLM 未配置，请在 config.yaml 中设置 API Key",
            error_code=ErrorCode.LLM_API_ERROR,
        )

    api_url = provider["api_url"]
    api_key = provider["api_key"]
    model = provider["model"]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": KEYWORD_SYSTEM_PROMPT},
            {"role": "user", "content": f"视频主题: {body.topic}"},
        ],
        "temperature": 0.7,
        "max_tokens": 256,
        "stream": False,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        raise AppError(
            message="LLM API 调用超时，请稍后重试",
            error_code=ErrorCode.LLM_API_TIMEOUT,
        )
    except Exception as e:
        logger.error("LLM keyword generation failed: %s", str(e))
        raise AppError(
            message=f"LLM API 调用失败: {str(e)}",
            error_code=ErrorCode.LLM_API_ERROR,
        )

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Parse the JSON array from LLM response
    keywords = _parse_keywords(content)
    return KeywordGenerateResponse(keywords=keywords)


# ------------------------------------------------------------------
# Voice list and preview
# ------------------------------------------------------------------

VOICE_LIST = [
    {"id": "longyan_v2", "name": "妍妍", "gender": "女"},
    {"id": "longyingtian", "name": "甜甜", "gender": "女"},
    {"id": "longxiaoxia_v2", "name": "夏夏", "gender": "女"},
    {"id": "longxiaochun_v2", "name": "小春", "gender": "女"},
    {"id": "longmiao_v2", "name": "喵喵", "gender": "女"},
    {"id": "longhua_v2", "name": "华华", "gender": "女"},
    {"id": "longxiaobai_v2", "name": "小白", "gender": "女"},
    {"id": "longshu_v2", "name": "舒舒", "gender": "男"},
]


@router.get("/voices")
def get_voice_list(
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get available AI TTS voice list with preview URLs."""
    voices = []
    for v in VOICE_LIST:
        voices.append({
            **v,
            "preview_url": f"/static/voice_previews/{v['id']}.mp3",
        })
    return {"voices": voices}


def _parse_keywords(content: str) -> list[str]:
    """Parse keywords from LLM response content.

    Tries JSON parsing first, then falls back to line-by-line extraction.
    """
    content = content.strip()

    # Try direct JSON parse
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [str(k).strip() for k in parsed if str(k).strip()][:5]
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting JSON array from within the text
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(content[start:end + 1])
            if isinstance(parsed, list):
                return [str(k).strip() for k in parsed if str(k).strip()][:5]
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: split by newlines or commas
    keywords = []
    for line in content.replace(",", "\n").split("\n"):
        cleaned = line.strip().strip('"').strip("'").strip("-").strip("•").strip()
        if cleaned and len(cleaned) < 100:
            keywords.append(cleaned)
        if len(keywords) >= 5:
            break

    return keywords
