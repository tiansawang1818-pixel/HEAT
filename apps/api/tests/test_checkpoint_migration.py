from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.models.entities import DataSource
from app.services.checkpoints import SourceCheckpointStore, fingerprint


def test_existing_database_upgrade_preserves_source_records_and_adds_durable_staging(tmp_path, monkeypatch):
    url = "sqlite+pysqlite:///" + str(tmp_path / "previous-release.db")
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name != "source_processing_checkpoints"])
    with Session(engine) as db:
        source = DataSource(name="Synthetic migration source", provider="Unit test", dataset_id="unit/migration", source_type="test_fixture", check_interval_seconds=60, stale_after_seconds=60)
        db.add(source); db.commit()
        source_id = source.id
    assert not inspect(engine).has_table("source_processing_checkpoints")
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "migrations"))
    monkeypatch.setattr(settings, "database_url", url)
    command.stamp(config, "20261104_0003")
    command.upgrade(config, "head")
    assert inspect(engine).has_table("source_processing_checkpoints")
    with Session(engine) as db:
        assert db.scalar(select(DataSource.id)) == source_id
        store = SourceCheckpointStore(db, source_id, "unit-migration-v1")
        scope, chunk = fingerprint("test-scope"), fingerprint("test-chunk")
        assert store.put(scope, chunk, {"synthetic_staging": True}, 60)
        assert store.get(scope, chunk) == {"synthetic_staging": True}
    engine.dispose()
