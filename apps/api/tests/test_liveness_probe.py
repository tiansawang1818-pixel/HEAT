"""A dependency outage must remain visible without blocking process liveness."""
from app.db.session import get_db
from app.main import app


def test_liveness_does_not_checkout_database_but_readiness_still_rejects_outage(client):
    checkouts = []

    class UnavailableDatabase:
        def execute(self, *args, **kwargs):
            raise OSError("isolated database outage")

    def unavailable_database():
        checkouts.append(True)
        yield UnavailableDatabase()

    app.dependency_overrides[get_db] = unavailable_database
    try:
        live = client.get("/api/health/live")
        assert live.status_code == 200 and live.json()["status"] == "ok"
        assert checkouts == []
        ready = client.get("/api/health/ready")
        assert ready.status_code == 503 and ready.json()["database"] == "unavailable"
        assert len(checkouts) == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
