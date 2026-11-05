from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.entities import (
    AreaIndicator,
    CandidateIntervention,
    DataSource,
    DatasetVersion,
    GeographicArea,
    ForecastRun,
    IngestionJob,
    OptimizationAllocation,
    OptimizationRun,
    PipelineEvent,
    PriorityScore,
    SourceCheck,
    TemperatureObservation,
    ValidationReport,
)


def test_health_and_dataset_status(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health/live").json()["status"] == "ok"
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    response = client.get("/api/datasets/status")
    assert response.status_code == 200
    assert response.json()["items"]
    assert all("last_source_observation" in item and "last_system_check" in item for item in response.json()["items"])
    assert all(item["dataset_id"] != "local/integration-fixture" for item in response.json()["items"])


def test_production_readiness_reports_actionable_safe_blockers(client):
    response = client.get("/api/system/readiness?probe_dependencies=false")
    assert response.status_code == 200
    payload = response.json()
    assert payload["configuration_ready"] is False
    assert "production_ready" not in payload
    assert payload["scope"] == "configuration_and_dependencies"
    assert response.headers["Cache-Control"] == "no-store"
    assert payload["status"] == "not_ready"
    assert payload["secrets_exposed"] is False
    assert payload["summary"]["blocked"] > 0
    assert {item["code"] for item in payload["checks"]}.issuperset(
        {"storage.postgis", "security.oidc", "provider.earth_engine_project"}
    )
    wire = response.text
    assert "test-admin-key" not in wire
    assert "redis://" not in wire
    assert "jwks_url" not in wire


def test_expensive_endpoint_returns_rate_limit_metadata(client, monkeypatch):
    from app.main import rate_limiter
    from app.services.rate_limit import RateLimitDecision

    monkeypatch.setattr(
        rate_limiter,
        "check",
        lambda *args, **kwargs: RateLimitDecision(False, 6, 0, 42),
    )
    response = client.post("/api/forecast/run", json={"horizon_years": 5})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.headers["X-RateLimit-Limit"] == "6"
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_operational_headers_echo_safe_request_id_and_replace_invalid_value(client):
    response = client.get("/api/health/live", headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"

    replaced = client.get("/api/health/live", headers={"X-Request-ID": "bad request id with spaces"})
    assert replaced.headers["X-Request-ID"] != "bad request id with spaces"
    assert len(replaced.headers["X-Request-ID"]) == 32


def test_prometheus_metrics_use_durable_pipeline_state_without_leaking_errors(client, db):
    source = db.scalar(select(DataSource).where(DataSource.source_type != "test_fixture").limit(1))
    version = DatasetVersion(
        source_id=source.id,
        source_asset_id="metrics-asset",
        source_version="v1",
        observed_at=datetime.now(timezone.utc) - timedelta(hours=2),
        status="published",
        quality_status="valid",
        layer_version=1,
    )
    db.add(version)
    db.flush()
    db.add_all(
        [
            SourceCheck(source_id=source.id, status="success", completed_at=datetime.now(timezone.utc)),
            IngestionJob(source_id=source.id, status="failed", error_message="secret-provider-payload"),
            ValidationReport(dataset_version_id=version.id, passed=False, checks={}, errors=["range"], warnings=[]),
        ]
    )
    db.commit()

    response = client.get("/api/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "heatshift_dataset_versions_total{status=\"published\"} 1" in response.text
    assert "heatshift_ingestion_failed_jobs_24h 1" in response.text
    assert "heatshift_validation_failures_24h 1" in response.text
    assert "heatshift_data_source_last_observation_age_seconds" in response.text
    assert "secret-provider-payload" not in response.text


def test_weather_context_returns_only_latest_published_provider_payload(client, db):
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == "open-meteo/weather-context"))
    observed_at = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
    published = DatasetVersion(
        source_id=source.id,
        source_asset_id="weather:published",
        source_version="forecast-api-v1",
        observed_at=observed_at,
        published_at=observed_at + timedelta(minutes=1),
        status="published",
        quality_status="valid",
        layer_version=4,
        geographic_extent={"type": "Point", "coordinates": [100.5018, 13.7563]},
        metadata_json={
            "data_class": "model_estimate_and_forecast",
            "provider_model": "best_match",
            "provider_timezone": "UTC",
            "reference_time": observed_at.isoformat(),
            "provider_payload": {
                "current": {"temperature_2m": 31.2, "apparent_temperature": 35.4, "relative_humidity_2m": 63, "wind_speed_10m": 7.1},
                "current_units": {"temperature_2m": "°C", "apparent_temperature": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"},
                "hourly": {"time": ["2026-10-31T04:00"], "temperature_2m": [31.2]},
                "hourly_units": {"temperature_2m": "°C"},
            },
        },
    )
    failed = DatasetVersion(
        source_id=source.id,
        source_asset_id="weather:failed",
        source_version="forecast-api-v1",
        observed_at=observed_at + timedelta(hours=1),
        status="failed",
        quality_status="rejected",
        geographic_extent={"type": "Point", "coordinates": [100.5018, 13.7563]},
        metadata_json={"provider_payload": {"current": {"temperature_2m": 999}}},
    )
    db.add_all([published, failed])
    db.commit()

    response = client.get("/api/weather/context")
    assert response.status_code == 200
    body = response.json()
    assert body["current"]["temperature_2m"] == 31.2
    assert body["current"]["temperature_2m"] != 999
    assert body["data_class"] == "model_estimate_and_forecast"
    assert body["provider_model"] == "best_match"
    assert body["layer_version"] == 4
    assert "ไม่ใช่อุณหภูมิพื้นผิวจากดาวเทียม" in body["limitations_th"]


def test_weather_context_is_transparently_unavailable_before_publication(client):
    response = client.get("/api/weather/context")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["current"] is None
    assert body["reference_time"] is None


def test_dataset_version_history_exposes_validation_but_not_provider_payload(client, db):
    source = db.scalar(select(DataSource).where(DataSource.dataset_id == "open-meteo/weather-context"))
    published = DatasetVersion(
        source_id=source.id,
        source_asset_id="weather:valid",
        source_version="forecast-api-v1",
        observed_at=datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc),
        discovered_at=datetime(2026, 11, 4, 1, 1, tzinfo=timezone.utc),
        published_at=datetime(2026, 11, 4, 1, 2, tzinfo=timezone.utc),
        status="published",
        quality_status="valid",
        layer_version=2,
        geographic_extent={"type": "Point", "coordinates": [100.5018, 13.7563]},
        metadata_json={
            "data_class": "model_estimate_and_forecast",
            "provider_payload": {"current": {"temperature_2m": 31.2}},
        },
    )
    failed = DatasetVersion(
        source_id=source.id,
        source_asset_id="weather:rejected",
        source_version="forecast-api-v1",
        observed_at=datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc),
        discovered_at=datetime(2026, 11, 4, 2, 1, tzinfo=timezone.utc),
        status="failed",
        quality_status="rejected",
        layer_version=0,
        geographic_extent={"type": "Point", "coordinates": [100.5018, 13.7563]},
        metadata_json={"provider_payload": {"raw": "must-not-be-public"}},
    )
    db.add_all([published, failed])
    db.flush()
    db.add_all(
        [
            ValidationReport(
                dataset_version_id=published.id,
                passed=True,
                checks={"forecast_hours": 48},
                warnings=[],
                errors=[],
            ),
            ValidationReport(
                dataset_version_id=failed.id,
                passed=False,
                checks={"forecast_hours": 0},
                warnings=[],
                errors=["missing Open-Meteo hourly fields"],
            ),
        ]
    )
    db.commit()

    response = client.get(f"/api/datasets/{source.id}/versions")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["source_asset_id"] == "weather:rejected"
    assert body["items"][0]["validation"]["passed"] is False
    assert "hourly fields" in body["items"][0]["validation"]["errors"][0]
    assert body["items"][0]["provider_payload_preserved"] is True
    assert "provider_payload" not in body["items"][0]["metadata"]
    assert "must-not-be-public" not in response.text

    filtered = client.get(f"/api/datasets/{source.id}/versions?status=published")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["layer_version"] == 2

    fixture = db.scalar(select(DataSource).where(DataSource.dataset_id == "local/integration-fixture"))
    assert client.get(f"/api/datasets/{fixture.id}/versions").status_code == 404


def test_manual_ingestion_requires_admin(client):
    response = client.post("/api/ingestion/run", json={"source": "open-meteo"})
    assert response.status_code == 401


def test_ingestion_status_exposes_observability_snapshot(client, db):
    source = db.scalar(select(DataSource).where(DataSource.source_type != "test_fixture").limit(1))
    db.add_all(
        [
            SourceCheck(
                source_id=source.id,
                status="success",
                discovered_count=2,
                queued_count=1,
                provider_quota={"status": "available", "remaining": 80},
            ),
            IngestionJob(source_id=source.id, status="running", stage="validation", progress=0.4, attempts=1),
            PipelineEvent(event_type="monitor.completed", source_id=source.id, payload={"discovered": 2}),
        ]
    )
    db.commit()

    response = client.get("/api/ingestion/status")
    assert response.status_code == 200
    body = response.json()
    assert body["queue"]["running"] == 1
    assert body["summary"]["discovered_assets_latest_checks"] == 2
    source_status = next(item for item in body["source_checks"] if item["source_id"] == source.id)
    assert source_status["provider_quota"]["remaining"] == 80
    assert body["recent_jobs"][0]["stage"] == "validation"
    assert body["recent_events"][0]["type"] == "monitor.completed"
    assert all(item["dataset_id"] != "local/integration-fixture" for item in body["source_checks"])


def test_sse_cursor_orders_same_timestamp_events_without_replaying_history(db):
    from app.api.router import _events_after_cursor

    created = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)
    db.add_all(
        [
            PipelineEvent(id="event-a", event_type="layer.published", created_at=created, payload={}),
            PipelineEvent(id="event-b", event_type="layer.published", created_at=created, payload={}),
            PipelineEvent(id="event-c", event_type="layer.published", created_at=created, payload={}),
        ]
    )
    db.commit()
    rows = _events_after_cursor(db, "event-b")
    assert [item.id for item in rows] == ["event-c"]
    assert _events_after_cursor(db, "event-missing") is None


