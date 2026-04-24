"""External configuration service.

Loads environment-specific config from YAML files.
Supports three environments via APP_ENV:
  - dev   → config.dev.yaml   (local development)
  - test  → config.test.yaml  (QA / staging)
  - aix   → config.aix.yaml   (production)

Falls back to config.yaml if no APP_ENV is set or the env-specific file
doesn't exist.

Values like "${VAR_NAME}" are expanded from environment variables at load
time, so production secrets never need to be committed to the repo.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("app.external_config")

# Regex matching ${VAR_NAME} placeholders
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders with environment variable values."""
    if isinstance(value, str):
        def _replacer(match: re.Match) -> str:
            var_name = match.group(1)
            env_val = os.environ.get(var_name, "")
            if not env_val:
                logger.debug("env var %s not set, using empty string", var_name)
            return env_val
        return _ENV_VAR_RE.sub(_replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def _resolve_config_path() -> str:
    """Determine which config file to load based on APP_ENV.

    Priority:
    1. APP_ENV env var → config.{env}.yaml
    2. Fallback → config.yaml
    """
    project_root = Path(__file__).parent.parent.parent
    app_env = os.environ.get("APP_ENV", "").strip().lower()

    if app_env:
        env_config = project_root / f"config.{app_env}.yaml"
        if env_config.exists():
            logger.info("loading config for env=%s from %s", app_env, env_config)
            return str(env_config)
        else:
            logger.warning(
                "APP_ENV=%s but config.%s.yaml not found, falling back to config.yaml",
                app_env, app_env,
            )

    default_config = project_root / "config.yaml"
    if default_config.exists():
        logger.info("loading default config from %s", default_config)
        return str(default_config)

    logger.warning("no config file found, using empty config")
    return ""


class ExternalConfig:
    """Configuration loaded from external YAML file.

    Supports environment-based config selection and ${VAR} expansion.
    """

    _instance: Optional["ExternalConfig"] = None
    _config: dict = {}
    _env: str = ""
    _config_path: str = ""

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self._config_path = config_path
            self._load(config_path)
        else:
            resolved = _resolve_config_path()
            if resolved:
                self._config_path = resolved
                self._load(resolved)
        self._env = self._config.get("app", {}).get("env", "dev")

    @classmethod
    def get_instance(cls, config_path: Optional[str] = None) -> "ExternalConfig":
        """Get singleton instance. Always reload from file to pick up changes."""
        cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reload(cls, config_path: Optional[str] = None) -> "ExternalConfig":
        """Reload configuration from file."""
        cls._instance = None
        return cls.get_instance(config_path)

    @property
    def env(self) -> str:
        """Current environment name (dev / test / aix)."""
        return self._env

    @property
    def config_path(self) -> str:
        """Path to the loaded config file."""
        return self._config_path

    def _load(self, config_path: str) -> None:
        """Load configuration from YAML file and expand env vars."""
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        self._config = _resolve_env_vars(raw)

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-notation key.

        Examples:
            get("llm.providers.deepseek.api_url")
            get("tts.voices")
        """
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_llm_provider(self, provider_id: str) -> Optional[dict]:
        """Get LLM provider configuration."""
        provider = self.get(f"llm.providers.{provider_id}")
        if provider:
            return {
                "id": provider_id,
                "name": provider.get("name", provider_id),
                "api_url": provider.get("api_url", ""),
                "api_key": provider.get("api_key", ""),
                "model": provider.get("model", ""),
                "key_hint": provider.get("key_hint", ""),
            }
        return None

    def get_all_llm_providers(self) -> list[dict]:
        """Get all configured LLM providers."""
        providers = self.get("llm.providers", {})
        result = []
        for provider_id, config in providers.items():
            result.append({
                "id": provider_id,
                "name": config.get("name", provider_id),
                "api_url": config.get("api_url", ""),
                "api_key": config.get("api_key", ""),
                "model": config.get("model", ""),
                "key_hint": config.get("key_hint", ""),
            })
        return result

    def get_default_provider(self) -> str:
        """Get default LLM provider ID."""
        return self.get("llm.default_provider", "deepseek")

    def is_gateway_enabled(self) -> bool:
        """Check if API gateway is enabled (future feature)."""
        return self.get("llm.gateway_enabled", False)

    def get_gateway_url(self) -> Optional[str]:
        """Get API gateway URL for credit billing (future feature)."""
        return self.get("llm.gateway_url")

    # --- Convenience methods ---

    def get_tts_config(self) -> dict:
        """Get TTS configuration."""
        return {
            "voices": self.get("tts.voices", "zh-CN-XiaoxiaoNeural,zh-CN-YunxiNeural"),
            "speed": self.get("tts.speed", "+0%"),
            "volume": self.get("tts.volume", "+0%"),
        }

    def get_video_config(self) -> dict:
        """Get video output configuration."""
        return {
            "resolution": self.get("video.resolution", "1080x1920"),
            "bitrate": self.get("video.bitrate", "8M"),
            "format": self.get("video.format", "mp4"),
        }

    def get_upload_config(self) -> dict:
        """Get upload configuration."""
        max_size_mb = self.get("upload.max_size_mb", 500)
        return {
            "max_size": max_size_mb * 1024 * 1024,
            "max_size_mb": max_size_mb,
            "allowed_formats": self.get("upload.allowed_formats", "mp4,mov,avi,jpg,png,webp,mp3,wav,aac"),
        }

    def get_batch_config(self) -> dict:
        """Get batch task configuration."""
        return {
            "max_concurrency": self.get("batch.max_concurrency", 3),
        }

    def get_vlm_config(self) -> dict:
        """Get VLM (Vision Language Model) configuration for AI Director."""
        return {
            "api_url": self.get("vlm.api_url", ""),
            "api_key": self.get("vlm.api_key", ""),
            "model": self.get("vlm.model", "gpt-5.4"),
            "frame_interval": self.get("vlm.frame_interval", 2),
            "max_frames": self.get("vlm.max_frames", 30),
        }

    def get_ai_tts_config(self) -> dict:
        """Get AI TTS configuration with fallback settings."""
        return {
            "provider": self.get("ai_tts.provider", ""),
            "api_key": self.get("ai_tts.api_key", ""),
            "api_url": self.get("ai_tts.api_url", ""),
            "model": self.get("ai_tts.model", "cosyvoice-v2"),
            "voice": self.get("ai_tts.voice", "longxiaochun_v2"),
            "fallback_to_edge_tts": self.get("ai_tts.fallback_to_edge_tts", True),
        }

    def get_pexels_config(self) -> dict:
        """Get Pexels API configuration."""
        return {
            "api_key": self.get("pexels.api_key", ""),
        }
