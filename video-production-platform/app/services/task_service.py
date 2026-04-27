"""Task service with state machine and lifecycle management."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.database import Task, generate_uuid, utcnow
from app.utils.errors import StateTransitionError

# Valid state transitions: maps current state -> list of allowed next states
VALID_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["copy_confirmed", "processing"],
    "copy_confirmed": ["tts_done", "processing"],
    "tts_done": ["video_done"],
    "processing": ["video_done", "failed"],
    "video_done": ["pending_review"],
    "pending_review": ["approved", "rejected"],
    "approved": ["published"],
    "rejected": ["draft", "processing"],
    "failed": ["processing"],
}


def transition_state(task: Task, new_status: str) -> None:
    """Validate and execute a state transition on a task.

    Args:
        task: The task to transition.
        new_status: The target status.

    Raises:
        StateTransitionError: If the transition is not allowed.
    """
    current_status = task.status
    allowed = VALID_TRANSITIONS.get(current_status, [])

    if new_status not in allowed:
        raise StateTransitionError(
            message=f"Cannot transition from '{current_status}' to '{new_status}'. "
                    f"Allowed transitions: {allowed}",
            details={
                "current_status": current_status,
                "requested_status": new_status,
                "allowed_transitions": allowed,
            },
        )

    task.status = new_status
    task.updated_at = utcnow()


def create_task(topic: str, user_id: str, db: Session, batch_id: str | None = None) -> Task:
    """Create a new task in draft status.

    Args:
        topic: The video topic.
        user_id: The creator's user ID.
        db: Database session.
        batch_id: Optional batch task ID.

    Returns:
        The created Task instance.
    """
    task = Task(
        id=generate_uuid(),
        topic=topic,
        status="draft",
        created_by=user_id,
        batch_id=batch_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
