"""Review management API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import ReviewLog, Task, User, generate_uuid, get_db, utcnow
from app.schemas.review import (
    PendingReviewsResponse,
    ReviewLogResponse,
    ReviewRejectRequest,
    ReviewTaskResponse,
)
from app.services.rag_service import RAGService
from app.services.task_service import transition_state
from app.utils.auth import require_role
from app.utils.errors import NotFoundError, ValidationError, ErrorCode

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.get("/pending", response_model=PendingReviewsResponse)
def get_pending_reviews(
    current_user: User = Depends(require_role("operator", "admin")),
    db: Session = Depends(get_db),
):
    """Get list of tasks pending review."""
    tasks = (
        db.query(Task)
        .filter(Task.status == "pending_review")
        .order_by(Task.updated_at.desc())
        .all()
    )
    return PendingReviewsResponse(tasks=tasks, total=len(tasks))


@router.post("/{task_id}/approve", response_model=ReviewLogResponse)
def approve_task(
    task_id: str,
    current_user: User = Depends(require_role("operator", "admin")),
    db: Session = Depends(get_db),
):
    """Approve a video task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError(message=f"Task '{task_id}' not found")

    # Transition state: pending_review -> approved
    transition_state(task, "approved")
    task.reviewed_by = current_user.id

    # Create review log
    review_log = ReviewLog(
        id=generate_uuid(),
        task_id=task_id,
        reviewer_id=current_user.id,
        action="approve",
        topic=task.topic,
        copywriting_snapshot=task.copywriting_final or task.copywriting_filtered or task.copywriting_raw,
        created_at=utcnow(),
    )
    db.add(review_log)
    db.commit()
    db.refresh(review_log)

    return review_log


@router.post("/{task_id}/reject", response_model=ReviewLogResponse)
def reject_task(
    task_id: str,
    body: ReviewRejectRequest,
    current_user: User = Depends(require_role("operator", "admin")),
    db: Session = Depends(get_db),
):
    """Reject a video task. Reason is required."""
    # Validate rejection reason is not empty
    if not body.reason or not body.reason.strip():
        raise ValidationError(
            message="Rejection reason is required",
            error_code=ErrorCode.REJECTION_REASON_REQUIRED,
        )

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError(message=f"Task '{task_id}' not found")

    # Transition state: pending_review -> rejected
    transition_state(task, "rejected")
    task.reviewed_by = current_user.id
    task.review_comment = body.reason.strip()

    # Get copywriting snapshot
    copywriting_snapshot = task.copywriting_final or task.copywriting_filtered or task.copywriting_raw or ""

    # Create review log
    review_log = ReviewLog(
        id=generate_uuid(),
        task_id=task_id,
        reviewer_id=current_user.id,
        action="reject",
        reason=body.reason.strip(),
        topic=task.topic,
        copywriting_snapshot=copywriting_snapshot,
        created_at=utcnow(),
    )
    db.add(review_log)

    # Task 14.2: Write rejection to RAG knowledge base
    rag_service = RAGService.get_instance()
    rag_service.add_rejection(
        topic=task.topic,
        reason=body.reason.strip(),
        copywriting=copywriting_snapshot,
    )

    db.commit()
    db.refresh(review_log)

    return review_log
