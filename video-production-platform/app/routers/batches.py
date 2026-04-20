"""Batch task management API endpoints."""

import csv
import io

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.models.database import BatchTask, Task, User, get_db
from app.schemas.batch import (
    BatchCreate,
    BatchDetailResponse,
    BatchListResponse,
    BatchTaskResponse,
)
from app.services.batch_service import create_batch
from app.utils.auth import get_current_user, require_role
from app.utils.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.post("", response_model=BatchTaskResponse)
def create_batch_endpoint(
    body: BatchCreate,
    current_user: User = Depends(require_role("intern", "admin")),
    db: Session = Depends(get_db),
):
    """Create a batch task with multiple topics and versions."""
    if not body.topics:
        raise ValidationError(message="Topics list cannot be empty")

    batch = create_batch(
        topics=body.topics,
        versions_per_topic=body.versions_per_topic,
        user_id=current_user.id,
        db=db,
    )
    return batch


@router.post("/upload-csv", response_model=BatchTaskResponse)
async def upload_csv_topics(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("intern", "admin")),
    db: Session = Depends(get_db),
):
    """Upload a CSV file with topics to create a batch task.

    CSV format: one topic per line (single column).
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise ValidationError(message="File must be a CSV file")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))

    topics = []
    for row in reader:
        if row and row[0].strip():
            topics.append(row[0].strip())

    if not topics:
        raise ValidationError(message="CSV file contains no valid topics")

    batch = create_batch(
        topics=topics,
        versions_per_topic=1,
        user_id=current_user.id,
        db=db,
    )
    return batch


@router.get("", response_model=BatchListResponse)
def list_batches(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all batch tasks."""
    query = db.query(BatchTask)

    # Intern sees only own batches
    if current_user.role == "intern":
        query = query.filter(BatchTask.created_by == current_user.id)

    query = query.order_by(BatchTask.created_at.desc())
    batches = query.all()

    return BatchListResponse(batches=batches, total=len(batches))


@router.get("/{batch_id}", response_model=BatchDetailResponse)
def get_batch_detail(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get batch task detail with sub-task status summary and progress."""
    batch = db.query(BatchTask).filter(BatchTask.id == batch_id).first()
    if not batch:
        raise NotFoundError(message=f"Batch task '{batch_id}' not found")

    # Intern can only view own batches
    if current_user.role == "intern" and batch.created_by != current_user.id:
        raise NotFoundError(message=f"Batch task '{batch_id}' not found")

    # Get sub-tasks
    sub_tasks = db.query(Task).filter(Task.batch_id == batch_id).all()

    # Calculate progress
    progress_percent = 0.0
    if batch.total_tasks > 0:
        progress_percent = (batch.completed_tasks / batch.total_tasks) * 100

    # Build sub-task summary
    tasks_summary = [
        {
            "id": t.id,
            "topic": t.topic,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in sub_tasks
    ]

    return BatchDetailResponse(
        id=batch.id,
        created_by=batch.created_by,
        total_tasks=batch.total_tasks,
        completed_tasks=batch.completed_tasks,
        failed_tasks=batch.failed_tasks,
        status=batch.status,
        progress_percent=progress_percent,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        tasks=tasks_summary,
    )
