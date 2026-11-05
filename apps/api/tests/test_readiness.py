from unittest.mock import MagicMock

import pytest
import redis

from app.core.config import settings
from app.services.readiness import production_readiness


def _check(payload, code):
    return next(item for item in payload["checks"] if item["code"] == code)


@pytest.mark.parametrize("origin", [
    "http://heat.example.org", "*", "https://localhost", "https://127.0.0.2", "https://[::1]",
    "https://heat.example.org/path", "https://user:password@heat.example.org",
])
def test_readiness_rejects_non_production_cors_origins(db, monkeypatch, origin):
    monkeypatch.setattr(settings, "cors_origins", [origin])
    result = production_readiness(db, probe_dependencies=False)
    assert _check(result, "security.cors")["status"] == "blocked"
    assert origin not in str(result)


def test_readiness_closes_redis_and_does_not_expose_outage_details(db, monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value.ping.side_effect = ConnectionError("redis://user:sensitiveRedisCredential123@private-host")
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: connection)
    result = production_readiness(db)
    assert _check(result, "queue.redis")["status"] == "blocked"
    connection.__exit__.assert_called_once()
    assert "sensitiveRedisCredential123" not in str(result)
    assert "private-host" not in str(result)


def test_readiness_does_not_claim_provider_or_end_to_end_verification(db, monkeypatch):
    for name, value in {
        "environment": "production", "demo_mode": False, "allow_fixture_ingestion": False,
        "admin_auth_mode": "oidc", "oidc_issuer": "https://identity.example.org",
        "oidc_audience": "heatshift", "oidc_jwks_url": "https://identity.example.org/jwks",
        "oidc_roles_claim": "roles", "oidc_admin_roles": ["heatshift.admin"],
        "oidc_allowed_algorithms": ["RS256"], "cors_origins": ["https://heat.example.org"],
        "rate_limit_enabled": True, "gee_project": "configured-project",
    }.items():
        monkeypatch.setattr(settings, name, value)
    postgres = MagicMock()
    postgres.get_bind.return_value.dialect.name = "postgresql"
    postgres.execute.return_value.scalar_one_or_none.return_value = "3.5 USE_GEOS=1"
    connection = MagicMock()
    connection.__enter__.return_value.ping.return_value = True
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: connection)

    result = production_readiness(postgres)
    assert result["configuration_ready"] is True
    assert result["scope"] == "configuration_and_dependencies"
    assert "production_ready" not in result
    assert "worker/scheduler" in result["limitations_th"]
    assert "configured-project" not in str(result)
    assert production_readiness(postgres, probe_dependencies=False)["configuration_ready"] is False


def test_empty_oidc_role_allowlist_blocks_admin_instead_of_allowing_any_identity(client, monkeypatch):
    from app.services.auth import oidc_verifier

    monkeypatch.setattr(settings, "admin_auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_admin_roles", [])
    verify = MagicMock(return_value={"sub": "viewer"})
    monkeypatch.setattr(oidc_verifier, "verify", verify)
    response = client.post("/api/ingestion/run", json={"source": "open-meteo"}, headers={"Authorization": "Bearer viewer-token"})
    assert response.status_code == 503
    verify.assert_not_called()
