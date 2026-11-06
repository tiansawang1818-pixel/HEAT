"""Durable, private staging cache for resumable provider requests."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.models.entities import SourceProcessingCheckpoint


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class SourceCheckpointStore:
    def __init__(self, db, source_id, processor_version):
        self.db, self.source_id, self.processor_version = db, source_id, processor_version

    def _query(self, scope, chunk):
        return select(SourceProcessingCheckpoint).where(
            SourceProcessingCheckpoint.source_id == self.source_id,
            SourceProcessingCheckpoint.processor_version == self.processor_version,
            SourceProcessingCheckpoint.scope_key == scope,
            SourceProcessingCheckpoint.chunk_key == chunk,
        )

    def get(self, scope, chunk):
        row = self.db.scalar(self._query(scope, chunk))
        if row is None:
            return None
        expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
        try:
            valid = isinstance(row.payload, dict) and fingerprint(row.payload) == row.checksum
        except (ValueError, TypeError):
            valid = False
        if expires <= datetime.now(timezone.utc) or not valid:
            self.db.delete(row); self.db.commit()
            return None
        return json.loads(canonical(row.payload))

    def put(self, scope, chunk, payload, ttl_seconds):
        try:
            encoded = canonical(payload)
        except (ValueError, TypeError):
            return False
        if not isinstance(payload, dict) or len(encoded) > 4_000_000:
            return False
        now = datetime.now(timezone.utc)
        self.db.execute(delete(SourceProcessingCheckpoint).where(
            SourceProcessingCheckpoint.source_id == self.source_id,
            SourceProcessingCheckpoint.expires_at <= now,
        ))
        row = self.db.scalar(self._query(scope, chunk))
        if row is None:
            row = SourceProcessingCheckpoint(source_id=self.source_id, processor_version=self.processor_version, scope_key=scope, chunk_key=chunk)
            self.db.add(row)
        row.payload, row.checksum = json.loads(encoded), hashlib.sha256(encoded).hexdigest()
        row.captured_at, row.expires_at = now, now + timedelta(seconds=min(86400, max(1, ttl_seconds)))
        self.db.commit()
        return True

    def clear(self):
        self.db.execute(delete(SourceProcessingCheckpoint).where(
            SourceProcessingCheckpoint.source_id == self.source_id,
            SourceProcessingCheckpoint.processor_version == self.processor_version,
        ))
        self.db.commit()
