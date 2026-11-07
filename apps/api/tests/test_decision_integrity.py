"""Synthetic evidence only; no fixture costs or benefits enter production."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.entities import AreaIndicator, CandidateIntervention, DataSource, DatasetVersion, GeographicArea, OptimizationAllocation, OptimizationRun


def seed(db, cost=100_000):
    point = {"type": "Point", "coordinates": [100.5, 13.7]}
    area = GeographicArea(id="integrity-area", name_th="พื้นที่ทดสอบ", name_en="Fixture", geometry=point)
    candidate = CandidateIntervention(id="integrity-site", area_id=area.id, name_th="โครงการทดสอบ", geometry=point,
        intervention_type="tree_planting", estimated_cost_thb=cost, estimated_beneficiaries=1200,
        priority_score=80, benefit_score=50, evidence={"fixture": True, "scientific_use": False},
        feasibility_status="eligible", active=True)
    db.add_all([area, candidate]); db.commit()
    return candidate


def test_empty_candidate_and_type_selections_are_empty_in_all_strategies(client, db):
    seed(db)
    for selection in ({"candidate_ids": []}, {"enabled_types": []}):
        result = client.post("/api/optimization/run", json={"budget_thb": 100_000, **selection}).json()
        assert result["selected"] == [] and result["allocated_thb"] == 0
        comparison = client.post("/api/scenarios/compare", json={"budget_thb": 100_000, **selection}).json()
        assert comparison["eligible_candidate_ids"] == []
        assert all(s["selected_ids"] == [] and s["allocated_thb"] == 0 for s in comparison["scenarios"])


def test_fractional_cost_cannot_exceed_budget_after_rounding(client, db):
    seed(db, cost=100_000.4)
    assert client.post("/api/optimization/run", json={"budget_thb": 100_000}).json()["selected"] == []
    result = client.post("/api/optimization/run", json={"budget_thb": 100_001}).json()
    assert result["allocated_thb"] == 100_001
    assert result["selected"][0]["estimated_cost_thb"] == 100_000.4


def test_saved_optimization_metadata_is_immutable_when_candidate_changes(client, db):
    candidate = seed(db)
    result = client.post("/api/optimization/run", json={"budget_thb": 500_000}).json()
    candidate.name_th, candidate.estimated_cost_thb, candidate.estimated_beneficiaries = "ชื่อปัจจุบัน", 300_000, 9999
    candidate.geometry = {"type": "Point", "coordinates": [100.8, 13.8]}
    db.commit()
    original = client.get("/api/optimization/runs/" + result["run_id"]).json()
    assert original["historical_candidate_snapshot_complete"] is True
    assert original["selected"][0]["name_th"] == "โครงการทดสอบ"
    assert original["selected"][0]["estimated_cost_thb"] == 100_000
    assert original["selected"][0]["geometry"]["coordinates"] == [100.5, 13.7]
    assert original["estimated_beneficiaries"] == 1200


def test_legacy_runs_do_not_invent_historical_metadata_from_live_candidates(client, db):
    candidate = seed(db)
    run = OptimizationRun(budget_thb=500_000, allocated_thb=100_000, objective="balanced", solver="fixture",
                          input_versions={}, objective_weights={})
    db.add(run); db.flush()
    db.add(OptimizationAllocation(run_id=run.id, candidate_id=candidate.id, rank=1, objective_contribution=.5,
                                 explanation={"estimated_cost_thb": 100_000}))
    db.commit()
    result = client.get("/api/optimization/runs/" + run.id).json()
    assert result["historical_candidate_snapshot_complete"] is False
    assert result["selected"][0]["geometry"] is None
    assert result["estimated_beneficiaries"] is None
    report = client.get("/api/optimization/runs/" + run.id + "/report")
    assert report.status_code == 200 and "snapshot" in report.text


def test_scenarios_freeze_evidence_versions_and_detect_new_costs(client, db):
    candidate = seed(db)
    source = db.scalar(select(DataSource).where(DataSource.name == "Landsat 8/9"))
    version = DatasetVersion(source_id=source.id, source_asset_id="synthetic-integrity-temperature", source_version="test",
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc), status="published", quality_status="valid", layer_version=1)
    db.add(version); db.flush()
    db.add(AreaIndicator(area_id=candidate.area_id, dataset_version_id=version.id, indicator_key="lst_c_mean", value=39,
                         observed_at=version.observed_at))
    db.commit()
    result = client.post("/api/scenarios/save", json={"budget_thb": 500_000, "name": "Integrity fixture"}).json()
    frozen = result["comparison"]
    assert all(s["status"] == "completed" and s["selected_ids"] == [candidate.id] for s in frozen["scenarios"])
    assert len(frozen["input_fingerprint"]) == 64
    assert all(s["weights"] == frozen["scenarios"][0]["weights"] for s in frozen["scenarios"])
    candidate.estimated_cost_thb = 200_000
    db.commit()
    current = client.get("/api/scenarios/" + result["id"]).json()
    assert current["is_outdated"] is True
    assert current["comparison"] == frozen
