import hashlib
import io
import json
import math
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.checkpoints import fingerprint
from app.services.connectors.base import (
    AreaProcessingRecord,
    BoundaryProcessingRecord,
    CandidateProcessingRecord,
    ConnectorError,
    ProviderRateLimitError,
    SourceAsset,
    SourceConnector,
)


BANGKOK_EXTENT = {
    "type": "Polygon",
    "coordinates": [[[100.327, 13.494], [100.938, 13.494], [100.938, 13.955], [100.327, 13.955], [100.327, 13.494]]],
}


class BangkokBoundaryConnector(SourceConnector):
    source_name = "Bangkok district boundaries"
    dataset_id = "bma/bangkok-district-boundaries"
    layer_key = "boundaries"
    requires_boundary_processing = True
    processor_version = "bma-shapefile-v1"
    expected_area_count = 50

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        try:
            response = httpx.get(
                settings.bma_boundary_catalog_url,
                params={"id": settings.bma_boundary_dataset_id},
                timeout=settings.source_request_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorError("BMA boundary catalog request failed: %s" % exc) from exc
        if not body.get("success") or not isinstance(body.get("result"), dict):
            raise ConnectorError("BMA boundary catalog response is incomplete")
        package = body["result"]
        resource = next(
            (
                item
                for item in package.get("resources", [])
                if item.get("id") == settings.bma_boundary_resource_id
            ),
            None,
        )
        if resource is None:
            raise ConnectorError("Configured BMA boundary resource is not present in the catalog")
        resource_url = str(resource.get("url") or "")
        last_modified = str(resource.get("last_modified") or resource.get("created") or "")
        if not resource_url or not last_modified:
            raise ConnectorError("BMA boundary resource URL or modification time is missing")
        asset_id = "bma-boundaries:%s:%s" % (resource["id"], last_modified)
        if asset_id == latest_asset_id:
            return []
        return [
            SourceAsset.with_checksum(
                asset_id,
                last_modified,
                self._parse_time(last_modified),
                BANGKOK_EXTENT,
                metadata={
                    "dataset_title": package.get("title"),
                    "dataset_id": package.get("id"),
                    "package_modified_at": package.get("metadata_modified"),
                    "resource_id": resource.get("id"),
                    "resource_name": resource.get("name"),
                    "resource_format": resource.get("format"),
                    "resource_url": resource_url,
                    "resource_last_modified_at": last_modified,
                    "license": package.get("license_title") or "License not specified",
                    "publisher": "Bangkok Metropolitan Administration / data.go.th",
                    "processor_version": self.processor_version,
                    "data_class": "official_administrative_boundary",
                },
            )
        ]

    def validate(self, asset: SourceAsset) -> Dict[str, Any]:
        report = super().validate(asset)
        errors = list(report["errors"])
        resource_url = str(asset.metadata.get("resource_url") or "")
        parsed = urlparse(resource_url)
        if parsed.scheme != "https" or parsed.hostname != "data.bangkok.go.th":
            errors.append("BMA boundary resource URL must use the approved data.bangkok.go.th HTTPS host")
        if str(asset.metadata.get("resource_format", "")).upper() != "ZIP":
            errors.append("BMA boundary resource must be the configured ZIP shapefile")
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": [*report["warnings"], "Provider metadata does not specify a data license"],
            "metrics": {
                "resource_id": asset.metadata.get("resource_id"),
                "resource_last_modified_at": asset.metadata.get("resource_last_modified_at"),
                "publisher": asset.metadata.get("publisher"),
            },
        }

    @staticmethod
    def _zip_member(archive: zipfile.ZipFile, suffix: str) -> bytes:
        matches = [name for name in archive.namelist() if name.lower().endswith(suffix)]
        if len(matches) != 1:
            raise ConnectorError("BMA boundary ZIP must contain exactly one %s file" % suffix)
        info = archive.getinfo(matches[0])
        if info.file_size > settings.bma_boundary_max_download_bytes:
            raise ConnectorError("BMA boundary ZIP member exceeds the configured size limit")
        return archive.read(info)

    def process_boundaries(self, asset: SourceAsset) -> List[BoundaryProcessingRecord]:
        try:
            response = httpx.get(
                asset.metadata["resource_url"],
                timeout=settings.source_request_timeout_seconds,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConnectorError("BMA boundary download failed: %s" % exc) from exc
        if len(response.content) > settings.bma_boundary_max_download_bytes:
            raise ConnectorError("BMA boundary download exceeds the configured size limit")
        checksum = hashlib.sha256(response.content).hexdigest()
        try:
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            shp = self._zip_member(archive, ".shp")
            shx = self._zip_member(archive, ".shx")
            dbf = self._zip_member(archive, ".dbf")
            prj = self._zip_member(archive, ".prj").decode("utf-8")
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise ConnectorError("BMA boundary ZIP is invalid: %s" % exc) from exc

        try:
            import shapefile
            from pyproj import CRS, Transformer
            from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
            from shapely.ops import transform
            from shapely.validation import make_valid

            reader = shapefile.Reader(
                shp=io.BytesIO(shp),
                shx=io.BytesIO(shx),
                dbf=io.BytesIO(dbf),
                encoding="cp874",
            )
            transformer = Transformer.from_crs(CRS.from_wkt(prj), CRS.from_epsg(4326), always_xy=True)
        except Exception as exc:
            raise ConnectorError("BMA boundary shapefile initialization failed: %s" % exc) from exc

        records: List[BoundaryProcessingRecord] = []
        for item in reader.iterShapeRecords():
            properties = {str(key).lower(): value for key, value in item.record.as_dict().items()}
            district_code = str(properties.get("dcode") or "").strip()
            name_th = str(properties.get("dname") or "").strip()
            name_en = str(properties.get("dname_e") or "").strip()
            if not district_code or not name_th or not name_en:
                raise ConnectorError("BMA boundary record is missing district code or name")
            source_geometry = shape(item.shape.__geo_interface__)
            geometry = make_valid(transform(transformer.transform, source_geometry))
            if isinstance(geometry, Polygon):
                geometry = MultiPolygon([geometry])
            elif isinstance(geometry, GeometryCollection):
                polygons = [part for part in geometry.geoms if isinstance(part, Polygon)]
                geometry = MultiPolygon(polygons)
            if not isinstance(geometry, MultiPolygon) or geometry.is_empty:
                raise ConnectorError("BMA boundary geometry is not a usable polygon for district %s" % district_code)
            area_sq_km = float(properties.get("area") or source_geometry.area) / 1_000_000
            centroid = geometry.representative_point()
            records.append(
                BoundaryProcessingRecord(
                    area_id="bma-district-%s" % district_code,
                    district_code=district_code,
                    name_th=name_th,
                    name_en=name_en,
                    geometry=json.loads(json.dumps(mapping(geometry))),
                    centroid=json.loads(json.dumps(mapping(centroid))),
                    area_sq_km=area_sq_km,
                    source_checksum=checksum,
                    metadata={
                        "source_crs": "EPSG:32647",
                        "target_crs": "EPSG:4326",
                        "resource_id": asset.metadata.get("resource_id"),
                        "processor_version": self.processor_version,
                    },
                )
            )
        return records

    def validate_boundaries(
        self, asset: SourceAsset, records: List[BoundaryProcessingRecord]
    ) -> Dict[str, Any]:
        report = super().validate_boundaries(asset, records)
        errors = list(report["errors"])
        if len(records) != self.expected_area_count:
            errors.append("expected %d Bangkok districts but processed %d" % (self.expected_area_count, len(records)))
        codes = [record.district_code for record in records]
        names_th = [record.name_th for record in records]
        if len(codes) != len(set(codes)):
            errors.append("BMA boundary district codes are not unique")
        if len(names_th) != len(set(names_th)):
            errors.append("BMA boundary Thai district names are not unique")
        checksums = {record.source_checksum for record in records}
        if len(checksums) != 1:
            errors.append("BMA boundary records do not share one source checksum")
        for record in records:
            coordinates = record.geometry.get("coordinates", [])
            flattened: List[List[float]] = []

            def visit(value: Any) -> None:
                if isinstance(value, list):
                    if len(value) >= 2 and all(isinstance(part, (int, float)) for part in value[:2]):
                        flattened.append(value)
                    else:
                        for part in value:
                            visit(part)

            visit(coordinates)
            if not flattened or any(not (100.2 <= point[0] <= 101.1 and 13.3 <= point[1] <= 14.2) for point in flattened):
                errors.append("district %s geometry falls outside the Bangkok validation extent" % record.district_code)
            if record.area_sq_km <= 0:
                errors.append("district %s has invalid area" % record.district_code)
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": report["warnings"],
            "metrics": {
                "processed_areas": len(records),
                "unique_district_codes": len(set(codes)),
                "source_checksum": next(iter(checksums), None),
                "source_crs": "EPSG:32647",
                "published_crs": "EPSG:4326",
            },
        }


