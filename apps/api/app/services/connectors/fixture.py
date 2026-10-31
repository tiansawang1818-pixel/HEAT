import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.connectors.base import ConnectorError, SourceAsset, SourceConnector


class FixtureConnector(SourceConnector):
    source_name = "Integration fixture"
    dataset_id = "local/integration-fixture"
    layer_key = "fixture"

    def __init__(self, path: Path):
        self.path = path

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConnectorError("Invalid fixture: %s" % exc) from exc
        entries = raw.get("assets", [raw]) if isinstance(raw, dict) else raw
        assets = []
        for index, entry in enumerate(entries):
            observed_raw = entry.get("observed_at") or entry.get("last_source_observed_at")
            if not observed_raw:
                raise ConnectorError("Fixture asset is missing observed_at")
            observed_at = datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            asset_id = str(entry.get("source_asset_id") or entry.get("id") or "%s:%d" % (self.path.name, index))
            if asset_id == latest_asset_id:
                continue
            extent = entry.get("geographic_extent") or {
                "type": "Polygon",
                "coordinates": [[[100.327, 13.494], [100.938, 13.494], [100.938, 13.955], [100.327, 13.955], [100.327, 13.494]]],
            }
            assets.append(
                SourceAsset.with_checksum(
                    asset_id,
                    str(entry.get("source_version", "fixture-v1")),
                    observed_at,
                    extent,
                    metadata={**entry.get("metadata", {}), "fixture": True, "scientific_use": False},
                    payload=entry.get("payload", entry.get("indicators", {})),
                )
            )
        return assets
