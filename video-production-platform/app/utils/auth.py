"""Authentication and authorization dependencies for FastAPI.

Permission Matrix:
- Intern: browse assets, generate/edit copywriting, TTS synthesis, video composition, view own tasks
- Operator: view pending review list, preview video, approve/reject
- Admin: all operations
"""

from typing import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.models.database import User, get_db
from app.services.auth_service import decode_access_token
from app.utils.errors import AuthError, PermissionDeniedError

# Permission matrix: maps operations to allowed roles
PERMISSION_MATRIX: dict[str, set[str]] = {
    # Asset operations
    "browse_assets": {"intern", "operator", "admin"},
    "upload_assets": {"admin"},
    "delete_assets": {"admin"},
    # Copywriting operations
    "generate_copywriting": {"intern", "admin"},
    "edit_copywriting": {"intern", "admin"},
    "view_copywriting": {"intern", "operator", "admin"},
    # TTS operations
    "tts_synthesize": {"intern", "admin"},
    # Video composition
    "compose_video": {"intern", "admin"},
    # Task operations
    "view_own_tasks": {"intern", "operator", "admin"},
    "view_pending_reviews": {"operator", "admin"},
    "view_all_tasks": {"admin"},
    # Review operations
    "review_approve": {"operator", "admin"},
    "review_reject": {"operator", "admin"},
    # Mixing operations
    "create_mix": {"intern", "operator", "admin"},
    "view_mix_status": {"intern", "operator", "admin"},
    "submit_mix_review": {"intern", "operator", "admin"},
    "retry_mix": {"intern", "operator", "admin"},
    "search_pexels": {"intern", "operator", "admin"},
    "generate_keywords": {"intern", "operator", "admin"},
    # Admin operations
    "manage_users": {"admin"},
    "manage_config": {"admin"},
    "manage_forbidden_words": {"admin"},
}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Extract and verify JWT token from Authorization header, return current user."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthError(message="Missing or invalid Authorization header")

    token = auth_header.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload is None:
        raise AuthError(message="Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError(message="Invalid token payload")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise AuthError(message="User not found")

    return user


def require_role(*roles: str) -> Callable:
    """Dependency factory that checks if the current user has one of the allowed roles.

    Usage:
        @router.get("/admin-only", dependencies=[Depends(require_role("admin"))])
        def admin_endpoint(): ...

    Or as a parameter dependency:
        def endpoint(user: User = Depends(require_role("admin", "operator"))): ...
    """

    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise PermissionDeniedError(
                message=f"Role '{current_user.role}' is not allowed. Required: {', '.join(roles)}",
                details={"required_roles": list(roles), "current_role": current_user.role},
            )
        return current_user

    return role_checker


def check_permission(role: str, operation: str) -> bool:
    """Check if a role has permission for a given operation.

    Args:
        role: User role (intern, operator, admin)
        operation: Operation key from PERMISSION_MATRIX

    Returns:
        True if allowed, False otherwise.
    """
    allowed_roles = PERMISSION_MATRIX.get(operation)
    if allowed_roles is None:
        return False
    return role in allowed_roles


def require_permission(operation: str) -> Callable:
    """Dependency factory that checks permission based on the operation key.

    Uses PERMISSION_MATRIX to determine if the user's role allows the operation.
    Returns 403 PERMISSION_DENIED if not allowed.
    """

    def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        if not check_permission(current_user.role, operation):
            raise PermissionDeniedError(
                message=f"Permission denied for operation '{operation}'",
                details={"operation": operation, "current_role": current_user.role},
            )
        return current_user

    return permission_checker
