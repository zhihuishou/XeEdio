"""Authentication router: login endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.schemas.auth import LoginRequest, LoginResponse
from app.services.auth_service import authenticate_user, create_access_token
from app.utils.errors import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate user and return JWT token."""
    user = authenticate_user(db, body.username, body.password)
    if user is None:
        raise AuthError(message="Invalid username or password")

    token = create_access_token(user_id=user.id, role=user.role)

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
        role=user.role,
    )
