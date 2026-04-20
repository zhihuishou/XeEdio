"""Asset service: file upload, validation, storage, and thumbnail generation."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Asset, generate_uuid, utcnow
from app.services.config_service import ConfigService
from app.utils.errors import ErrorCode, ValidationError

# File format whitelist
ALLOWED_FORMATS: dict[str, list[str]] = {
    "video": ["mp4", "mov", "avi"],
    "image": ["jpg", "jpeg", "png", "webp"],
    "audio": ["mp3", "wav", "aac"],
}

# Valid categories
VALID_CATEGORIES = {"talent_speaking", "product", "pexels_broll"}

# Default max upload size (500MB)
DEFAULT_MAX_SIZE = 500 * 1024 * 1024

# Storage base path
STORAGE_BASE = "storage/assets"


def get_media_type(file_format: str) -> Optional[str]:
    """Determine media type from file extension."""
    ext = file_format.lower()
    for media_type, formats in ALLOWED_FORMATS.items():
        if ext in formats:
            return media_type
    return None


def validate_file_format(filename: str) -> tuple[str, str]:
    """Validate file format against whitelist.

    Returns:
        Tuple of (file_extension, media_type).

    Raises:
        ValidationError: If format is not supported.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media_type = get_media_type(ext)
    if media_type is None:
        all_formats = []
        for formats in ALLOWED_FORMATS.values():
            all_formats.extend(formats)
        raise ValidationError(
            message=f"Unsupported file format '.{ext}'. Supported formats: {', '.join(all_formats)}",
            error_code=ErrorCode.UNSUPPORTED_FORMAT,
        )
    return ext, media_type


def validate_file_size(file_size: int, db: Session) -> None:
    """Validate file size against configured maximum.

    Raises:
        ValidationError: If file exceeds max size.
    """
    config_service = ConfigService.get_instance()
    max_size_str = config_service.get_config("upload_max_size", db, str(DEFAULT_MAX_SIZE))
    max_size = int(max_size_str)
    if file_size > max_size:
        max_mb = max_size / (1024 * 1024)
        file_mb = file_size / (1024 * 1024)
        raise ValidationError(
            message=f"File too large ({file_mb:.1f}MB). Maximum allowed: {max_mb:.1f}MB",
            error_code=ErrorCode.FILE_TOO_LARGE,
        )


def generate_thumbnail(source_path: str, asset_dir: str, media_type: str) -> Optional[str]:
    """Generate thumbnail for the asset.

    - Video: extract first frame using ffmpeg
    - Image: resize using PIL

    Returns:
        Thumbnail path relative to storage, or None if generation fails.
    """
    thumbnail_path = os.path.join(asset_dir, "thumbnail.jpg")

    try:
        if media_type == "video":
            # Use ffmpeg to extract first frame
            result = subprocess.run(
                [
                    "ffmpeg", "-i", source_path,
                    "-vframes", "1",
                    "-vf", "scale=320:-1",
                    "-y", thumbnail_path,
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and os.path.exists(thumbnail_path):
                return thumbnail_path
            return None

        elif media_type == "image":
            # Use PIL to resize
            try:
                from PIL import Image
                img = Image.open(source_path)
                img.thumbnail((320, 320))
                img = img.convert("RGB")
                img.save(thumbnail_path, "JPEG", quality=80)
                return thumbnail_path
            except ImportError:
                return None

    except (subprocess.TimeoutExpired, OSError, Exception):
        pass

    return None
