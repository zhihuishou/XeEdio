"""Pexels video search and download service.

Provides search and download capabilities for Pexels free stock videos,
referenced from MoneyPrinterTurbo's material.py search_videos_pexels function.
"""

import logging
import os
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models.database import Asset, generate_uuid, utcnow
from app.services.config_service import ConfigService
from app.services.external_config import ExternalConfig
from app.utils.errors import AppError, ErrorCode, ValidationError

logger = logging.getLogger("app.pexels_service")

# Aspect ratio to Pexels orientation mapping
ASPECT_RATIO_TO_ORIENTATION = {
    "16:9": "landscape",
    "9:16": "portrait",
    "1:1": "square",
}

# Aspect ratio to target resolution mapping
ASPECT_RATIO_TO_RESOLUTION = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


class PexelsService:
    """Search and download videos from Pexels API."""

    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService.get_instance()
        self.ext_config = ExternalConfig.get_instance()

    # ------------------------------------------------------------------
    # 5.1  search_videos
    # ------------------------------------------------------------------

    def search_videos(
        self,
        keywords: list[str],
        aspect_ratio: str = "9:16",
        per_page: int = 10,
    ) -> dict:
        """Search Pexels for videos matching keywords and aspect ratio.

        Args:
            keywords: List of search keywords.
            aspect_ratio: Target aspect ratio ("16:9", "9:16", "1:1").
            per_page: Number of results per keyword query.

        Returns:
            Dict with 'videos' (list of video items) and 'total' count.

        Raises:
            ValidationError: If Pexels API key is not configured.
            AppError: If Pexels API call fails.
        """
        api_key = self.ext_config.get_pexels_config().get("api_key", "")
        if not api_key:
            # Fallback: try DB config for backward compatibility
            api_key = self.config.get_config("pexels_api_key", self.db, "")
        if not api_key:
            raise ValidationError(
                message="Pexels API Key 未配置，请在 config.yaml 中设置",
                details={"config_key": "pexels.api_key"},
            )

        orientation = ASPECT_RATIO_TO_ORIENTATION.get(aspect_ratio, "portrait")
        target_width, target_height = ASPECT_RATIO_TO_RESOLUTION.get(
            aspect_ratio, (1080, 1920)
        )

        headers = {
            "Authorization": api_key,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/115.0.0.0 Safari/537.36"
            ),
        }

        all_videos: list[dict] = []

        for keyword in keywords:
            params = {
                "query": keyword,
                "per_page": per_page,
                "orientation": orientation,
            }
            url = f"https://api.pexels.com/videos/search?{urlencode(params)}"
            logger.info("searching Pexels videos: %s", url)

            try:
                with httpx.Client(timeout=60.0) as client:
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                    data = response.json()
            except httpx.TimeoutException:
                logger.error("Pexels API timeout for keyword: %s", keyword)
                raise AppError(
                    message=f"Pexels API 调用超时: {keyword}",
                    error_code=ErrorCode.MIXING_ERROR,
                )
            except httpx.HTTPStatusError as e:
                logger.error("Pexels API HTTP error: %s", str(e))
                raise AppError(
                    message=f"Pexels API 调用失败: {e.response.status_code}",
                    error_code=ErrorCode.MIXING_ERROR,
                )
            except Exception as e:
                logger.error("Pexels API error: %s", str(e))
                raise AppError(
                    message=f"Pexels API 调用失败: {str(e)}",
                    error_code=ErrorCode.MIXING_ERROR,
                )

            if "videos" not in data:
                logger.warning("No 'videos' key in Pexels response for: %s", keyword)
                continue

            for video in data["videos"]:
                duration = video.get("duration", 0)
                thumbnail_url = ""
                # Extract thumbnail from video pictures
                pictures = video.get("video_pictures", [])
                if pictures:
                    thumbnail_url = pictures[0].get("picture", "")

                # Find the video file matching target resolution
                video_files = video.get("video_files", [])
                for vf in video_files:
                    w = int(vf.get("width", 0))
                    h = int(vf.get("height", 0))
                    if w == target_width and h == target_height:
                        all_videos.append({
                            "url": vf.get("link", ""),
                            "thumbnail_url": thumbnail_url,
                            "duration": duration,
                            "width": w,
                            "height": h,
                        })
                        break

        logger.info("found %d matching Pexels videos", len(all_videos))
        return {
            "videos": all_videos,
            "total": len(all_videos),
        }

    # ------------------------------------------------------------------
    # 5.2  download_video
    # ------------------------------------------------------------------

    def download_video(self, video_url: str, user_id: str) -> Asset:
        """Download a Pexels video and create an Asset record.

        Args:
            video_url: URL of the video to download.
            user_id: ID of the user initiating the download.

        Returns:
            The newly created Asset record.

        Raises:
            AppError: If download fails.
        """
        asset_id = generate_uuid()
        asset_dir = os.path.join("storage", "assets", asset_id)
        os.makedirs(asset_dir, exist_ok=True)
        file_path = os.path.join(asset_dir, "original.mp4")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/115.0.0.0 Safari/537.36"
            ),
        }

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.get(video_url, headers=headers, follow_redirects=True)
                response.raise_for_status()
                with open(file_path, "wb") as f:
                    f.write(response.content)
        except httpx.TimeoutException:
            logger.error("Pexels video download timeout: %s", video_url)
            raise AppError(
                message="Pexels 视频下载超时",
                error_code=ErrorCode.MIXING_ERROR,
            )
        except Exception as e:
            logger.error("Pexels video download failed: %s", str(e))
            raise AppError(
                message=f"Pexels 视频下载失败: {str(e)}",
                error_code=ErrorCode.MIXING_ERROR,
            )

        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        if file_size == 0:
            raise AppError(
                message="下载的视频文件为空",
                error_code=ErrorCode.MIXING_ERROR,
            )

        # Probe duration using ffprobe if available
        duration = self._probe_duration(file_path)

        # Create Asset record
        asset = Asset(
            id=asset_id,
            filename=f"{asset_id}.mp4",
            original_filename="pexels_video.mp4",
            file_path=file_path,
            category="pexels_broll",
            media_type="video",
            file_format="mp4",
            file_size=file_size,
            duration=duration,
            uploaded_by=user_id,
            created_at=utcnow(),
        )
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)

        logger.info("Pexels video downloaded: asset_id=%s, path=%s", asset_id, file_path)
        return asset

    @staticmethod
    def _probe_duration(video_path: str) -> Optional[float]:
        """Get video duration using ffprobe."""
        import subprocess
        import json as _json

        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_format", video_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                data = _json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception:
            pass
        return None
