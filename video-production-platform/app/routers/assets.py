"""Asset management router: upload, list, detail, delete endpoints."""

import os
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.models.database import Asset, User, generate_uuid, get_db, utcnow
from app.schemas.asset import AssetListResponse, AssetResponse, AssetUploadResponse
from app.services.asset_service import (
    STORAGE_BASE,
    VALID_CATEGORIES,
    generate_thumbnail,
    validate_file_format,
    validate_file_size,
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

    # Read file content to get size
    content = await file.read()
    file_size = len(content)

    # Validate file size
    validate_file_size(file_size, db)

    # Generate asset ID and create storage directory
    asset_id = generate_uuid()
    asset_dir = os.path.join(STORAGE_BASE, asset_id)
    os.makedirs(asset_dir, exist_ok=True)

    # Save original file
    stored_filename = f"original.{ext}"
    file_path = os.path.join(asset_dir, stored_filename)
    with open(file_path, "wb") as f:
        f.write(content)

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

    return asset


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

    return AssetListResponse(
        items=[AssetResponse.model_validate(a) for a in assets],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get asset details by ID."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise NotFoundError(message=f"Asset '{asset_id}' not found")
    return asset


@router.delete("/{asset_id}", status_code=204)
def delete_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Delete an asset (Admin only). Removes both file and database record."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise NotFoundError(message=f"Asset '{asset_id}' not found")

    # Delete files from storage
    asset_dir = os.path.join(STORAGE_BASE, asset_id)
    if os.path.exists(asset_dir):
        shutil.rmtree(asset_dir)

    # Delete database record
    db.delete(asset)
    db.commit()
