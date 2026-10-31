from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.entities import (
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
)


def test_health_and_dataset_status(client):
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/datasets/status")
    assert response.status_code == 200
    assert response.json()["items"]
    assert all("last_source_observation" in item and "last_system_check" in item for item in response.json()["items"])
    assert all(item["dataset_id"] != "local/integration-fixture" for item in response.json()["items"])


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
    response = client.get("/api/map/features?layer=priority&bbox=100.4,13.6,100.7,13.9")
    assert response.status_code == 200
    assert [feature["id"] for feature in response.json()["features"]] == ["inside"]


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
    assert body["model_version"] == "scenario-v1"
    assert body["method"] == "guardrail-insufficient-data"
    assert body["assumptions"]["spatial_holdout_required_for_model"] is True


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
