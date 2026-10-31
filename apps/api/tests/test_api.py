from app.models.entities import GeographicArea


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
