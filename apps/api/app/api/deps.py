import re
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_admin(
    x_admin_key: str = Header(default=""),
    x_admin_actor: str = Header(default="operator"),
) -> str:
    configured = settings.admin_api_key or ""
    if settings.environment == "production" and configured in {
        "change-me-in-production",
        "local-admin-key-change-me",
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin authentication is not configured for production",
        )
    if not configured or not secrets.compare_digest(x_admin_key.encode(), configured.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin API key is required")
    actor = re.sub(r"[^a-zA-Z0-9_.:@-]", "-", x_admin_actor.strip())[:80]
    return actor or "operator"
