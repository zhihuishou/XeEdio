"""User management router: CRUD endpoints (Admin only)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import User, get_db, generate_uuid, utcnow
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services.auth_service import hash_password
from app.utils.auth import require_role
from app.utils.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/users", tags=["users"])

VALID_ROLES = {"intern", "operator", "admin"}


@router.get("", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Get all users (Admin only)."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return users


@router.post("", response_model=UserResponse, status_code=201)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Create a new user (Admin only)."""
    if body.role not in VALID_ROLES:
        raise ValidationError(
            message=f"Invalid role '{body.role}'. Must be one of: {', '.join(VALID_ROLES)}",
            details={"valid_roles": list(VALID_ROLES)},
        )

    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise ValidationError(message=f"Username '{body.username}' already exists")

    user = User(
        id=generate_uuid(),
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Update user info/role (Admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise NotFoundError(message=f"User '{user_id}' not found")

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise ValidationError(
                message=f"Invalid role '{body.role}'. Must be one of: {', '.join(VALID_ROLES)}",
                details={"valid_roles": list(VALID_ROLES)},
            )
        user.role = body.role

    if body.username is not None:
        existing = db.query(User).filter(User.username == body.username, User.id != user_id).first()
        if existing:
            raise ValidationError(message=f"Username '{body.username}' already exists")
        user.username = body.username

    if body.password is not None:
        user.password_hash = hash_password(body.password)

    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Delete a user (Admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise NotFoundError(message=f"User '{user_id}' not found")

    db.delete(user)
    db.commit()
