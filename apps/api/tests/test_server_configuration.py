import importlib.util
import stat
from pathlib import Path

import pytest


def test_server_configuration_has_private_permissions_independent_random_secrets_and_no_invented_accounts(tmp_path):
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("server_config", root / "scripts/init_production_env.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    target = tmp_path / "private.env"
    module.create_configuration(target, root / ".env.production.example")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    values = dict(line.split("=", 1) for line in target.read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert len(values["POSTGRES_PASSWORD"]) == len(values["AUTH_SESSION_SECRET"]) == 64
    assert values["POSTGRES_PASSWORD"] != values["AUTH_SESSION_SECRET"]
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    assert not values["GEE_PROJECT"] and not values["OIDC_ISSUER"] and not values["HEATSHIFT_DOMAIN"]
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        module.create_configuration(target, root / ".env.production.example")
    assert target.read_bytes() == before
