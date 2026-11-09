import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class AreaProcessingRecord:
    area_id: str
    indicators: Dict[str, Optional[float]]
    units: Dict[str, str] = field(default_factory=dict)
    coverage_fraction: Optional[float] = None
    quality_status: str = "valid"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BoundaryProcessingRecord:
    area_id: str
    district_code: str
    name_th: str
    name_en: str
    geometry: Dict[str, Any]
    centroid: Dict[str, Any]
    area_sq_km: float
    source_checksum: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateProcessingRecord:
    candidate_id: str
    area_id: str
    name_th: str
    intervention_type: str
    geometry: Dict[str, Any]
    source_feature_id: str
    feasibility_constraints: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    uncertainty: Dict[str, Any] = field(default_factory=dict)


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


class ProviderRateLimitError(ConnectorError):
    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message)
        self.retry_after_seconds = max(1, min(int(retry_after_seconds), 86400))


class SourceConnector(ABC):
    checkpoint_store: Any = None

    def bind_checkpoints(self, store):
        self.checkpoint_store = store

    source_name: str
    dataset_id: str
    layer_key: str
    requires_area_processing: bool = False
    requires_boundary_processing: bool = False
    requires_candidate_processing: bool = False
    produces_raster: bool = False

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

    def process(self, asset: SourceAsset, areas: Sequence[Dict[str, Any]]) -> List[AreaProcessingRecord]:
        """Produce validated-ready regional records without publishing them.

        Connectors that set ``requires_area_processing`` must override this hook.
        The pipeline persists the returned records in the same transaction as
        layer publication, so incomplete results never become visible.
        """
        return []

    def validate_processed(
        self, asset: SourceAsset, records: Sequence[AreaProcessingRecord]
    ) -> Dict[str, Any]:
        if self.requires_area_processing and not records:
            return {
                "passed": False,
                "errors": ["processing produced no usable geographic records"],
                "warnings": [],
                "metrics": {"processed_areas": 0},
            }
        return {
            "passed": True,
            "errors": [],
            "warnings": [],
            "metrics": {"processed_areas": len(records)},
        }

    def quota_status(self) -> Dict[str, Any]:
        """Return provider quota metadata captured during the latest request."""
        return {}

    def monitor_warning(self) -> Optional[str]:
        """Report an upstream check that completed using a degraded input path."""
        return None

    def raster_metadata(self, asset: SourceAsset) -> Optional[Dict[str, Any]]:
        return None

    def process_boundaries(self, asset: SourceAsset) -> List[BoundaryProcessingRecord]:
        return []

    def validate_boundaries(
        self, asset: SourceAsset, records: Sequence[BoundaryProcessingRecord]
    ) -> Dict[str, Any]:
        if self.requires_boundary_processing and not records:
            return {
                "passed": False,
                "errors": ["processing produced no usable boundary records"],
                "warnings": [],
                "metrics": {"processed_areas": 0},
            }
        return {
            "passed": True,
            "errors": [],
            "warnings": [],
            "metrics": {"processed_areas": len(records)},
        }

    def process_candidates(
        self, asset: SourceAsset, areas: Sequence[Dict[str, Any]]
    ) -> List[CandidateProcessingRecord]:
        return []

    def validate_candidates(
        self, asset: SourceAsset, records: Sequence[CandidateProcessingRecord]
    ) -> Dict[str, Any]:
        if self.requires_candidate_processing and not records:
            return {
                "passed": False,
                "errors": ["processing produced no usable intervention candidates"],
                "warnings": [],
                "metrics": {"processed_candidates": 0},
            }
        return {
            "passed": True,
            "errors": [],
            "warnings": [],
            "metrics": {"processed_candidates": len(records)},
        }
