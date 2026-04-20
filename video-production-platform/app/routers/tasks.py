"""Task management API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.models.database import Task, User, get_db
from app.schemas.task import TaskCreate, TaskListResponse, TaskResponse
from app.services.task_service import create_task
from app.utils.auth import get_current_user, require_role
from app.utils.errors import NotFoundError

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse)
def create_task_endpoint(
    body: TaskCreate,
    current_user: User = Depends(require_role("intern", "admin")),
    db: Session = Depends(get_db),
):
    """Create a new task in draft status."""
    task = create_task(topic=body.topic, user_id=current_user.id, db=db)
    return task


@router.get("", response_model=TaskListResponse)
def list_tasks(
    status: Optional[str] = Query(None, description="Filter by task status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List tasks based on user role.

    - Intern: only own tasks
    - Operator: only pending_review tasks
    - Admin: all tasks
    """
    query = db.query(Task)

    if current_user.role == "intern":
        query = query.filter(Task.created_by == current_user.id)
    elif current_user.role == "operator":
        query = query.filter(Task.status == "pending_review")

    if status:
        query = query.filter(Task.status == status)

    query = query.order_by(Task.created_at.desc())
    tasks = query.all()

    return TaskListResponse(tasks=tasks, total=len(tasks))


@router.get("/{task_id}", response_model=TaskResponse)
def get_task_detail(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get task details including copywriting, assets, and video preview path."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError(message=f"Task '{task_id}' not found")

    # Intern can only view own tasks
    if current_user.role == "intern" and task.created_by != current_user.id:
        raise NotFoundError(message=f"Task '{task_id}' not found")

    # Operator can only view pending_review tasks
    if current_user.role == "operator" and task.status != "pending_review":
        raise NotFoundError(message=f"Task '{task_id}' not found")

    return task