class WorldPopConnector(SourceConnector):
    source_name = "WorldPop"
    dataset_id = "worldpop/global"
    layer_key = "population"
    requires_area_processing = True
    processor_version = "worldpop-api-v3-batch-v2"

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
            "minute_limit": "X-RateLimit-Minute-Limit",
            "minute_remaining": "X-RateLimit-Minute-Remaining",
            "minute_reset_at": "X-RateLimit-Minute-Reset",
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
        limits = self._request_json("GET", "/health").get("limits", {})
        if not isinstance(limits, dict):
            raise ConnectorError("WorldPop health did not report request limits")
        batches = self._population_batches(areas, asset.metadata["reference_year"], limits)
        scope = fingerprint({"asset": asset.source_asset_id, "areas": sorted(areas, key=lambda a: str(a["id"])), "provider": settings.worldpop_api_base_url})
        responses = []
        resumed = 0
        # Provider documentation requires sequential requests. Keep every original
        # vertex: batching changes transport, never the scientific study boundary.
        for body in batches:
            chunk = fingerprint(body)
            response = self.checkpoint_store.get(scope, chunk) if self.checkpoint_store else None
            cached = response is not None
            if not cached:
                response = self._request_json("POST", "/batch", json=body)
            if response.get("data_year") != asset.metadata["reference_year"]:
                raise ConnectorError("WorldPop response reference year differs from the requested year")
            features = response.get("features")
            requested = {str(f["id"]) for f in body["features"]["features"]}
            if not isinstance(features, list) or any(not isinstance(f, dict) for f in features):
                raise ConnectorError("WorldPop batch features are missing or malformed")
            returned = [str(f.get("id", "")) for f in features]
            if len(returned) != len(requested) or set(returned) != requested:
                raise ConnectorError("WorldPop batch did not return each requested area exactly once")
            cacheable = all(not f.get("skipped") and all(
                not isinstance(f.get(key), bool) and isinstance(f.get(key), (int, float))
                and math.isfinite(f[key]) and (f[key] > 0 if key == "area_km2" else f[key] >= 0)
                for key in ("total_population", "population_density", "area_km2")) for f in features)
            if self.checkpoint_store and not cached and cacheable:
                self.checkpoint_store.put(scope, chunk, response, ttl_seconds=86400)
            resumed += int(cached)
            responses.append(response)
        response_features = [feature for response in responses for feature in response["features"]]
        skipped_ids = [str(feature.get("id", "")) for feature in response_features if feature.get("skipped")]
        self._processing_meta = {
            "requested_areas": len(areas),
            "returned_areas": len(response_features),
            "skipped_area_ids": skipped_ids,
            "request_batches": len(batches),
            "resumed_batches": resumed,
            "geometry_simplified": False,
            "provider_request_limits": {key: limits[key] for key in ("max_vertices", "max_parts", "max_batch_features", "max_body_bytes")},
        }
        records: List[AreaProcessingRecord] = []
        for batch_index, response in enumerate(responses):
            for feature in response["features"]:
                if feature.get("skipped"):
                    continue
                records.append(AreaProcessingRecord(
                    area_id=str(feature.get("id", "")),
                    indicators={
                        "population_estimate": feature.get("total_population"),
                        "population_density": feature.get("population_density"),
                        "population_reference_year": response["data_year"],
                    },
                    units={
                        "population_estimate": "people",
                        "population_density": "people/km²",
                        "population_reference_year": "year",
                    },
                    quality_status="model_estimate",
                    metadata={
                        "reference_year": response["data_year"],
                        "area_km2": feature.get("area_km2"),
                        "provider_batch_share_of_total": feature.get("share_of_total"),
                        "provider_batch_rank": feature.get("rank"),
                        "provider_batch_index": batch_index,
                        "data_source": "WorldPop Global2",
                        "release": asset.metadata.get("release"),
                        "resolution_m": asset.metadata.get("resolution_m"),
                        "processor_version": self.processor_version,
                        "processing_time_ms": response.get("processing_time_ms"),
                        "uncertainty_available": False,
                    },
                ))
        return records

    @staticmethod
    def _population_batches(areas, year, limits):
        def limit(name, cap):
            value = limits.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConnectorError("WorldPop health did not report a valid %s" % name)
            return min(value, cap)

        max_vertices = limit("max_vertices", 20000)
        max_parts = limit("max_parts", 1000)
        max_features = limit("max_batch_features", 1000)
        max_bytes = limit("max_body_bytes", 512 * 1024)
        batches, features, seen = [], [], set()
        vertices, parts = 0, 0

        def body(items):
            return {"features": {"type": "FeatureCollection", "features": items}, "year": year, "include": "population"}

        for area in sorted(areas, key=lambda item: str(item["id"])):
            area_id, geometry = str(area["id"]), area.get("geometry") or {}
            if not area_id or area_id in seen or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
                raise ConnectorError("WorldPop requires unique area IDs and Polygon/MultiPolygon geometries")
            seen.add(area_id)
            polygons = [geometry.get("coordinates", [])] if geometry["type"] == "Polygon" else geometry.get("coordinates", [])
            area_vertices = sum(len(ring) for polygon in polygons for ring in polygon)
            area_parts = sum(len(polygon) for polygon in polygons)
            feature = {"type": "Feature", "id": area_id, "properties": {"name": area_id}, "geometry": geometry}

            def fits(items, vertex_count, part_count):
                return (len(items) <= max_features and vertex_count <= max_vertices and part_count <= max_parts
                        and len(json.dumps(body(items), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()) <= max_bytes)

            if not area_vertices or not fits([feature], area_vertices, area_parts):
                raise ConnectorError("WorldPop area %s exceeds a single request limit; boundary was not simplified" % area_id)
            if features and not fits([*features, feature], vertices + area_vertices, parts + area_parts):
                batches.append(body(features))
                features, vertices, parts = [], 0, 0
            features.append(feature)
            vertices += area_vertices
            parts += area_parts
        if features:
            batches.append(body(features))
        return batches

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
            if isinstance(population, bool) or not isinstance(population, (int, float)) or not math.isfinite(population) or population < 0:
                errors.append("invalid WorldPop population for %s" % record.area_id)
            else:
                total_population += float(population)
            if isinstance(density, bool) or not isinstance(density, (int, float)) or not math.isfinite(density) or density < 0:
                errors.append("invalid WorldPop population density for %s" % record.area_id)
            if isinstance(area_sq_km, bool) or not isinstance(area_sq_km, (int, float)) or not math.isfinite(area_sq_km) or area_sq_km <= 0:
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
    requires_candidate_processing = True
    processor_version = "overpass-bangkok-candidates-v1"

    def __init__(self) -> None:
        self._candidate_elements: List[Dict[str, Any]] = []
        self._processing_meta: Dict[str, Any] = {}

    @staticmethod
    def _raise_for_rate_limit(response: httpx.Response, provider: str) -> None:
        if response.status_code != 429:
            return
        retry_after = response.headers.get("Retry-After", "60")
        try:
            seconds = int(retry_after)
        except ValueError:
            seconds = 60
        raise ProviderRateLimitError("%s rate limit reached" % provider, seconds)

    @staticmethod
    def _element_point(element: Dict[str, Any]) -> Optional[tuple[float, float]]:
        coordinate = element if element.get("type") == "node" else element.get("center", {})
        try:
            lon, lat = float(coordinate["lon"]), float(coordinate["lat"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (100.327 <= lon <= 100.938 and 13.494 <= lat <= 13.955):
            return None
        return lon, lat

    @staticmethod
    def _intervention_type(tags: Dict[str, Any]) -> Optional[str]:
        amenity = tags.get("amenity")
        leisure = tags.get("leisure")
        if leisure == "park":
            return "tree_planting"
        if amenity in {"hospital", "clinic", "community_centre"}:
            return "cooling_rest_area"
        if (
            tags.get("highway") == "bus_stop"
            or tags.get("public_transport") == "platform"
            or amenity in {"marketplace", "school"}
            or leisure == "playground"
            or tags.get("place") == "square"
        ):
            return "public_shade_structure"
        return None

    @staticmethod
    def _candidate_name(tags: Dict[str, Any], osm_type: str, osm_id: int) -> str:
        return str(tags.get("name:th") or tags.get("name") or "จุดสำรวจ OSM %s/%s" % (osm_type, osm_id))[:200]

    def _fetch_candidate_elements(self) -> List[Dict[str, Any]]:
        west, south, east, north = settings.bangkok_bbox
        divisions = max(1, min(settings.overpass_grid_divisions, 4))
        longitude_step = (east - west) / divisions
        latitude_step = (north - south) / divisions
        by_id: Dict[tuple[str, int], Dict[str, Any]] = {}
        total_bytes = 0
        generators = set()
        request_count = 0
        resumed_count = 0
        snapshot_times = []
        for row in range(divisions):
            tile_south = south + latitude_step * row
            tile_north = north if row == divisions - 1 else tile_south + latitude_step
            for column in range(divisions):
                tile_west = west + longitude_step * column
                tile_east = east if column == divisions - 1 else tile_west + longitude_step
                query = """[out:json][timeout:{timeout}][bbox:{south},{west},{north},{east}];
(
  nwr[\"amenity\"~\"^(hospital|clinic|community_centre|marketplace)$\"];
  nwr[\"leisure\"~\"^(park|playground)$\"];
  nwr[\"place\"=\"square\"];
);
out center tags qt;""".format(
                    timeout=settings.overpass_timeout_seconds,
                    south=tile_south,
                    west=tile_west,
                    north=tile_north,
                    east=tile_east,
                )
                chunk = fingerprint({"query": query, "provider": settings.overpass_api_url})
                scope = getattr(self, "_checkpoint_scope", None)
                body = self.checkpoint_store.get(scope, chunk) if self.checkpoint_store and scope else None
                cached = body is not None
                if not cached:
                    try:
                        response = httpx.post(
                            settings.overpass_api_url,
                            data={"data": query},
                            timeout=settings.overpass_timeout_seconds + 15,
                            headers={"User-Agent": "HEAT-SHIFT/0.1 (Bangkok urban heat research)"},
                        )
                        self._raise_for_rate_limit(response, "Overpass API")
                        response.raise_for_status()
                    except ProviderRateLimitError:
                        raise
                    except httpx.HTTPError as exc:
                        raise ConnectorError("Overpass candidate request failed: %s" % exc) from exc
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise ConnectorError("Overpass response is not valid JSON") from exc
                    request_count += 1
                else:
                    resumed_count += 1
                total_bytes += len(json.dumps(body, ensure_ascii=False).encode())
                if total_bytes > settings.overpass_max_response_bytes:
                    raise ConnectorError("Overpass responses exceed the configured cumulative safety limit")
                if not isinstance(body, dict) or body.get("remark"):
                    raise ConnectorError("Overpass reported incomplete query results; previous dataset is preserved")
                elements = body.get("elements")
                if not isinstance(elements, list):
                    raise ConnectorError("Overpass response does not contain an element list")
                snapshot_metadata = body.get("osm3s") or {}
                if not isinstance(snapshot_metadata, dict):
                    raise ConnectorError("Overpass snapshot metadata is invalid")
                reference = snapshot_metadata.get("timestamp_osm_base")
                if reference:
                    try:
                        parsed_time = datetime.fromisoformat(reference.replace("Z", "+00:00"))
                        if parsed_time.tzinfo is None:
                            raise ValueError("missing source timezone")
                        snapshot_times.append(parsed_time.astimezone(timezone.utc))
                    except (TypeError, ValueError):
                        raise ConnectorError("Overpass source snapshot timestamp is invalid")
                if self.checkpoint_store and scope and not cached:
                    self.checkpoint_store.put(scope, chunk, body, ttl_seconds=1800)
                if body.get("generator"):
                    generators.add(str(body["generator"]))
                for element in elements:
                    if element.get("type") in {"node", "way", "relation"} and element.get("id") is not None:
                        by_id[(str(element["type"]), int(element["id"]))] = element
                if len(by_id) > settings.overpass_max_elements:
                    raise ConnectorError("Overpass responses exceed the configured element limit")
        result = list(by_id.values())
        self._processing_meta = {
            "overpass_generators": sorted(generators),
            "overpass_request_count": request_count,
            "resumed_tiles": resumed_count,
            "snapshot_observed_from": min(snapshot_times).isoformat() if len(snapshot_times) == divisions ** 2 else None,
            "snapshot_observed_to": max(snapshot_times).isoformat() if len(snapshot_times) == divisions ** 2 else None,
            "response_bytes": total_bytes,
            "raw_element_count": len(result),
            "processor_version": self.processor_version,
        }
        return result

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        try:
            response = httpx.get(
                settings.osm_replication_state_url,
                timeout=settings.source_request_timeout_seconds,
                follow_redirects=True,
            )
            self._raise_for_rate_limit(response, "OpenStreetMap replication")
            response.raise_for_status()
        except ProviderRateLimitError:
            raise
        except httpx.HTTPError as exc:
            raise ConnectorError("OpenStreetMap replication state request failed: %s" % exc) from exc
        values = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line and not line.lstrip().startswith("#"))
        sequence = values.get("sequenceNumber")
        timestamp = values.get("timestamp", "").replace("\\:", ":")
        if not sequence or not timestamp:
            raise ConnectorError("OSM replication state is incomplete")
        self._checkpoint_scope = fingerprint({"sequence": sequence, "bbox": settings.bangkok_bbox, "grid": settings.overpass_grid_divisions})
        self._candidate_elements = self._fetch_candidate_elements()
        normalized = []
        for element in self._candidate_elements:
            point = self._element_point(element)
            intervention_type = self._intervention_type(element.get("tags", {}))
            if point and intervention_type and element.get("type") in {"node", "way", "relation"} and element.get("id") is not None:
                normalized.append(
                    {
                        "type": element["type"],
                        "id": int(element["id"]),
                        "point": point,
                        "intervention_type": intervention_type,
                        "tags": element.get("tags", {}),
                    }
                )
        snapshot_checksum = hashlib.sha256(
            json.dumps(sorted(normalized, key=lambda item: (item["type"], item["id"])), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        asset_id = "osm-candidate-snapshot:%s" % snapshot_checksum
        if asset_id == latest_asset_id:
            return []
        snapshot_time = self._processing_meta.get("snapshot_observed_from")
        observed = datetime.fromisoformat(snapshot_time or timestamp.replace("Z", "+00:00"))
        return [
            SourceAsset.with_checksum(
                asset_id,
                sequence,
                observed,
                BANGKOK_EXTENT,
                metadata={
                    "replication_sequence": int(sequence),
                    "replication_generated_at": timestamp,
                    "observation_time_kind": "overpass_snapshot" if snapshot_time else "replication_checkpoint",
                    "snapshot_observed_from": snapshot_time,
                    "snapshot_observed_to": self._processing_meta.get("snapshot_observed_to"),
                    "snapshot_checksum": snapshot_checksum,
                    "candidate_element_count": len(normalized),
                    "data_class": "open_data_candidate_locations",
                    "license": "ODbL 1.0",
                    "attribution": "© OpenStreetMap contributors",
                    "attribution_url": "https://www.openstreetmap.org/copyright",
                    "processor_version": self.processor_version,
                    "note": "OSM locations are screening candidates only; suitability, cost and benefit require separate evidence.",
                },
            )
        ]

    def process_candidates(
        self, asset: SourceAsset, areas: List[Dict[str, Any]]
    ) -> List[CandidateProcessingRecord]:
        from shapely.geometry import Point, shape
        from shapely.prepared import prep

        if not self._candidate_elements:
            self._candidate_elements = self._fetch_candidate_elements()
        prepared_areas = [
            (area["id"], prep(shape(area["geometry"])))
            for area in areas
            if area.get("geometry")
        ]
        records: List[CandidateProcessingRecord] = []
        unassigned = 0
        seen = set()
        for element in self._candidate_elements:
            osm_type = element.get("type")
            osm_id = element.get("id")
            tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
            point = self._element_point(element)
            intervention_type = self._intervention_type(tags)
            if not point or not intervention_type or osm_type not in {"node", "way", "relation"} or osm_id is None:
                continue
            source_feature_id = "%s/%s" % (osm_type, osm_id)
            candidate_id = "osm-%s-%s-%s" % (osm_type, osm_id, intervention_type.replace("_", "-"))
            if candidate_id in seen:
                continue
            location = Point(point)
            area_id = next((area_id for area_id, polygon in prepared_areas if polygon.covers(location)), None)
            if area_id is None:
                unassigned += 1
                continue
            seen.add(candidate_id)
            records.append(
                CandidateProcessingRecord(
                    candidate_id=candidate_id,
                    area_id=area_id,
                    name_th=self._candidate_name(tags, osm_type, int(osm_id)),
                    intervention_type=intervention_type,
                    geometry={"type": "Point", "coordinates": [point[0], point[1]]},
                    source_feature_id=source_feature_id,
                    feasibility_constraints={
                        "requires_site_survey": True,
                        "requires_land_control_verification": True,
                        "requires_design_and_accessibility_review": True,
                    },
                    evidence={
                        "source_dataset_id": self.dataset_id,
                        "source_feature_id": source_feature_id,
                        "source_url": "https://www.openstreetmap.org/%s/%s" % (osm_type, osm_id),
                        "source_tags": tags,
                        "license": "ODbL 1.0",
                        "attribution": "© OpenStreetMap contributors",
                        "screening_only": True,
                        "cost_evidence": None,
                        "benefit_evidence": None,
                    },
                    uncertainty={
                        "location": "community-maintained OSM feature; verify on site",
                        "feasibility": "not assessed",
                        "cost": "unavailable",
                        "benefit": "unavailable",
                    },
                )
            )
        self._processing_meta.update({"unassigned_elements": unassigned, "usable_candidates": len(records)})
        return records

    def validate_candidates(
        self, asset: SourceAsset, records: List[CandidateProcessingRecord]
    ) -> Dict[str, Any]:
        report = super().validate_candidates(asset, records)
        errors = list(report["errors"])
        warnings = list(report["warnings"])
        ids = [record.candidate_id for record in records]
        if len(ids) != len(set(ids)):
            errors.append("candidate ids are not unique")
        if any(record.intervention_type not in {"tree_planting", "public_shade_structure", "cooling_rest_area"} for record in records):
            errors.append("unsupported intervention type returned from OSM processing")
        if self._processing_meta.get("unassigned_elements"):
            warnings.append("Some OSM elements could not be assigned to a published Bangkok district")
        return {
            **report,
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "metrics": {**report.get("metrics", {}), **self._processing_meta},
        }
