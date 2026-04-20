"""Authentication service: password hashing, JWT token generation and verification."""

import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.models.database import User

# JWT Configuration
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: str, role: str, expires_delta: timedelta | None = None) -> str:
    """Generate a JWT token containing user_id, role, and expiration."""
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode and verify a JWT token. Returns payload dict or None if invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """Authenticate user by username and password. Returns User or None."""
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
