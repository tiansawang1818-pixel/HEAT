import threading
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import jwt

from app.core.config import settings


class OIDCVerificationError(ValueError):
    pass


class OIDCVerifier:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client_state: Optional[Tuple[str, int, jwt.PyJWKClient]] = None

    @staticmethod
    def _configuration() -> Tuple[str, str, str]:
        issuer = (settings.oidc_issuer or "").rstrip("/")
        audience = settings.oidc_audience or ""
        jwks_url = settings.oidc_jwks_url or ""
        if not issuer or not audience or not jwks_url:
            raise OIDCVerificationError("OIDC issuer, audience and JWKS URL must be configured")
        if settings.environment.lower() == "production":
            if urlparse(issuer).scheme != "https" or urlparse(jwks_url).scheme != "https":
                raise OIDCVerificationError("Production OIDC issuer and JWKS URL must use HTTPS")
        return issuer, audience, jwks_url

    def _client(self, jwks_url: str) -> jwt.PyJWKClient:
        lifetime = max(60, settings.oidc_jwks_cache_seconds)
        with self._lock:
            if self._client_state and self._client_state[:2] == (jwks_url, lifetime):
                return self._client_state[2]
            client = jwt.PyJWKClient(
                jwks_url,
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=lifetime,
                timeout=max(1, settings.oidc_http_timeout_seconds),
            )
            self._client_state = (jwks_url, lifetime, client)
            return client

    def _signing_key(self, token: str, jwks_url: str) -> Any:
        return self._client(jwks_url).get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> Dict[str, Any]:
        if not token or len(token) > 16_384:
            raise OIDCVerificationError("Bearer token is missing or too large")
        issuer, audience, jwks_url = self._configuration()
        algorithms = settings.oidc_allowed_algorithms
        if not algorithms or any(value not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"} for value in algorithms):
            raise OIDCVerificationError("OIDC algorithm allowlist is invalid")
        try:
            claims = jwt.decode(
                token,
                self._signing_key(token, jwks_url),
                algorithms=algorithms,
                audience=audience,
                leeway=max(0, settings.oidc_clock_skew_seconds),
                options={"require": ["exp", "sub", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise OIDCVerificationError("OIDC bearer token validation failed") from exc
        if not isinstance(claims, dict):
            raise OIDCVerificationError("OIDC token claims are invalid")
        # Providers differ on the trailing slash (Auth0 issues "https://tenant/"),
        # so compare issuers with it removed; anything else must match exactly.
        if not isinstance(claims.get("iss"), str) or claims["iss"].rstrip("/") != issuer:
            raise OIDCVerificationError("OIDC bearer token validation failed")
        return claims


oidc_verifier = OIDCVerifier()
