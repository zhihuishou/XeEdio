"""Database initialization script.

Creates all tables and seeds default data (admin user + system config).
"""

from passlib.context import CryptContext

from app.models.database import (
    Base,
    SessionLocal,
    SystemConfig,
    User,
    engine,
    generate_uuid,
    utcnow,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_CONFIGS = [
    {
        "key": "llm_api_url",
        "value": "https://api.deepseek.com/v1",
        "description": "LLM API 地址",
    },
    {
        "key": "llm_api_key",
        "value": "",
        "description": "LLM API Key",
    },
    {
        "key": "llm_model",
        "value": "deepseek-chat",
        "description": "LLM 模型名称",
    },
    {
        "key": "tts_voices",
        "value": "zh-CN-XiaoxiaoNeural,zh-CN-YunxiNeural",
        "description": "TTS 可用语音角色列表（逗号分隔）",
    },
    {
        "key": "tts_speed",
        "value": "+0%",
        "description": "TTS 语速调整",
    },
    {
        "key": "tts_volume",
        "value": "+0%",
        "description": "TTS 音量调整",
    },
    {
        "key": "video_resolution",
        "value": "1080x1920",
        "description": "视频输出分辨率（宽x高）",
    },
    {
        "key": "video_bitrate",
        "value": "8M",
        "description": "视频输出码率",
    },
    {
        "key": "video_format",
        "value": "mp4",
        "description": "视频输出格式",
    },
    {
        "key": "upload_max_size",
        "value": "524288000",
        "description": "素材上传最大文件大小（字节），默认 500MB",
    },
    {
        "key": "upload_allowed_formats",
        "value": "mp4,mov,avi,jpg,png,webp,mp3,wav,aac",
        "description": "素材上传允许的文件格式（逗号分隔）",
    },
    {
        "key": "batch_max_concurrency",
        "value": "3",
        "description": "批量任务最大并发数",
    },
]


def init_db():
    """Create all tables and seed default data."""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        _seed_admin_user(db)
        _seed_system_config(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_admin_user(db):
    """Create default admin user if not exists."""
    existing = db.query(User).filter(User.username == "admin").first()
    if existing is None:
        admin = User(
            id=generate_uuid(),
            username="admin",
            password_hash=pwd_context.hash("admin123"),
            role="admin",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(admin)


def _seed_system_config(db):
    """Insert default system config entries if not exists."""
    for cfg in DEFAULT_CONFIGS:
        existing = db.query(SystemConfig).filter(SystemConfig.key == cfg["key"]).first()
        if existing is None:
            config = SystemConfig(
                key=cfg["key"],
                value=cfg["value"],
                description=cfg["description"],
                updated_at=utcnow(),
            )
            db.add(config)


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
