"""Forbidden words CRUD and check API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import ForbiddenWord, User, get_db
from app.schemas.forbidden_word import (
    ForbiddenWordCheckRequest,
    ForbiddenWordCheckResponse,
    ForbiddenWordCreate,
    ForbiddenWordImportRequest,
    ForbiddenWordImportResponse,
    ForbiddenWordListResponse,
    ForbiddenWordMatch,
    ForbiddenWordResponse,
)
from app.services.rag_service import RAGService
from app.utils.auth import require_role
from app.utils.errors import NotFoundError

router = APIRouter(prefix="/api/forbidden-words", tags=["forbidden-words"])


@router.get("", response_model=ForbiddenWordListResponse)
def list_forbidden_words(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Get all forbidden words (Admin only)."""
    words = db.query(ForbiddenWord).order_by(ForbiddenWord.created_at.desc()).all()
    items = [
        ForbiddenWordResponse(
            id=w.id,
            word=w.word,
            category=w.category,
            suggestion=w.suggestion,
            created_at=w.created_at.isoformat() if w.created_at else None,
        )
        for w in words
    ]
    return ForbiddenWordListResponse(items=items, total=len(items))


@router.post("", response_model=ForbiddenWordResponse, status_code=201)
def add_forbidden_word(
    body: ForbiddenWordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Add a forbidden word (Admin only). Syncs to ChromaDB."""
    fw = ForbiddenWord(
        word=body.word,
        category=body.category,
        suggestion=body.suggestion,
    )
    db.add(fw)
    db.commit()
    db.refresh(fw)

    # Sync to ChromaDB
    rag = RAGService.get_instance()
    rag.add_forbidden_word(body.word, body.category, body.suggestion)

    return ForbiddenWordResponse(
        id=fw.id,
        word=fw.word,
        category=fw.category,
        suggestion=fw.suggestion,
        created_at=fw.created_at.isoformat() if fw.created_at else None,
    )


@router.delete("/{word_id}", status_code=204)
def delete_forbidden_word(
    word_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Delete a forbidden word (Admin only). Syncs removal to ChromaDB."""
    fw = db.query(ForbiddenWord).filter(ForbiddenWord.id == word_id).first()
    if not fw:
        raise NotFoundError(message="Forbidden word not found")

    word_text = fw.word
    db.delete(fw)
    db.commit()

    # Sync removal to ChromaDB
    rag = RAGService.get_instance()
    rag.remove_forbidden_word(word_text)

    return None


@router.post("/import", response_model=ForbiddenWordImportResponse)
def import_forbidden_words(
    body: ForbiddenWordImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Batch import forbidden words (Admin only). Syncs to ChromaDB."""
    imported = 0
    skipped = 0
    rag = RAGService.get_instance()

    for item in body.words:
        # Check if word already exists
        existing = db.query(ForbiddenWord).filter(ForbiddenWord.word == item.word).first()
        if existing:
            skipped += 1
            continue

        fw = ForbiddenWord(
            word=item.word,
            category=item.category,
            suggestion=item.suggestion,
        )
        db.add(fw)
        rag.add_forbidden_word(item.word, item.category, item.suggestion)
        imported += 1

    db.commit()
    return ForbiddenWordImportResponse(imported=imported, skipped=skipped)


@router.post("/check", response_model=ForbiddenWordCheckResponse)
def check_forbidden_words(
    body: ForbiddenWordCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "admin")),
):
    """Check text for forbidden words. Returns matches with positions and suggestions."""
    # Get all forbidden words from DB for exact matching
    all_words = db.query(ForbiddenWord).all()
    words_list = [
        {"word": w.word, "category": w.category or "", "suggestion": w.suggestion or ""}
        for w in all_words
    ]

    # Use RAG service for combined exact + semantic matching
    rag = RAGService.get_instance()
    matches = rag.check_text(body.text, words_list)

    status = "contains_forbidden" if matches else "passed"
    match_models = [
        ForbiddenWordMatch(
            word=m["word"],
            position=m["position"],
            category=m.get("category"),
            suggestion=m.get("suggestion"),
        )
        for m in matches
    ]

    return ForbiddenWordCheckResponse(
        status=status,
        matches=match_models,
        text=body.text,
    )
