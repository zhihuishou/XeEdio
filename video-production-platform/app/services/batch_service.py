"""Batch task service with concurrency control."""

import asyncio
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import BatchTask, Task, generate_uuid, utcnow
from app.services.config_service import ConfigService

logger = logging.getLogger("app.batch")


def create_batch(
    topics: list[str],
    versions_per_topic: int,
    user_id: str,
    db: Session,
) -> BatchTask:
    """Create a batch task with N × V sub-tasks.

    Args:
        topics: List of video topics.
        versions_per_topic: Number of versions per topic.
        user_id: Creator's user ID.
        db: Database session.

    Returns:
        The created BatchTask with associated sub-tasks.
    """
    total = len(topics) * versions_per_topic

    batch = BatchTask(
        id=generate_uuid(),
        created_by=user_id,
        total_tasks=total,
        completed_tasks=0,
        failed_tasks=0,
        status="running",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(batch)
    db.flush()

    # Create N × V individual tasks
    for topic in topics:
        for _ in range(versions_per_topic):
            task = Task(
                id=generate_uuid(),
                topic=topic,
                status="draft",
                created_by=user_id,
                batch_id=batch.id,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(task)

    db.commit()
    db.refresh(batch)
    return batch


async def execute_batch(batch_id: str, db: Session) -> None:
    """Execute batch tasks with concurrency control.

    Uses asyncio.Semaphore to limit concurrent task execution.
    Single task failure does not affect others.

    Args:
        batch_id: The batch task ID.
        db: Database session.
    """
    config_service = ConfigService.get_instance()
    max_concurrency = int(config_service.get_config("batch_max_concurrency", db, default="3"))
    semaphore = asyncio.Semaphore(max_concurrency)

    batch = db.query(BatchTask).filter(BatchTask.id == batch_id).first()
    if not batch:
        logger.error("Batch task %s not found", batch_id)
        return

    tasks = db.query(Task).filter(Task.batch_id == batch_id).all()

    async def process_task(task: Task) -> None:
        """Process a single sub-task with semaphore control."""
        async with semaphore:
            try:
                # Placeholder for actual task processing pipeline
                # In production, this would call copywriting, TTS, composition services
                logger.info("Processing sub-task %s for batch %s", task.id, batch_id)
            except Exception as e:
                logger.error("Sub-task %s failed: %s", task.id, str(e))
                batch.failed_tasks += 1
                db.commit()

    # Execute all tasks concurrently with semaphore limiting
    await asyncio.gather(
        *[process_task(t) for t in tasks],
        return_exceptions=True,
    )

    # Update batch status
    batch.updated_at = utcnow()
    if batch.failed_tasks > 0 and batch.failed_tasks < batch.total_tasks:
        batch.status = "partial_failed"
    elif batch.failed_tasks == batch.total_tasks:
        batch.status = "partial_failed"
    else:
        batch.status = "completed"
    db.commit()
