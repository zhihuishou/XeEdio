"""Asset management router: upload, list, detail, delete endpoints."""

import os
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.models.database import Asset, User, generate_uuid, get_db, utcnow
from app.schemas.asset import (
    AssetAnalysisResponse,
    AssetDetailResponse,
    AssetListResponse,
    AssetResponse,
    AssetSearchResponse,
    AssetSearchItem,
    AssetUploadResponse,
    BatchUploadItemResult,
    BatchUploadResponse,
    ReanalyzeResponse,
)
from app.services.asset_service import (
    STORAGE_BASE,
    VALID_CATEGORIES,
    generate_thumbnail,
    validate_file_format,
)
from app.utils.auth import require_role
from app.utils.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.post("/upload", response_model=AssetUploadResponse, status_code=201)
async def upload_asset(
    file: UploadFile = File(...),
    category: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Upload an asset file (Admin only).

    Accepts multipart/form-data with:
    - file: The asset file
    - category: One of talent_speaking, product, pexels_broll
    """
    # Validate category
    if category not in VALID_CATEGORIES:
        raise ValidationError(
            message=f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}",
            details={"valid_categories": list(VALID_CATEGORIES)},
        )

    # Validate file format
    original_filename = file.filename or "unknown"
    ext, media_type = validate_file_format(original_filename)

    # Generate asset ID and create storage directory
    asset_id = generate_uuid()
    asset_dir = os.path.join(STORAGE_BASE, asset_id)
    os.makedirs(asset_dir, exist_ok=True)

    # Save file using streaming write to avoid loading entire file into memory
    stored_filename = f"original.{ext}"
    file_path = os.path.join(asset_dir, stored_filename)

    # Determine max allowed size for streaming validation
    from app.services.config_service import ConfigService
    config_service = ConfigService.get_instance()
    max_size_str = config_service.get_config(
        "upload_max_size", db, str(2 * 1024 * 1024 * 1024)
    )
    max_size = int(max_size_str)

    try:
        file_size = 0
        await file.seek(0)
        with open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                file_size += len(chunk)
                if file_size > max_size:
                    f.close()
                    os.remove(file_path)
                    max_mb = max_size / (1024 * 1024)
                    raise ValidationError(
                        message=f"File too large (>{max_mb:.0f}MB). Upload aborted.",
                    )
                f.write(chunk)
    except ValidationError:
        # Clean up the asset directory on size validation failure
        if os.path.exists(asset_dir):
            shutil.rmtree(asset_dir)
        raise

    # Generate thumbnail (non-blocking, skip if fails)
    thumbnail_path = generate_thumbnail(file_path, asset_dir, media_type)

    # Create database record
    asset = Asset(
        id=asset_id,
        filename=stored_filename,
        original_filename=original_filename,
        file_path=file_path,
        thumbnail_path=thumbnail_path,
        category=category,
        media_type=media_type,
        file_format=ext,
        file_size=file_size,
        duration=None,  # Duration extraction can be added later
        uploaded_by=current_user.id,
        created_at=utcnow(),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    # Trigger async asset analysis (VLM + audio detection + transcription)
    import threading
    from app.services.asset_analysis_service import AssetAnalysisService
    threading.Thread(
        target=AssetAnalysisService().analyze_asset,
        args=(asset.id,),
        daemon=True,
    ).start()

    return asset


@router.post("/upload/batch", response_model=BatchUploadResponse, status_code=201)
async def batch_upload_assets(
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Batch upload multiple asset files (Admin only).

    Accepts multipart/form-data with:
    - files: Multiple asset files
    - category: One of talent_speaking, product, pexels_broll (applied to all)
    """
    if category not in VALID_CATEGORIES:
        raise ValidationError(
            message=f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}",
            details={"valid_categories": list(VALID_CATEGORIES)},
        )

    from app.services.config_service import ConfigService
    config_service = ConfigService.get_instance()
    max_size_str = config_service.get_config(
        "upload_max_size", db, str(2 * 1024 * 1024 * 1024)
    )
    max_size = int(max_size_str)

    results: list[BatchUploadItemResult] = []
    succeeded = 0
    failed = 0

    for file in files:
        original_filename = file.filename or "unknown"
        asset_id = generate_uuid()
        asset_dir = os.path.join(STORAGE_BASE, asset_id)

        try:
            ext, media_type = validate_file_format(original_filename)

            os.makedirs(asset_dir, exist_ok=True)
            stored_filename = f"original.{ext}"
            file_path = os.path.join(asset_dir, stored_filename)

            file_size = 0
            await file.seek(0)
            with open(file_path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    file_size += len(chunk)
                    if file_size > max_size:
                        f.close()
                        os.remove(file_path)
                        max_mb = max_size / (1024 * 1024)
                        raise ValidationError(
                            message=f"File too large (>{max_mb:.0f}MB).",
                        )
                    f.write(chunk)

            thumbnail_path = generate_thumbnail(file_path, asset_dir, media_type)

            asset = Asset(
                id=asset_id,
                filename=stored_filename,
                original_filename=original_filename,
                file_path=file_path,
                thumbnail_path=thumbnail_path,
                category=category,
                media_type=media_type,
                file_format=ext,
                file_size=file_size,
                duration=None,
                uploaded_by=current_user.id,
                created_at=utcnow(),
            )
            db.add(asset)
            db.commit()
            db.refresh(asset)

            # Trigger async analysis
            import threading
            from app.services.asset_analysis_service import AssetAnalysisService
            threading.Thread(
                target=AssetAnalysisService().analyze_asset,
                args=(asset.id,),
                daemon=True,
            ).start()

            succeeded += 1
            results.append(BatchUploadItemResult(
                original_filename=original_filename,
                success=True,
                asset=AssetUploadResponse.model_validate(asset),
            ))

        except Exception as e:
            failed += 1
            if os.path.exists(asset_dir):
                shutil.rmtree(asset_dir)
            results.append(BatchUploadItemResult(
                original_filename=original_filename,
                success=False,
                error=str(e),
            ))

    return BatchUploadResponse(
        total=len(files),
        succeeded=succeeded,
        failed=failed,
        results=results,
    )


@router.get("", response_model=AssetListResponse)
def list_assets(
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get asset list with optional filtering and pagination.

    Query params:
    - category: Filter by category (talent_speaking, product, pexels_broll)
    - keyword: Search by original filename
    - page: Page number (default 1)
    - page_size: Items per page (default 20)
    """
    query = db.query(Asset)

    # Apply category filter
    if category:
        if category not in VALID_CATEGORIES:
            raise ValidationError(
                message=f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}",
            )
        query = query.filter(Asset.category == category)

    # Apply keyword search on original_filename
    if keyword:
        query = query.filter(Asset.original_filename.ilike(f"%{keyword}%"))

    # Get total count
    total = query.count()

    # Calculate pagination
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size

    # Fetch paginated results
    assets = query.order_by(Asset.created_at.desc()).offset(offset).limit(page_size).all()

    # Enrich with analysis data
    from app.models.database import AssetAnalysis
    asset_ids = [a.id for a in assets]
    analyses = {
        aa.asset_id: aa
        for aa in db.query(AssetAnalysis).filter(AssetAnalysis.asset_id.in_(asset_ids)).all()
    } if asset_ids else {}

    items = []
    for a in assets:
        data = AssetResponse.model_validate(a)
        aa = analyses.get(a.id)
        if aa:
            data.analysis_status = aa.status
            data.analysis_role = aa.role
            data.analysis_description = aa.description
            data.analysis_has_speech = aa.has_speech
        else:
            data.analysis_status = None
        items.append(data)

    return AssetListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/search", response_model=AssetSearchResponse)
def search_assets(
    q: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Semantic search for assets using natural language query.

    Generates an embedding from the query text and finds the most
    similar assets by cosine similarity against stored embeddings.

    Query params:
    - q: Search query text (e.g. "产品特写")
    - limit: Max results to return (default 10, max 50)
    """
    if not q or not q.strip():
        raise ValidationError(message="Search query 'q' must not be empty")

    limit = max(1, min(limit, 50))

    from app.services.asset_analysis_service import AssetAnalysisService
    analysis_service = AssetAnalysisService()
    results = analysis_service.search_by_text(q.strip(), limit=limit, db=db)

    items = [AssetSearchItem(**r) for r in results]
    return AssetSearchResponse(items=items, total=len(items))


@router.get("/{asset_id}/analysis", response_model=AssetAnalysisResponse)
def get_asset_analysis(
    asset_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get analysis results for an asset by ID.

    Returns the full VLM analysis data (status, description, role, tags, etc.).
    Returns 404 if the asset doesn't exist or has no analysis record.
    """
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise NotFoundError(message=f"Asset '{asset_id}' not found")

    from app.services.asset_analysis_service import AssetAnalysisService
    analysis_service = AssetAnalysisService()
    analysis_data = analysis_service.get_analysis(asset_id, db=db)

    if not analysis_data:
        raise NotFoundError(message=f"No analysis found for asset '{asset_id}'")

    return AssetAnalysisResponse(
        status=analysis_data.get("status", "pending"),
        error_message=analysis_data.get("error_message"),
        description=analysis_data.get("description"),
        role=analysis_data.get("role"),
        visual_quality=analysis_data.get("visual_quality"),
        scene_tags=analysis_data.get("scene_tags"),
        key_moments=analysis_data.get("key_moments"),
        audio_quality=analysis_data.get("audio_quality"),
        has_speech=analysis_data.get("has_speech"),
        speech_ranges=analysis_data.get("speech_ranges"),
        transcript=analysis_data.get("transcript"),
        vlm_model=analysis_data.get("vlm_model"),
        analyzed_at=analysis_data.get("analyzed_at"),
    )


@router.post("/{asset_id}/reanalyze", response_model=ReanalyzeResponse)
def reanalyze_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Trigger re-analysis of an asset (Admin only).

    Useful when VLM model has been upgraded or when a previous analysis failed.
    Launches async re-analysis in a background thread and returns immediately.
    Returns 404 if the asset doesn't exist.
    """
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise NotFoundError(message=f"Asset '{asset_id}' not found")

    import threading
    from app.services.asset_analysis_service import AssetAnalysisService
    threading.Thread(
        target=AssetAnalysisService().analyze_asset,
        args=(asset.id,),
        daemon=True,
    ).start()

    return ReanalyzeResponse(
        asset_id=asset.id,
        status="reanalyzing",
        message="重新分析已触发",
    )


@router.get("/{asset_id}", response_model=AssetDetailResponse)
def get_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get asset details by ID, including full analysis results."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise NotFoundError(message=f"Asset '{asset_id}' not found")

    # Build base response from asset
    response = AssetDetailResponse.model_validate(asset)

    # Load full analysis from asset_analysis table
    from app.services.asset_analysis_service import AssetAnalysisService
    analysis_service = AssetAnalysisService()
    analysis_data = analysis_service.get_analysis(asset_id, db=db)

    if analysis_data:
        response.analysis = AssetAnalysisResponse(
            status=analysis_data.get("status", "pending"),
            error_message=analysis_data.get("error_message"),
            description=analysis_data.get("description"),
            role=analysis_data.get("role"),
            visual_quality=analysis_data.get("visual_quality"),
            scene_tags=analysis_data.get("scene_tags"),
            key_moments=analysis_data.get("key_moments"),
            audio_quality=analysis_data.get("audio_quality"),
            has_speech=analysis_data.get("has_speech"),
            speech_ranges=analysis_data.get("speech_ranges"),
            transcript=analysis_data.get("transcript"),
            vlm_model=analysis_data.get("vlm_model"),
            analyzed_at=analysis_data.get("analyzed_at"),
        )

    return response


@router.delete("/{asset_id}", status_code=204)
def delete_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Delete an asset (Admin only). Removes both file and database record."""
    from app.models.database import AssetAnalysis, TaskAsset

    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise NotFoundError(message=f"Asset '{asset_id}' not found")

    # Delete files from storage
    asset_dir = os.path.join(STORAGE_BASE, asset_id)
    if os.path.exists(asset_dir):
        shutil.rmtree(asset_dir)

    # Remove foreign key references first, then delete the asset
    db.query(AssetAnalysis).filter(AssetAnalysis.asset_id == asset_id).delete()
    db.query(TaskAsset).filter(TaskAsset.asset_id == asset_id).delete()
    db.delete(asset)
    db.commit()
