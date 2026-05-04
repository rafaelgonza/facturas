"""Authentication helpers: bcrypt password hashing and session check."""
import bcrypt
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def require_auth(request: Request):
    """FastAPI dependency: raises a redirect to /login if not authenticated."""
    if not is_authenticated(request):
        # Use 303 to ensure GET on the redirect target
        raise HTTPException(
            status_code=303,
            headers={"Location": "/login"},
        )
    return True
