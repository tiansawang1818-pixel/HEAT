from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.entities import DataSource, SourceProcessingCheckpoint
from app.services.checkpoints import SourceCheckpointStore, fingerprint


def test_expired_or_corrupt_staging_payloads_are_never_reused(db):
    source = db.scalar(select(DataSource))
    store = SourceCheckpointStore(db, source.id, "unit-v1")
    scope, chunk = fingerprint("unit-scope"), fingerprint("unit-chunk")
    assert store.put(scope, chunk, {"private_staging": [1, 2]}, 10)
    row = db.scalar(select(SourceProcessingCheckpoint))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert store.get(scope, chunk) is None
    store.put(scope, chunk, {"private_staging": [1, 2]}, 10)
    row = db.scalar(select(SourceProcessingCheckpoint))
    row.payload = {"private_staging": [999]}
    db.commit()
    assert store.get(scope, chunk) is None


def test_staging_is_scoped_by_source_processor_and_geometry_and_survives_a_new_session(db):
    source = db.scalar(select(DataSource))
    scope, chunk = fingerprint({"geometry": "old"}), fingerprint("unit-chunk")
    store = SourceCheckpointStore(db, source.id, "unit-v1")
    store.put(scope, chunk, {"private_staging": [1, 2]}, 10)
    from sqlalchemy.orm import Session
    with Session(db.get_bind()) as other:
        resumed = SourceCheckpointStore(other, source.id, "unit-v1")
        assert resumed.get(scope, chunk) == {"private_staging": [1, 2]}
        assert resumed.get(fingerprint({"geometry": "changed"}), chunk) is None
        assert SourceCheckpointStore(other, source.id, "unit-v2").get(scope, chunk) is None
        assert SourceCheckpointStore(other, "different-source", "unit-v1").get(scope, chunk) is None
    assert not store.put(scope, chunk, {"value": float("nan")}, 10)
