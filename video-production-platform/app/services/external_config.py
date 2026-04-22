"""External configuration service.

Loads config from config.yaml file.
Designed for future migration to Go-based API gateway with credit billing.
"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml


class ExternalConfig:
    """Configuration loaded from external YAML file.
    
    MVP: Direct API key configuration
    Future: Gateway URL for Go-based credit billing service
    """

    _instance: Optional["ExternalConfig"] = None
    _config: dict = {}

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self._load(config_path)
        else:
            # Default path: config.yaml in project root
            default_path = Path(__file__).parent.parent.parent / "config.yaml"
            if default_path.exists():
                self._load(str(default_path))

    @classmethod
    def get_instance(cls, config_path: Optional[str] = None) -> "ExternalConfig":
        """Get singleton instance. Always reload from file to pick up changes."""
        # Always reload to support config.yaml hot-reload
        cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reload(cls, config_path: Optional[str] = None) -> "ExternalConfig":
        """Reload configuration from file."""
        cls._instance = None
        return cls.get_instance(config_path)

    def _load(self, config_path: str) -> None:
        """Load configuration from YAML file."""
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f) or {}

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
        """Get LLM provider configuration.
        
        Returns dict with: name, api_url, api_key, model, key_hint
        """
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

    # --- Convenience methods for other configs ---

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
            "max_size": max_size_mb * 1024 * 1024,  # Convert to bytes
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
