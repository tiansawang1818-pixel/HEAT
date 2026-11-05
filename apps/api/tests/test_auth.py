from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings
from app.services.auth import OIDCVerificationError, OIDCVerifier


def test_oidc_verifier_checks_signature_issuer_audience_and_expiry(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(settings, "oidc_issuer", "https://identity.example.org")
    monkeypatch.setattr(settings, "oidc_audience", "heatshift-api")
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://identity.example.org/.well-known/jwks.json")
    monkeypatch.setattr(settings, "oidc_allowed_algorithms", ["RS256"])
    verifier = OIDCVerifier()
    monkeypatch.setattr(verifier, "_signing_key", lambda token, url: public_key)
    token = jwt.encode(
        {
            "iss": "https://identity.example.org",
            "aud": "heatshift-api",
            "sub": "admin-123",
            "roles": ["heatshift.admin"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    claims = verifier.verify(token)
    assert claims["sub"] == "admin-123"

    wrong_audience = jwt.encode(
        {
            "iss": "https://identity.example.org",
            "aud": "another-api",
            "sub": "admin-123",
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )
    with pytest.raises(OIDCVerificationError):
        verifier.verify(wrong_audience)


def test_oidc_verifier_refuses_non_https_configuration_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "oidc_issuer", "http://identity.example.org")
    monkeypatch.setattr(settings, "oidc_audience", "heatshift-api")
    monkeypatch.setattr(settings, "oidc_jwks_url", "http://identity.example.org/jwks.json")

    with pytest.raises(OIDCVerificationError, match="HTTPS"):
        OIDCVerifier().verify("header.payload.signature")
