import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SourceAsset:
    source_asset_id: str
    source_version: str
    observed_at: datetime
    checksum: Optional[str]
    geographic_extent: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def with_checksum(
        cls,
        source_asset_id: str,
        source_version: str,
        observed_at: datetime,
        geographic_extent: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> "SourceAsset":
        digest = hashlib.sha256(
            json.dumps(
                {"id": source_asset_id, "version": source_version, "metadata": metadata or {}, "payload": payload or {}},
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return cls(source_asset_id, source_version, observed_at, digest, geographic_extent, metadata or {}, payload or {})


class ConnectorError(RuntimeError):
    pass


class SourceConnector(ABC):
    source_name: str
    dataset_id: str
    layer_key: str

    @abstractmethod
    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        """Return only assets newer than the connector checkpoint where supported."""
        raise NotImplementedError

    def validate(self, asset: SourceAsset) -> Dict[str, Any]:
        errors = []
        if not asset.source_asset_id:
            errors.append("missing source asset id")
        if asset.observed_at is None:
            errors.append("missing source observation time")
        extent_type = asset.geographic_extent.get("type") if asset.geographic_extent else None
        if extent_type not in {"Polygon", "MultiPolygon", "Point"}:
            errors.append("invalid geographic extent")
        return {"passed": not errors, "errors": errors, "warnings": [], "metrics": {}}
