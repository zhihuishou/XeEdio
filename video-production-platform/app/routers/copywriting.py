"""Copywriting generation, editing, and confirmation API endpoints."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import Task, User, get_db
from app.schemas.copywriting import (
    CopywritingConfirmResponse,
    CopywritingEditRequest,
    CopywritingGenerateRequest,
    CopywritingResponse,
    ForbiddenWordMatchItem,
)
from app.services.copywriting_service import CopywritingService
from app.services.external_config import ExternalConfig
from app.utils.auth import require_role
from app.utils.errors import AppError, ErrorCode, NotFoundError

logger = logging.getLogger("app.llm")

router = APIRouter(prefix="/api/copywriting", tags=["copywriting"])


def _build_llm_config(body: CopywritingGenerateRequest) -> dict | None:
    """Resolve LLM config from provider_id + optional api_key override.

    Priority:
      1. provider_id -> look up api_url/model from config.yaml
      2. api_key from request body overrides config.yaml api_key
      3. If no provider_id, fall back to config.yaml default_provider
      4. If still nothing, return None (service will use legacy DB config)
    """
    ext = ExternalConfig.get_instance()
    provider_id = body.provider_id or ext.get_default_provider()

    provider = ext.get_llm_provider(provider_id)
    if not provider:
        return None

    # Request-level api_key overrides config.yaml api_key
    api_key = body.api_key or provider["api_key"]
    if not api_key:
        return None

    return {
        "api_url": provider["api_url"],
        "api_key": api_key,
        "model": provider["model"],
    }


@router.post("/generate", response_model=CopywritingResponse)
def generate_copywriting(
    body: CopywritingGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Generate copywriting for a topic using LLM.

    Automatically runs forbidden word filtering on the generated text.
    Creates a new Task if task_id is not provided.
    """
    service = CopywritingService(db)
    llm_config = _build_llm_config(body)

    try:
        task = service.generate_copywriting(
            topic=body.topic,
            task_id=body.task_id,
            user_id=current_user.id,
            llm_config=llm_config,
        )
    except ValueError as e:
        raise NotFoundError(message=str(e))
    except Exception as e:
        error_msg = str(e)
        logger.error("Copywriting generation failed: %s", error_msg)
        if "timeout" in error_msg.lower():
            raise AppError(
                message="LLM API 调用超时，请稍后重试",
                error_code=ErrorCode.LLM_API_TIMEOUT,
            )
        raise AppError(
            message=f"LLM API 调用失败: {error_msg}",
            error_code=ErrorCode.LLM_API_ERROR,
        )

    # Run check to get matches for response
    check_result = service.check_and_filter_text(task.copywriting_raw or "")

    return CopywritingResponse(
        task_id=task.id,
        topic=task.topic,
        status=task.status,
        copywriting_raw=task.copywriting_raw,
        copywriting_filtered=task.copywriting_filtered,
        copywriting_final=task.copywriting_final,
        filter_status=check_result["status"],
        matches=[
            ForbiddenWordMatchItem(
                word=m["word"],
                position=m["position"],
                category=m.get("category"),
                suggestion=m.get("suggestion"),
            )
            for m in check_result["matches"]
        ],
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.get("/{task_id}", response_model=CopywritingResponse)
def get_copywriting(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get copywriting details for a task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError(message="Task not found")

    service = CopywritingService(db)
    text_to_check = task.copywriting_final or task.copywriting_filtered or task.copywriting_raw or ""
    check_result = service.check_and_filter_text(text_to_check) if text_to_check else {"status": "passed", "matches": []}

    return CopywritingResponse(
        task_id=task.id,
        topic=task.topic,
        status=task.status,
        copywriting_raw=task.copywriting_raw,
        copywriting_filtered=task.copywriting_filtered,
        copywriting_final=task.copywriting_final,
        filter_status=check_result["status"],
        matches=[
            ForbiddenWordMatchItem(
                word=m["word"],
                position=m["position"],
                category=m.get("category"),
                suggestion=m.get("suggestion"),
            )
            for m in check_result["matches"]
        ],
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.put("/{task_id}", response_model=CopywritingResponse)
def edit_copywriting(
    task_id: str,
    body: CopywritingEditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Edit copywriting for a task.

    Automatically re-runs forbidden word detection on the edited text.
    Returns 409 COPY_LOCKED if the copywriting has been confirmed.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError(message="Task not found")

    if task.status == "copy_confirmed":
        raise AppError(
            message="文案已确认锁定，无法编辑",
            error_code=ErrorCode.COPY_LOCKED,
        )

    task.copywriting_final = body.copywriting_final

    service = CopywritingService(db)
    check_result = service.check_and_filter_text(body.copywriting_final)

    task.copywriting_filtered = check_result["filtered_text"]
    from app.models.database import utcnow
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)

    return CopywritingResponse(
        task_id=task.id,
        topic=task.topic,
        status=task.status,
        copywriting_raw=task.copywriting_raw,
        copywriting_filtered=task.copywriting_filtered,
        copywriting_final=task.copywriting_final,
        filter_status=check_result["status"],
        matches=[
            ForbiddenWordMatchItem(
                word=m["word"],
                position=m["position"],
                category=m.get("category"),
                suggestion=m.get("suggestion"),
            )
            for m in check_result["matches"]
        ],
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.post("/{task_id}/confirm", response_model=CopywritingConfirmResponse)
def confirm_copywriting(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Confirm and lock copywriting for a task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError(message="Task not found")

    if task.status == "copy_confirmed":
        raise AppError(
            message="文案已确认锁定",
            error_code=ErrorCode.COPY_LOCKED,
        )

    final_text = task.copywriting_final or task.copywriting_filtered or task.copywriting_raw or ""
    task.copywriting_final = final_text
    task.status = "copy_confirmed"
    from app.models.database import utcnow
    task.updated_at = utcnow()
    db.commit()
    db.refresh(task)

    return CopywritingConfirmResponse(
        task_id=task.id,
        status=task.status,
        copywriting_final=task.copywriting_final,
    )
