from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.connectors.base import ConnectorError, SourceAsset, SourceConnector


BANGKOK_EXTENT = {
    "type": "Polygon",
    "coordinates": [[[100.327, 13.494], [100.938, 13.494], [100.938, 13.955], [100.327, 13.955], [100.327, 13.494]]],
}


class WorldPopConnector(SourceConnector):
    source_name = "WorldPop"
    dataset_id = "worldpop/global"
    layer_key = "population"

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        try:
            response = httpx.get(settings.worldpop_catalog_url, params={"iso3": "THA"}, timeout=settings.source_request_timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorError("WorldPop catalogue request failed: %s" % exc) from exc
        entries = data.get("data", data.get("results", data if isinstance(data, list) else []))
        candidates = []
        for entry in entries if isinstance(entries, list) else []:
            year = entry.get("year") or entry.get("popyear")
            asset_id = str(entry.get("id") or entry.get("dataset") or entry.get("title") or "")
            if not asset_id or not year:
                continue
            candidates.append((int(year), asset_id, entry))
        if not candidates:
            return []
        year, asset_id, entry = max(candidates, key=lambda item: item[0])
        version_id = "worldpop:%s:%s" % (year, asset_id)
        if version_id == latest_asset_id:
            return []
        return [
            SourceAsset.with_checksum(
                version_id,
                str(year),
                datetime(year, 1, 1, tzinfo=timezone.utc),
                BANGKOK_EXTENT,
                metadata={"reference_year": year, "catalog_record": entry, "data_class": "population_estimate"},
            )
        ]


class OpenStreetMapConnector(SourceConnector):
    source_name = "OpenStreetMap"
    dataset_id = "osm/bangkok"
    layer_key = "urban_form"

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        try:
            response = httpx.get(settings.osm_replication_state_url, timeout=settings.source_request_timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConnectorError("OpenStreetMap replication state request failed: %s" % exc) from exc
        values = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line and not line.lstrip().startswith("#"))
        sequence = values.get("sequenceNumber")
        timestamp = values.get("timestamp", "").replace("\\:", ":")
        if not sequence or not timestamp:
            raise ConnectorError("OSM replication state is incomplete")
        asset_id = "osm-day-sequence:%s" % sequence
        if asset_id == latest_asset_id:
            return []
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return [
            SourceAsset.with_checksum(
                asset_id,
                sequence,
                observed,
                BANGKOK_EXTENT,
                metadata={
                    "replication_sequence": int(sequence),
                    "data_class": "provider_change_checkpoint",
                    "note": "Downstream import applies and spatially filters provider-approved diffs before feature publication.",
                },
            )
        ]
