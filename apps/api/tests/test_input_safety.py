import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.connectors.base import SourceAsset
from app.services.connectors.open_meteo import OpenMeteoConnector


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e10000"])
def test_non_finite_priority_requests_return_422_without_echoing_raw_inputs(client, value):
    response = client.post("/api/priority/calculate", content='{"temperature_anomaly":' + value + '}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "finite_number"
    assert "input" not in response.json()["detail"][0]


def test_optimization_rejects_unknown_weights_and_unbounded_candidate_lists(client):
    assert client.post("/api/optimization/run", json={"budget_thb": 100000, "weights": {"unsupported": 1}}).status_code == 422
    assert client.post("/api/optimization/run", json={"budget_thb": 100000, "candidate_ids": ["site"] * 501}).status_code == 422


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True])
def test_weather_validation_rejects_non_finite_or_boolean_values(invalid):
    asset = SourceAsset.with_checksum("unit-weather", "unit-only", datetime.now(timezone.utc), {"type":"Point","coordinates":[100.5,13.7]}, payload={
        "current": {"time":"2020-01-01T00:00", "temperature_2m":invalid, "relative_humidity_2m":60, "apparent_temperature":30, "wind_speed_10m":2},
        "current_units": {"temperature_2m":"°C","wind_speed_10m":"km/h"},
        "hourly": {"time":["2020-01-01T01:00"],"temperature_2m":[invalid],"apparent_temperature":[30],"relative_humidity_2m":[60],"precipitation_probability":[10]},
        "hourly_units": {"temperature_2m":"°C","precipitation_probability":"%"},
    })
    report = OpenMeteoConnector().validate(asset)
    assert not report["passed"]
    assert any("finite" in error for error in report["errors"])


def test_monitoring_preflight_rejects_placeholders_without_printing_the_receiver():
    path = Path(__file__).resolve().parents[3] / "scripts/check_alerting_config.py"
    spec = importlib.util.spec_from_file_location("alerting_config", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert module.validate_text("url: https://CHANGE_ME.invalid/private-secret")
    assert module.validate_text("url: http://private.example.net/private-secret")
    assert not module.validate_text("url: https://ops.company.test/private-secret")
    assert "private-secret" not in str(module.validate_text("url: http://private.example.net/private-secret"))