def test_sse_delivers_the_first_publication_to_an_already_connected_client(db):
    import asyncio
    from app.api.router import events_stream

    class ConnectedRequest:
        headers = {}

        async def is_disconnected(self):
            return False

    async def scenario():
        response = await events_stream(ConnectedRequest(), db=db)
        assert "no-transform" in response.headers["Cache-Control"]
        body = response.body_iterator
        assert "connected" in await body.__anext__()
        db.add(PipelineEvent(id="first-live-publication", event_type="layer.published", layer_key="temperature", payload={"affected_layers": ["temperature"]}))
        db.commit()
        wire = await asyncio.wait_for(body.__anext__(), timeout=1)
        assert "event: data-update" in wire
        assert "id: first-live-publication" in wire
        await body.aclose()

    asyncio.run(scenario())


def test_layer_freshness_uses_observation_age_and_failed_checks_preserve_map(client, db):
    now = datetime.now(timezone.utc)
    observed_at = now - timedelta(days=90)
    source = db.scalar(select(DataSource).where(DataSource.name == "Landsat 8/9"))
    area = GeographicArea(id="freshness-area", name_th="พื้นที่ทดสอบ", name_en="Test area", geometry={"type": "MultiPolygon", "coordinates": [[[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]]})
    version = DatasetVersion(source_id=source.id, source_asset_id="old-validated-asset", source_version="v1", status="published", quality_status="valid", layer_version=1, observed_at=observed_at, published_at=now - timedelta(hours=1))
    db.add_all([area, version])
    db.flush()
    db.add(AreaIndicator(area_id=area.id, dataset_version_id=version.id, indicator_key="lst_c_mean", value=35.0, observed_at=observed_at, quality_status="valid"))
    check = SourceCheck(source_id=source.id, status="success", checked_at=now, completed_at=now)
    db.add(check)
    db.commit()
    layer = next(item for item in client.get("/api/map/layers").json()["items"] if item["id"] == "temperature")
    assert layer["status"] == "stale"
    check.status = "failed"
    db.commit()
    layer = next(item for item in client.get("/api/map/layers").json()["items"] if item["id"] == "temperature")
    assert layer["status"] == "error"
    assert client.get("/api/map/features?layer=temperature").json()["features"][0]["properties"]["value"] == 35.0


def test_synthetic_records_do_not_make_a_provider_layer_current(client, db):
    area = GeographicArea(id="fixture-only-area", name_th="พื้นที่สังเคราะห์", name_en="Synthetic test", quality_status="synthetic_demo", geometry={"type": "MultiPolygon", "coordinates": [[[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]]})
    db.add(area)
    db.flush()
    db.add(AreaIndicator(area_id=area.id, indicator_key="lst_c_mean", value=99.0, observed_at=datetime.now(timezone.utc), quality_status="synthetic_demo"))
    db.commit()
    layer = next(item for item in client.get("/api/map/layers").json()["items"] if item["id"] == "temperature")
    assert layer["status"] == "unavailable"
    feature = client.get("/api/map/features?layer=temperature").json()["features"][0]
    assert feature["properties"]["quality_status"] == "synthetic_demo"
    assert feature["properties"]["boundary_quality_status"] == "synthetic_demo"


def test_priority_calculation_endpoint(client):
    response = client.post(
        "/api/priority/calculate",
        json={
            "temperature_anomaly": 4.2,
            "population_exposure": 10000,
            "vegetation_deficit": 0.7,
            "built_intensity": 0.8,
            "historical_change": 0.2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert 0 <= body["score"] <= 100
    assert body["label"] == "Heat Exposure Priority"


def test_priority_rankings_use_latest_score_and_expose_formula(client, db):
    area = GeographicArea(
        id="priority-area",
        name_th="พื้นที่ลำดับความสำคัญ",
        name_en="Priority area",
        geometry={"type": "Point", "coordinates": [100.52, 13.74]},
        source_name="test",
    )
    db.add(area)
    db.flush()
    db.add_all(
        [
            PriorityScore(
                area_id=area.id,
                score=40,
                components={"temperature_anomaly": 0.4},
                weights={"temperature_anomaly": 1.0},
                missing_inputs=["population_exposure"],
                input_versions={"landsat": "v1"},
                formula_version="priority-v1",
            ),
            PriorityScore(
                area_id=area.id,
                score=78,
                components={"temperature_anomaly": 0.8, "population_exposure": 0.7},
                weights={"temperature_anomaly": 0.5, "population_exposure": 0.5},
                missing_inputs=[],
                input_versions={"landsat": "v2", "worldpop": "2025"},
                formula_version="priority-v1",
            ),
        ]
    )
    db.commit()

    response = client.get("/api/priority/rankings")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["score"] == 78
    assert body["items"][0]["formula_version"] == "priority-v1"
    assert body["label"] == "Heat Exposure Priority"
    assert "ไม่ใช่ข้อสรุปเรื่องรายได้" in body["interpretation_warning_th"]


def test_assistant_runs_grounded_budget_optimization_and_records_portfolio(client, db):
    area = GeographicArea(
        id="assistant-area",
        name_th="พื้นที่ผู้ช่วย",
        name_en="Assistant area",
        geometry={"type": "Point", "coordinates": [100.52, 13.74]},
        source_name="test",
    )
    candidates = [
        CandidateIntervention(
            id="assistant-tree",
            area_id=area.id,
            name_th="แนวต้นไม้ที่ผ่านการสำรวจ",
            intervention_type="tree_planting",
            geometry={"type": "Point", "coordinates": [100.52, 13.74]},
            estimated_cost_thb=2_000_000,
            estimated_beneficiaries=1_200,
            priority_score=86,
            benefit_score=0.8,
            feasibility_status="eligible",
            evidence={"source": "approved-site-survey"},
            uncertainty={"level": "medium"},
        ),
        CandidateIntervention(
            id="assistant-shade",
            area_id=area.id,
            name_th="โครงสร้างร่มเงาที่ผ่านการสำรวจ",
            intervention_type="shade_structure",
            geometry={"type": "Point", "coordinates": [100.53, 13.75]},
            estimated_cost_thb=3_000_000,
            estimated_beneficiaries=1_800,
            priority_score=79,
            benefit_score=0.7,
            feasibility_status="eligible",
            evidence={"source": "approved-site-survey"},
            uncertainty={"level": "medium"},
        ),
        CandidateIntervention(
            id="assistant-unverified",
            area_id=area.id,
            name_th="โครงการที่ยังไม่มีหลักฐาน",
            intervention_type="cooling_rest_area",
            geometry={"type": "Point", "coordinates": [100.54, 13.76]},
            estimated_cost_thb=100_000,
            estimated_beneficiaries=20_000,
            priority_score=99,
            benefit_score=1.0,
            feasibility_status="eligible",
            evidence={},
            uncertainty={"level": "unknown"},
        ),
    ]
    db.add_all([area, *candidates])
    db.commit()

    response = client.post(
        "/api/assistant/query",
        json={"question": "มีงบ ๕ ล้านบาท ควรลงทุนตรงไหน?", "area_id": area.id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "deterministic"
    assert body["invented_values"] is False
    assert body["optimization"]["allocated_thb"] == 5_000_000
    assert len(body["optimization"]["selected"]) == 2
    assert "assistant-unverified" not in {item["id"] for item in body["optimization"]["selected"]}
    assert body["optimization"]["selected"][0]["geometry"]["type"] == "Point"
    assert body["grounding"][0]["type"] == "optimization_run"
    assert db.get(OptimizationRun, body["optimization"]["run_id"]) is not None
    allocations = db.scalars(
        select(OptimizationAllocation).where(OptimizationAllocation.run_id == body["optimization"]["run_id"])
    ).all()
    assert len(allocations) == 2


def test_assistant_rejects_budget_outside_optimizer_range(client):
    response = client.post("/api/assistant/query", json={"question": "มีงบ 50,000 บาท"})
    assert response.status_code == 200
    body = response.json()
    assert body["optimization"] is None
    assert "100,000 ถึง 100,000,000 บาท" in body["answer_th"]


def test_optimization_rejects_out_of_range_budget(client):
    response = client.post("/api/optimization/run", json={"budget_thb": 99999})
    assert response.status_code == 422


def test_map_features_applies_viewport_bbox(client, db):
    db.add_all(
        [
            GeographicArea(
                id="inside",
                name_th="ด้านใน",
                name_en="Inside",
                geometry={"type": "Point", "coordinates": [100.5, 13.75]},
                source_name="test",
            ),
            GeographicArea(
                id="outside",
                name_th="ด้านนอก",
                name_en="Outside",
                geometry={"type": "Point", "coordinates": [101.5, 14.75]},
                source_name="test",
            ),
        ]
    )
    db.commit()
    response = client.get("/api/map/features?layer=priority&bbox=100.4,13.6,100.7,13.9&layer_version=3")
    assert response.status_code == 200
    assert [feature["id"] for feature in response.json()["features"]] == ["inside"]
    assert response.json()["metadata"]["requested_layer_version"] == 3
    assert response.json()["metadata"]["spatial_filter"] == "application_bounds_fallback"
    assert response.headers["Cache-Control"].startswith("private, max-age=60")
    assert response.headers["ETag"].startswith('"')
    cached = client.get(
        "/api/map/features?layer=priority&bbox=100.4,13.6,100.7,13.9&layer_version=3",
        headers={"If-None-Match": response.headers["ETag"]},
    )
    assert cached.status_code == 304


def test_map_features_rejects_invalid_bbox(client):
    response = client.get("/api/map/features?bbox=100.5,13.7,99.0,14.0")
    assert response.status_code == 422


def test_future_risk_layer_uses_recorded_scenario_projection(client, db):
    area = GeographicArea(
        id="future-area",
        name_th="พื้นที่อนาคต",
        name_en="Future area",
        geometry={"type": "Point", "coordinates": [100.55, 13.76]},
        source_name="test",
    )
    forecast = ForecastRun(
        horizon_years=3,
        method="transparent-linear-trend-scenario-v1",
        model_version="scenario-v1",
        training_versions={"landsat": "v1"},
        assumptions={"not_a_forecast": True},
        results={
            "status": "completed",
            "mode": "trend_scenario",
            "projections": [
                {"area_id": area.id, "scenario_change_c": 1.2, "uncertainty_c": 0.6, "label": "scenario_projection_not_forecast"}
            ],
        },
    )
    db.add_all([area, forecast])
    db.commit()

    response = client.get("/api/map/features?layer=future-risk")
    assert response.status_code == 200
    properties = response.json()["features"][0]["properties"]
    assert properties["value"] == 1.2
    assert properties["quality_status"] == "scenario_projection"
    assert properties["scenario_horizon_years"] == 3
    assert properties["scenario_uncertainty_c"] == 0.6


def test_forecast_guardrail_returns_versioned_scenario_metadata(client):
    response = client.post("/api/forecast/run", json={"horizon_years": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["mode"] == "scenario_only"
    assert body["model_version"] == "scenario-v2"
    assert body["method"] == "guardrail-validation-v2"
    assert body["assumptions"]["spatial_validation"] == "deterministic 20 percent district holdout"
    assert body["metrics"]["validation_passed"] is False


def test_forecast_publishes_only_after_temporal_spatial_and_baseline_validation(client, db):
    source = db.scalar(select(DataSource).where(DataSource.dataset_id.like("LANDSAT/%")).limit(1))
    version = DatasetVersion(
        source_id=source.id,
        source_asset_id="forecast-validation-history",
        source_version="history-v1",
        observed_at=datetime(2025, 4, 15, tzinfo=timezone.utc),
        status="published",
        quality_status="valid",
        layer_version=1,
    )
    db.add(version)
    db.flush()
    for area_index in range(15):
        area_id = "forecast-district-%02d" % area_index
        db.add(
            GeographicArea(
                id=area_id,
                name_th="เขตทดสอบ %02d" % area_index,
                name_en="Forecast district %02d" % area_index,
                geometry={"type": "Point", "coordinates": [100.4 + area_index * 0.01, 13.7]},
                source_name="validated test fixture",
            )
        )
        for year_index, year in enumerate(range(2018, 2026)):
            db.add(
                TemperatureObservation(
                    area_id=area_id,
                    dataset_version_id=version.id,
                    observed_at=datetime(year, 4, 15, tzinfo=timezone.utc),
                    lst_c_mean=30.0 + area_index * 0.05 + year_index * 0.3,
                    lst_c_p90=32.0 + area_index * 0.05 + year_index * 0.3,
                    coverage_fraction=0.9,
                    uncertainty_c=0.2,
                )
            )
    db.commit()

    response = client.post("/api/forecast/run", json={"horizon_years": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["mode"] == "trend_scenario"
    assert body["method"] == "validated-seasonal-trend-scenario-v2"
    assert body["metrics"]["validation_passed"] is True
    assert body["metrics"]["temporal_holdout_samples"] == 30
    assert body["metrics"]["spatial_holdout_samples"] == 21
    assert body["metrics"]["temporal_model_mae_c"] < body["metrics"]["temporal_baseline_mae_c"]
    assert body["metrics"]["spatial_model_mae_c"] < body["metrics"]["spatial_baseline_mae_c"]
    assert len(body["projections"]) == 15
    assert body["projections"][0]["label"] == "validated_historical_trend_scenario_not_forecast"
    assert 1.4 <= body["projections"][0]["scenario_change_c"] <= 1.6

    stored = db.get(ForecastRun, body["run_id"])
    assert stored.metrics["validation_passed"] is True


def test_saved_optimization_can_be_retrieved_and_exported(client, db):
    area = GeographicArea(
        id="report-area",
        name_th="พื้นที่รายงาน",
        name_en="Report area",
        geometry={"type": "Point", "coordinates": [100.5, 13.75]},
        source_name="test",
    )
    candidate = CandidateIntervention(
        id="report-candidate",
        area_id=area.id,
        name_th="<script>alert('unsafe')</script>",
        intervention_type="tree_planting",
        geometry={"type": "Point", "coordinates": [100.5, 13.75]},
        estimated_cost_thb=500_000,
        annual_maintenance_thb=25_000,
        estimated_beneficiaries=1_250,
        priority_score=82,
        benefit_score=0.7,
        evidence={"source": "approved-cost-study-v1"},
        uncertainty={"level": "medium"},
    )
    run = OptimizationRun(
        budget_thb=1_000_000,
        allocated_thb=500_000,
        objective="balanced",
        objective_weights={"beneficiaries": 0.5, "priority": 0.5},
        objective_value=91.2,
        solver="OR-Tools CP-SAT",
        input_versions={"worldpop/global": {"version_id": "v1", "layer_version": 1}},
    )
    db.add_all([area, candidate, run])
    db.flush()
    db.add(
        OptimizationAllocation(
            run_id=run.id,
            candidate_id=candidate.id,
            rank=1,
            objective_contribution=91.2,
            explanation={"traceable": True},
        )
    )
    db.commit()

    detail = client.get(f"/api/optimization/runs/{run.id}")
    assert detail.status_code == 200
    assert detail.json()["selected"][0]["evidence"]["source"] == "approved-cost-study-v1"
    assert detail.json()["is_outdated"] is False

    comparison = client.post(
        "/api/scenarios/compare",
        json={"budget_thb": 1_000_000, "enabled_types": ["tree_planting"]},
    )
    assert comparison.status_code == 200
    assert comparison.json()["eligibility_consistent"] is True
    assert len(comparison.json()["scenarios"]) == 3
    assert all(item["allocations"][0]["geometry"]["type"] == "Point" for item in comparison.json()["scenarios"])
    assert all(item["covered_area_ids"] == [area.id] for item in comparison.json()["scenarios"])

    report = client.get(f"/api/optimization/runs/{run.id}/report")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/html")
    assert "attachment;" in report.headers["content-disposition"]
    assert report.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in report.headers["content-security-policy"]
    assert "รายงานพอร์ตการลงทุนลดความร้อน" in report.text
    assert "&lt;script&gt;" in report.text
    assert "<script>alert('unsafe')</script>" not in report.text


def test_missing_optimization_report_returns_not_found(client):
    assert client.get("/api/optimization/runs/not-found/report").status_code == 404


def test_saved_scenario_preserves_comparison_versions_and_exports_safe_report(client, db):
    area = GeographicArea(
        id="scenario-area",
        name_th="พื้นที่ฉากทัศน์",
        name_en="Scenario area",
        geometry={"type": "Point", "coordinates": [100.57, 13.73]},
        source_name="test",
    )
    candidate = CandidateIntervention(
        id="scenario-candidate",
        area_id=area.id,
        name_th="จุดพักคลายร้อน",
        intervention_type="cooling_rest_area",
        geometry={"type": "Point", "coordinates": [100.57, 13.73]},
        estimated_cost_thb=750_000,
        estimated_beneficiaries=900,
        priority_score=84,
        benefit_score=0.75,
        feasibility_status="eligible",
        evidence={"source": "approved-pilot-study"},
        uncertainty={"level": "medium"},
    )
    db.add_all([area, candidate])
    db.commit()

    saved = client.post(
        "/api/scenarios/save",
        json={
            "name": "<script>alert('scenario')</script>",
            "budget_thb": 1_000_000,
            "enabled_types": ["cooling_rest_area"],
        },
    )
    assert saved.status_code == 201
    body = saved.json()
    assert len(body["comparison"]["scenarios"]) == 3
    assert body["comparison"]["eligibility_consistent"] is True
    assert body["is_outdated"] is False

    listing = client.get("/api/scenarios")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == body["id"]

    source = db.scalar(select(DataSource).where(DataSource.dataset_id == "worldpop/global"))
    db.add(
        DatasetVersion(
            source_id=source.id,
            source_asset_id="worldpop:new-version",
            source_version="2026",
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            published_at=datetime.now(timezone.utc),
            status="published",
            quality_status="valid",
            layer_version=1,
            geographic_extent={"type": "Point", "coordinates": [100.57, 13.73]},
        )
    )
    db.commit()

    detail = client.get(f"/api/scenarios/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["is_outdated"] is True

    report = client.get(f"/api/scenarios/{body['id']}/report")
    assert report.status_code == 200
    assert report.headers["cache-control"] == "no-store"
    assert "attachment;" in report.headers["content-disposition"]
    assert "default-src 'none'" in report.headers["content-security-policy"]
    assert "ผลเปรียบเทียบอาจล้าสมัย" in report.text
    assert "&lt;script&gt;" in report.text
    assert "<script>alert('scenario')</script>" not in report.text
    assert "approved-pilot-study" in report.text


def test_saved_scenario_rejects_blank_name_and_missing_report(client):
    response = client.post("/api/scenarios/save", json={"name": "   ", "budget_thb": 1_000_000})
    assert response.status_code == 422
    assert client.get("/api/scenarios/not-found/report").status_code == 404


def test_admin_approves_versioned_cost_and_candidate_before_optimization(client, db):
    area = GeographicArea(
        id="approval-area",
        name_th="พื้นที่ตรวจหลักฐาน",
        name_en="Evidence review area",
        geometry={"type": "Point", "coordinates": [100.55, 13.75]},
        source_name="test",
    )
    candidate = CandidateIntervention(
        id="approval-candidate",
        area_id=area.id,
        name_th="พื้นที่ร่มเงาที่รอตรวจ",
        intervention_type="public_shade_structure",
        geometry={"type": "Point", "coordinates": [100.55, 13.75]},
        estimated_cost_thb=None,
        estimated_beneficiaries=None,
        priority_score=88,
        benefit_score=None,
        feasibility_status="insufficient_evidence",
        evidence={"source_dataset_id": "osm/bangkok", "screening_only": True},
        uncertainty={"cost": "unavailable", "benefit": "unavailable"},
    )
    db.add_all([area, candidate])
    db.commit()

    payload = {
        "intervention_type": "public_shade_structure",
        "version": "bma-reference-2026.1",
        "unit_cost_thb": 500_000,
        "annual_maintenance_thb": 25_000,
        "cost_basis": "fixed_per_site",
        "evidence_url": "https://example.org/bma-cost-reference",
        "evidence_title": "เอกสารอ้างอิงราคากลางที่อนุมัติ",
    }
    assert client.post("/api/cost-assumptions", json=payload).status_code == 401
    created = client.post(
        "/api/cost-assumptions",
        json=payload,
        headers={"X-Admin-Key": "test-admin-key", "X-Admin-Actor": "qa-reviewer"},
    )
    assert created.status_code == 201
    assumption = created.json()
    assert assumption["assumptions"]["approved_by"] == "qa-reviewer"
    cost_event = db.scalar(
        select(PipelineEvent).where(PipelineEvent.event_type == "cost_assumption.approved")
    )
    assert cost_event.payload["cost_assumption_id"] == assumption["id"]
    duplicate = client.post(
        "/api/cost-assumptions",
        json=payload,
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert duplicate.status_code == 409

    approval_payload = {
        "cost_assumption_id": assumption["id"],
        "confirm_site_feasibility": True,
        "confirm_land_control": True,
        "confirm_design_accessibility_review": True,
        "feasibility_evidence_url": "https://example.org/site-survey/approval-candidate",
        "feasibility_notes": "ตรวจสิทธิในพื้นที่ การเข้าถึง และข้อจำกัดการออกแบบแล้ว",
        "estimated_beneficiaries": 650,
        "benefit_score": 62,
        "benefit_evidence_url": "https://example.org/benefit-study",
    }
    approved = client.post(
        "/api/candidates/approval-candidate/approve",
        json=approval_payload,
        headers={"X-Admin-Key": "test-admin-key", "X-Admin-Actor": "urban-climate-lead"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["feasibility_status"] == "eligible"
    assert body["estimated_cost_thb"] == 500_000
    assert body["annual_maintenance_thb"] == 25_000
    assert body["evidence"]["approved_by"] == "urban-climate-lead"

    optimized = client.post(
        "/api/optimization/run",
        json={"budget_thb": 500_000, "candidate_ids": ["approval-candidate"]},
    )
    assert optimized.status_code == 200
    assert optimized.json()["selected"][0]["id"] == "approval-candidate"
    assert db.scalar(
        select(PipelineEvent).where(PipelineEvent.event_type == "candidate.approved")
    ) is not None


def test_candidate_approval_requires_benefit_evidence_and_matching_cost_type(client, db):
    area = GeographicArea(
        id="approval-validation-area",
        name_th="พื้นที่ตรวจเงื่อนไข",
        name_en="Approval validation area",
        geometry={"type": "Point", "coordinates": [100.56, 13.76]},
    )
    candidate = CandidateIntervention(
        id="approval-validation-candidate",
        area_id=area.id,
        name_th="พื้นที่ปลูกต้นไม้",
        intervention_type="tree_planting",
        geometry={"type": "Point", "coordinates": [100.56, 13.76]},
        estimated_cost_thb=None,
        priority_score=70,
        feasibility_status="insufficient_evidence",
        evidence={"source_dataset_id": "osm/bangkok"},
    )
    db.add_all([area, candidate])
    db.commit()
    cost = client.post(
        "/api/cost-assumptions",
        json={
            "intervention_type": "cooling_rest_area",
            "version": "mismatch-1",
            "unit_cost_thb": 300_000,
            "evidence_url": "https://example.org/cost",
            "evidence_title": "หลักฐานต้นทุนจุดพักคลายร้อน",
        },
        headers={"X-Admin-Key": "test-admin-key"},
    ).json()
    no_benefit_source = client.post(
        "/api/candidates/approval-validation-candidate/approve",
        json={
            "cost_assumption_id": cost["id"],
            "confirm_site_feasibility": True,
            "confirm_land_control": True,
            "confirm_design_accessibility_review": True,
            "feasibility_evidence_url": "https://example.org/site",
            "feasibility_notes": "ตรวจสอบพื้นที่จริงครบตามรายการแล้ว",
            "estimated_beneficiaries": 100,
        },
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert no_benefit_source.status_code == 422
    mismatch = client.post(
        "/api/candidates/approval-validation-candidate/approve",
        json={
            "cost_assumption_id": cost["id"],
            "confirm_site_feasibility": True,
            "confirm_land_control": True,
            "confirm_design_accessibility_review": True,
            "feasibility_evidence_url": "https://example.org/site",
            "feasibility_notes": "ตรวจสอบพื้นที่จริงครบตามรายการแล้ว",
        },
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert mismatch.status_code == 422


def test_production_rejects_known_default_admin_key(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_api_key", "local-admin-key-change-me")
    response = client.post(
        "/api/cost-assumptions",
        json={
            "intervention_type": "tree_planting",
            "version": "blocked-default",
            "unit_cost_thb": 100_000,
            "evidence_url": "https://example.org/cost",
            "evidence_title": "หลักฐานต้นทุน",
        },
        headers={"X-Admin-Key": "local-admin-key-change-me"},
    )
    assert response.status_code == 503


def test_oidc_admin_requires_role_and_uses_verified_identity(client, monkeypatch):
    from app.core.config import settings
    from app.services.auth import oidc_verifier

    monkeypatch.setattr(settings, "admin_auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_admin_roles", ["heatshift.admin"])
    monkeypatch.setattr(
        oidc_verifier,
        "verify",
        lambda token: {
            "sub": "identity-42",
            "preferred_username": "climate.admin@example.org",
            "roles": ["heatshift.admin"],
        },
    )
    payload = {
        "intervention_type": "tree_planting",
        "version": "oidc-approved-v1",
        "unit_cost_thb": 100_000,
        "evidence_url": "https://example.org/cost",
        "evidence_title": "หลักฐานต้นทุน",
    }
    created = client.post(
        "/api/cost-assumptions",
        json=payload,
        headers={"Authorization": "Bearer verified-token"},
    )
    assert created.status_code == 201
    assert created.json()["assumptions"]["approved_by"] == "climate.admin@example.org"
    identity = client.get("/api/admin/identity", headers={"Authorization": "Bearer verified-token"})
    assert identity.status_code == 200
    assert identity.json() == {"actor": "climate.admin@example.org", "authenticated": True}
    assert identity.headers["cache-control"] == "no-store"
    assert client.get("/api/admin/identity").status_code == 401

    monkeypatch.setattr(
        oidc_verifier,
        "verify",
        lambda token: {"sub": "viewer-1", "roles": ["heatshift.viewer"]},
    )
    denied = client.post(
        "/api/cost-assumptions",
        json={**payload, "version": "oidc-denied-v1"},
        headers={"Authorization": "Bearer viewer-token"},
    )
    assert denied.status_code == 403
    assert client.get("/api/admin/identity", headers={"Authorization": "Bearer viewer-token"}).status_code == 403


def test_production_refuses_local_admin_mode_even_with_custom_key(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_auth_mode", "local_key")
    monkeypatch.setattr(settings, "admin_api_key", "a-strong-but-shared-key")
    response = client.post(
        "/api/cost-assumptions",
        json={
            "intervention_type": "tree_planting",
            "version": "production-local-blocked",
            "unit_cost_thb": 100_000,
            "evidence_url": "https://example.org/cost",
            "evidence_title": "หลักฐานต้นทุน",
        },
        headers={"X-Admin-Key": "a-strong-but-shared-key"},
    )
    assert response.status_code == 503
