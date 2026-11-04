from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.connectors.base import AreaProcessingRecord, ConnectorError, ProviderRateLimitError, SourceAsset, SourceConnector


BANGKOK_EXTENT = {
    "type": "Polygon",
    "coordinates": [[[100.327, 13.494], [100.938, 13.494], [100.938, 13.955], [100.327, 13.955], [100.327, 13.494]]],
}


class WorldPopConnector(SourceConnector):
    source_name = "WorldPop"
    dataset_id = "worldpop/global"
    layer_key = "population"
    requires_area_processing = True
    processor_version = "worldpop-api-v3-batch-v1"

    def __init__(self) -> None:
        self._quota: Dict[str, Any] = {}
        self._catalog: Dict[str, Any] = {}
        self._processing_meta: Dict[str, Any] = {}

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": settings.worldpop_api_key} if settings.worldpop_api_key else {}

    def _capture_quota(self, response: httpx.Response) -> None:
        mapping = {
            "limit": "X-RateLimit-Limit",
            "remaining": "X-RateLimit-Remaining",
            "reset_at": "X-RateLimit-Reset",
            "retry_after_seconds": "Retry-After",
        }
        captured = {key: response.headers.get(header) for key, header in mapping.items() if response.headers.get(header) is not None}
        if captured:
            self._quota = {
                **self._quota,
                **captured,
                "provider": "WorldPop API v3",
                "status": "rate_limited" if response.status_code == 429 else "reported",
            }

    def quota_status(self) -> Dict[str, Any]:
        return dict(self._quota)

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = "%s/%s" % (settings.worldpop_api_base_url.rstrip("/"), path.lstrip("/"))
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers(),
                timeout=settings.source_request_timeout_seconds,
                **kwargs,
            )
            self._capture_quota(response)
            if response.status_code == 429:
                retry_after_raw = response.headers.get("Retry-After", "60")
                try:
                    retry_after = int(retry_after_raw)
                except ValueError:
                    retry_after = 60
                raise ProviderRateLimitError(
                    "WorldPop rate limit reached; retry_after_seconds=%s"
                    % retry_after,
                    retry_after_seconds=retry_after,
                )
            response.raise_for_status()
            data = response.json()
        except ConnectorError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorError("WorldPop %s request failed: %s" % (path, exc)) from exc
        if not isinstance(data, dict):
            raise ConnectorError("WorldPop %s response is not a JSON object" % path)
        return data

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        catalog = self._request_json("GET", "/catalog")
        self._catalog = catalog
        years = sorted({int(year) for year in catalog.get("years", [])})
        if not years:
            raise ConnectorError("WorldPop catalog has no available population years")
        if settings.worldpop_reference_year is not None:
            year = settings.worldpop_reference_year
            if year not in years:
                raise ConnectorError("Configured WorldPop reference year %s is unavailable" % year)
        else:
            current_year = datetime.now(timezone.utc).year
            eligible = [year for year in years if year <= current_year]
            if not eligible:
                raise ConnectorError("WorldPop catalog has no non-future reference year")
            year = max(eligible)
        release = str(catalog.get("release") or "unknown-release")
        resolution_m = int(catalog.get("resolution_m") or 0)
        version_id = "worldpop:%s:%s:%sm" % (release, year, resolution_m)
        if version_id == latest_asset_id:
            return []
        return [
            SourceAsset.with_checksum(
                version_id,
                "%s:%s" % (release, year),
                datetime(year, 1, 1, tzinfo=timezone.utc),
                BANGKOK_EXTENT,
                metadata={
                    "reference_year": year,
                    "release": release,
                    "resolution_m": resolution_m,
                    "dataset": catalog.get("dataset"),
                    "license": catalog.get("licence"),
                    "citation": catalog.get("citation"),
                    "stac_url": catalog.get("stac"),
                    "data_class": "population_estimate",
                    "observation_semantics": "reference_year_not_realtime_measurement",
                    "processor_version": self.processor_version,
                    "available_years": years,
                },
            )
        ]

    def validate(self, asset: SourceAsset) -> Dict[str, Any]:
        report = super().validate(asset)
        errors = list(report["errors"])
        year = asset.metadata.get("reference_year")
        if not isinstance(year, int) or not 2015 <= year <= 2030:
            errors.append("WorldPop reference year is missing or outside supported range")
        if asset.metadata.get("data_class") != "population_estimate":
            errors.append("WorldPop data class must be population_estimate")
        if not asset.metadata.get("release") or not asset.metadata.get("resolution_m"):
            errors.append("WorldPop release or resolution metadata is missing")
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": report["warnings"],
            "metrics": {
                "reference_year": year,
                "release": asset.metadata.get("release"),
                "resolution_m": asset.metadata.get("resolution_m"),
                "data_class": asset.metadata.get("data_class"),
            },
        }

    def process(self, asset: SourceAsset, areas: List[Dict[str, Any]]) -> List[AreaProcessingRecord]:
        if not areas:
            raise ConnectorError("No geographic areas are available for WorldPop aggregation")
        feature_collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": area["id"],
                    "properties": {"name": area["id"]},
                    "geometry": area["geometry"],
                }
                for area in areas
                if area.get("geometry")
            ],
        }
        if not feature_collection["features"]:
            raise ConnectorError("Geographic areas do not contain usable geometries")
        response = self._request_json(
            "POST",
            "/batch",
            json={
                "features": feature_collection,
                "year": asset.metadata["reference_year"],
                "include": "population",
            },
        )
        response_year = response.get("data_year")
        if response_year != asset.metadata["reference_year"]:
            raise ConnectorError(
                "WorldPop response year %s does not match requested reference year %s"
                % (response_year, asset.metadata["reference_year"])
            )
        response_features = response.get("features", [])
        skipped_ids = [str(feature.get("id", "")) for feature in response_features if feature.get("skipped")]
        self._processing_meta = {
            "requested_areas": len(feature_collection["features"]),
            "returned_areas": len(response_features),
            "skipped_area_ids": skipped_ids,
        }
        records: List[AreaProcessingRecord] = []
        for feature in response_features:
            if feature.get("skipped"):
                continue
            records.append(
                AreaProcessingRecord(
                    area_id=str(feature.get("id", "")),
                    indicators={
                        "population_estimate": feature.get("total_population"),
                        "population_density": feature.get("population_density"),
                        "population_reference_year": response_year,
                    },
                    units={
                        "population_estimate": "people",
                        "population_density": "people/km²",
                        "population_reference_year": "year",
                    },
                    quality_status="model_estimate",
                    metadata={
                        "reference_year": response_year,
                        "area_km2": feature.get("area_km2"),
                        "share_of_total": feature.get("share_of_total"),
                        "rank": feature.get("rank"),
                        "data_source": "WorldPop Global2",
                        "release": asset.metadata.get("release"),
                        "resolution_m": asset.metadata.get("resolution_m"),
                        "processor_version": self.processor_version,
                        "processing_time_ms": response.get("processing_time_ms"),
                        "uncertainty_available": False,
                    },
                )
            )
        return records

    def validate_processed(self, asset: SourceAsset, records: List[AreaProcessingRecord]) -> Dict[str, Any]:
        report = super().validate_processed(asset, records)
        errors = list(report["errors"])
        warnings = list(report["warnings"])
        if self._processing_meta.get("returned_areas") != self._processing_meta.get("requested_areas"):
            errors.append("WorldPop response did not return every requested area")
        if self._processing_meta.get("skipped_area_ids"):
            errors.append(
                "WorldPop skipped area ids: %s"
                % ", ".join(self._processing_meta["skipped_area_ids"])
            )
        total_population = 0.0
        for record in records:
            population = record.indicators.get("population_estimate")
            density = record.indicators.get("population_density")
            area_sq_km = record.metadata.get("area_km2")
            if not isinstance(population, (int, float)) or population < 0:
                errors.append("invalid WorldPop population for %s" % record.area_id)
            else:
                total_population += float(population)
            if not isinstance(density, (int, float)) or density < 0:
                errors.append("invalid WorldPop population density for %s" % record.area_id)
            if not isinstance(area_sq_km, (int, float)) or area_sq_km <= 0:
                errors.append("invalid WorldPop area for %s" % record.area_id)
            if record.metadata.get("reference_year") != asset.metadata.get("reference_year"):
                errors.append("WorldPop reference year mismatch for %s" % record.area_id)
        return {
            **report,
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "metrics": {
                **report.get("metrics", {}),
                **self._processing_meta,
                "reference_year": asset.metadata.get("reference_year"),
                "total_population_across_areas": total_population,
                "quota": self.quota_status(),
            },
        }


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
