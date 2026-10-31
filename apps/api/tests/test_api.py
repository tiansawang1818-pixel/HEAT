from app.models.entities import CandidateIntervention, GeographicArea, OptimizationAllocation, OptimizationRun


def test_health_and_dataset_status(client):
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/datasets/status")
    assert response.status_code == 200
    assert response.json()["items"]
    assert all("last_source_observation" in item and "last_system_check" in item for item in response.json()["items"])
    assert all(item["dataset_id"] != "local/integration-fixture" for item in response.json()["items"])


def test_manual_ingestion_requires_admin(client):
    response = client.post("/api/ingestion/run", json={"source": "open-meteo"})
    assert response.status_code == 401


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
