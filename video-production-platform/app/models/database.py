"""SQLAlchemy database models and session management for Video Production Platform."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


DATABASE_URL = "sqlite:///./video_platform.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="intern")  # intern|operator|admin
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    tasks = relationship("Task", back_populates="creator", foreign_keys="Task.created_by")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True, default=generate_uuid)
    filename = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    thumbnail_path = Column(String, nullable=True)
    category = Column(String, nullable=False)  # talent_speaking|product|pexels_broll
    media_type = Column(String, nullable=False)  # video|image|audio
    file_format = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)
    duration = Column(Float, nullable=True)
    uploaded_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    uploader = relationship("User", foreign_keys=[uploaded_by])
    task_assets = relationship("TaskAsset", back_populates="asset")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=generate_uuid)
    topic = Column(String, nullable=False)
    status = Column(String, nullable=False, default="draft")
    # draft|copy_confirmed|tts_done|video_done|pending_review|approved|rejected|published
    copywriting_raw = Column(Text, nullable=True)
    copywriting_filtered = Column(Text, nullable=True)
    copywriting_final = Column(Text, nullable=True)
    tts_voice = Column(String, nullable=True)
    tts_audio_path = Column(String, nullable=True)
    tts_duration = Column(Float, nullable=True)
    video_path = Column(String, nullable=True)
    video_resolution = Column(String, nullable=True)
    video_duration = Column(Float, nullable=True)
    video_file_size = Column(Integer, nullable=True)
    mix_params = Column(Text, nullable=True)  # JSON storage for mixing parameters
    video_paths = Column(Text, nullable=True)  # JSON array of output video paths
    error_message = Column(Text, nullable=True)  # Failure reason
    review_comment = Column(String, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(String, ForeignKey("users.id"), nullable=True)
    batch_id = Column(String, ForeignKey("batch_tasks.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    creator = relationship("User", back_populates="tasks", foreign_keys=[created_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    batch = relationship("BatchTask", back_populates="tasks")
    task_assets = relationship("TaskAsset", back_populates="task")
    review_logs = relationship("ReviewLog", back_populates="task")


class TaskAsset(Base):
    __tablename__ = "task_assets"

    id = Column(String, primary_key=True, default=generate_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    asset_id = Column(String, ForeignKey("assets.id"), nullable=False)
    roll_type = Column(String, nullable=False)  # clip|a_roll|b_roll (legacy: "asset")
    sequence_order = Column(Integer, nullable=False, default=0)

    task = relationship("Task", back_populates="task_assets")
    asset = relationship("Asset", back_populates="task_assets")


class BatchTask(Base):
    __tablename__ = "batch_tasks"

    id = Column(String, primary_key=True, default=generate_uuid)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    total_tasks = Column(Integer, nullable=False, default=0)
    completed_tasks = Column(Integer, nullable=False, default=0)
    failed_tasks = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="running")  # running|completed|partial_failed
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    creator = relationship("User", foreign_keys=[created_by])
    tasks = relationship("Task", back_populates="batch")


class ForbiddenWord(Base):
    __tablename__ = "forbidden_words"

    id = Column(String, primary_key=True, default=generate_uuid)
    word = Column(String, nullable=False)
    category = Column(String, nullable=True)
    suggestion = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    reviewer_id = Column(String, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)  # approve|reject
    reason = Column(Text, nullable=True)
    topic = Column(Text, nullable=True)
    copywriting_snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    task = relationship("Task", back_populates="review_logs")
    reviewer = relationship("User", foreign_keys=[reviewer_id])


class SystemConfig(Base):
    __tablename__ = "system_config"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class AssetAnalysis(Base):
    """VLM analysis results for an asset — populated asynchronously after upload."""
    __tablename__ = "asset_analysis"

    id = Column(String, primary_key=True, default=generate_uuid)
    asset_id = Column(String, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # VLM structured output
    description = Column(Text, nullable=True)           # Content description
    role = Column(String, nullable=True, index=True)    # presenter / product_closeup / lifestyle / transition / other
    visual_quality = Column(String, nullable=True)       # high / medium / low
    scene_tags = Column(Text, nullable=True)             # JSON array: ["室内", "美妆", "产品展示"]
    key_moments = Column(Text, nullable=True)            # JSON array: [{"time": 5.0, "desc": "..."}]

    # Audio metadata
    audio_quality = Column(String, nullable=True)        # good / noisy / silent
    has_speech = Column(Boolean, default=False)
    speech_ranges = Column(Text, nullable=True)          # JSON array: [[0, 230], [1650, 1664]]
    transcript = Column(Text, nullable=True)             # Whisper transcription

    # Embedding vector
    embedding = Column(LargeBinary, nullable=True)       # float32 array as bytes

    # Status tracking
    status = Column(String, nullable=False, default="pending")  # pending / analyzing / completed / failed
    error_message = Column(Text, nullable=True)
    vlm_model = Column(String, nullable=True)            # Model version used for analysis
    analyzed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    asset = relationship("Asset", backref="analysis")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    asset_ids = Column(Text, nullable=True)  # JSON array of related asset IDs
    agent_state = Column(Text, nullable=True)  # JSON object with structured context memory
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", foreign_keys=[user_id])
    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    conversation_id = Column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String, nullable=False)  # user | assistant | tool
    content = Column(Text, nullable=False, default="")
    tool_name = Column(String, nullable=True)
    tool_call_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class MixConversationSession(Base):
    __tablename__ = "mix_conversation_sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, default="未命名会话")
    last_task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", foreign_keys=[user_id])
    messages = relationship(
        "MixConversationMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MixConversationMessage.sequence",
    )


class MixConversationMessage(Base):
    __tablename__ = "mix_conversation_messages"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, ForeignKey("mix_conversation_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False, default=0)
    sender = Column(String, nullable=False)  # user|system
    message_type = Column(String, nullable=False)  # text|progress|asset_upload|video_result|error
    content = Column(Text, nullable=True)
    extra_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    session = relationship("MixConversationSession", back_populates="messages")


# --- Database session dependency ---

def get_db():
    """FastAPI dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
