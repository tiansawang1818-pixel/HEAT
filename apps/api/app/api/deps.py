import re
import secrets

from typing import Any, Dict, List

from fastapi import Header, HTTPException, status

from app.core.config import settings
from app.services.auth import OIDCVerificationError, oidc_verifier


def _claim(claims: Dict[str, Any], path: str) -> Any:
    # Namespaced claims such as Auth0's "https://example.org/roles" contain
    # dots, so an exact key wins before the path is treated as nested.
    if path in claims:
        return claims[path]
    value: Any = claims
    for segment in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _roles(claims: Dict[str, Any]) -> List[str]:
    value = _claim(claims, settings.oidc_roles_claim)
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int))]
    return []


def _safe_actor(value: Any) -> str:
    actor = re.sub(r"[^a-zA-Z0-9_.:@-]", "-", str(value or "").strip())[:80]
    return actor or "operator"


def require_admin(
    x_admin_key: str = Header(default=""),
    x_admin_actor: str = Header(default="operator"),
    authorization: str = Header(default=""),
) -> str:
    mode = settings.admin_auth_mode.lower()
    if settings.environment.lower() == "production" and mode != "oidc":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Production admin authentication requires OIDC",
        )
    if mode == "oidc":
        if not settings.oidc_roles_claim.strip() or not settings.oidc_admin_roles or any(
            not role.strip() for role in settings.oidc_admin_roles
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OIDC admin role configuration is required",
            )
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC bearer token is required")
        try:
            claims = oidc_verifier.verify(token)
        except OIDCVerificationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        required_roles = set(settings.oidc_admin_roles)
        if required_roles and not required_roles.intersection(_roles(claims)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="OIDC admin role is required")
        actor = claims.get("preferred_username") or claims.get("email") or claims.get("sub")
        return _safe_actor(actor)
    if mode != "local_key":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin authentication mode is invalid")
    configured = settings.admin_api_key or ""
    if not configured or not secrets.compare_digest(x_admin_key.encode(), configured.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin API key is required")
    return _safe_actor(x_admin_actor)
