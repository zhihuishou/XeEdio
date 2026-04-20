"""Configuration service with in-memory cache and write-through strategy."""

import threading
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import SystemConfig, utcnow


class ConfigService:
    """System configuration service.

    Provides get/set operations for SystemConfig with an in-memory cache.
    Write-through strategy: writes update both cache and database simultaneously.
    """

    _instance: Optional["ConfigService"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls) -> "ConfigService":
        """Get singleton instance of ConfigService."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def _load_all(self, db: Session) -> None:
        """Load all configs from database into cache."""
        configs = db.query(SystemConfig).all()
        self._cache = {c.key: c.value for c in configs if c.value is not None}
        self._loaded = True

    def _ensure_loaded(self, db: Session) -> None:
        """Ensure cache is populated."""
        if not self._loaded:
            self._load_all(db)

    def get_config(self, key: str, db: Session, default: Optional[str] = None) -> Optional[str]:
        """Get a configuration value by key.

        Args:
            key: Configuration key.
            db: Database session.
            default: Default value if key not found.

        Returns:
            Configuration value or default.
        """
        self._ensure_loaded(db)
        return self._cache.get(key, default)

    def set_config(self, key: str, value: str, db: Session, description: Optional[str] = None) -> None:
        """Set a configuration value (write-through: updates cache and DB).

        Args:
            key: Configuration key.
            value: Configuration value.
            db: Database session.
            description: Optional description for the config entry.
        """
        config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if config:
            config.value = value
            config.updated_at = utcnow()
            if description is not None:
                config.description = description
        else:
            config = SystemConfig(
                key=key,
                value=value,
                description=description,
                updated_at=utcnow(),
            )
            db.add(config)
        db.commit()
        # Update cache immediately (write-through)
        self._cache[key] = value

    def get_all_configs(self, db: Session) -> dict[str, dict]:
        """Get all configuration items.

        Returns:
            Dict mapping key to {value, description, updated_at}.
        """
        self._ensure_loaded(db)
        # Fetch full records for description and updated_at
        configs = db.query(SystemConfig).all()
        result = {}
        for c in configs:
            result[c.key] = {
                "key": c.key,
                "value": c.value,
                "description": c.description,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        return result

    def invalidate_cache(self) -> None:
        """Invalidate the in-memory cache (forces reload on next access)."""
        self._cache.clear()
        self._loaded = False
