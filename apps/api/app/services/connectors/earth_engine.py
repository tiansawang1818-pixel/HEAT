import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.config import settings
from app.services.connectors.base import AreaProcessingRecord, ConnectorError, SourceAsset, SourceConnector


@dataclass(frozen=True)
class HistoricalPage:
    assets: List[SourceAsset]
    cursor: Dict[str, Any]
    scanned_count: int
    done: bool


class EarthEngineConnector(SourceConnector):
    collection_id: str
    collection_ids: Optional[List[str]] = None
    required_bands: List[str] = []
    cloud_property: Optional[str] = None
    max_cloud_percent: float = 65.0
    lookback_days: int = 45
    processing_recipe: Dict[str, Any] = {}
    analysis_scale_m: int = 30
    min_publishable_coverage: float = 0.05
    # About 1 m at Bangkok's latitude, an order of magnitude below the 10-30 m
    # analysis pixels. The full boundaries are 3.4 MB of GeoJSON; serialising
    # them into each Earth Engine request exhausted the 768 MiB runtime.
    analysis_geometry_simplify_degrees: float = 0.00001
    processor_version = "gee-zonal-v2"
    requires_area_processing = True
    produces_raster = True

    def discover_historical_page(
        self, start_date: date, end_date: date, cursor: Dict[str, Any],
        page_size: int = 10, window_days: int = 16,
    ) -> HistoricalPage:
        """One bounded catalogue query; end_date is exclusive.

        Keyset pagination stays inside one original collection and time window.
        Keep original image IDs for processing/tiles, including Landsat 8 and 9.
        Advance over unusable band records too, so they cannot stall a cursor.
        """
        if not 1 <= page_size <= 25 or not 1 <= window_days <= 32 or start_date >= end_date:
            raise ValueError("Invalid historical discovery bounds")
        ids = self.collection_ids or [self.collection_id]
        window_start = date.fromisoformat(cursor.get("window_start", start_date.isoformat()))
        collection_index = int(cursor.get("collection_index", 0))
        after_index = cursor.get("after_index")
        if window_start >= end_date:
            return HistoricalPage([], dict(cursor), 0, True)
        if window_start < start_date or not 0 <= collection_index < len(ids):
            raise ValueError("Historical cursor is outside its frozen range")
        window_end = min(end_date, window_start + timedelta(days=window_days))
        ee = self._earth_engine()
        collection_id = ids[collection_index]
        collection = ee.ImageCollection(collection_id).filterBounds(ee.Geometry.Rectangle(settings.bangkok_bbox))
        collection = collection.filterDate(window_start.isoformat(), window_end.isoformat())
        if self.cloud_property:
            collection = collection.filter(ee.Filter.lte(self.cloud_property, self.max_cloud_percent))
        if after_index:
            collection = collection.filter(ee.Filter.gt("system:index", after_index))
        try:
            raw = collection.sort("system:index", True).limit(page_size + 1).getInfo()
        except Exception as exc:
            raise ConnectorError("Earth Engine historical catalogue query failed: %s" % exc) from exc
        features = raw.get("features", [])
        if not isinstance(features, list) or len(features) > page_size + 1:
            raise ConnectorError("Earth Engine returned an invalid historical page")
        assets: List[SourceAsset] = []
        previous_index = after_index or ""
        bbox = settings.bangkok_bbox
        extent = {"type": "Polygon", "coordinates": [[[bbox[0], bbox[1]], [bbox[2], bbox[1]],
                  [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]}
        for feature in features[:page_size]:
            properties = feature.get("properties", {})
            index = properties.get("system:index")
            asset_id = feature.get("id", "")
            if not isinstance(index, str) or index <= previous_index or not asset_id.startswith(collection_id + "/"):
                raise ConnectorError("Historical catalogue page has non-monotonic indices or invalid image IDs")
            previous_index = index
            bands = {band.get("id") for band in feature.get("bands", [])}
            if any(band not in bands for band in self.required_bands):
                continue
            try:
                observed = datetime.fromtimestamp(properties["system:time_start"] / 1000, tz=timezone.utc)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ConnectorError("Historical image is missing a valid observation time") from exc
            if not window_start <= observed.date() < window_end:
                raise ConnectorError("Historical image falls outside the requested observation window")
            assets.append(SourceAsset.with_checksum(asset_id, index, observed, extent, metadata={
                "collection": collection_id, "cloud_percent": properties.get(self.cloud_property) if self.cloud_property else None,
                "required_bands": self.required_bands, "processing_recipe": self.processing_recipe,
                "processor_version": self.processor_version,
            }))
        more_in_collection = len(features) > page_size
        if more_in_collection:
            next_cursor = {"window_start": window_start.isoformat(), "collection_index": collection_index,
                           "after_index": previous_index}
        elif collection_index + 1 < len(ids):
            next_cursor = {"window_start": window_start.isoformat(), "collection_index": collection_index + 1}
        else:
            next_cursor = {"window_start": window_end.isoformat(), "collection_index": 0}
        return HistoricalPage(assets, next_cursor, min(len(features), page_size),
                              date.fromisoformat(next_cursor["window_start"]) >= end_date)

    def raster_metadata(self, asset: SourceAsset) -> Optional[Dict[str, Any]]:
        return {
            "storage_uri": "earthengine://%s" % asset.source_asset_id,
            "media_type": "application/vnd.google-earth-engine.image",
            "crs": "EPSG:4326",
            "bounds": asset.geographic_extent,
            "resolution_m": self.analysis_scale_m,
            "band_metadata": {
                "source_asset_id": asset.source_asset_id,
                "source_checksum": asset.checksum,
                "required_bands": self.required_bands,
                "processing_recipe": self.processing_recipe,
                "processor_version": self.processor_version,
                "delivery": "provider-backed XYZ tiles generated from the validated image expression",
            },
        }

    def _earth_engine(self):
        try:
            import ee

            if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                import google.auth

                # A service account obtained through ADC must be scoped before
                # refresh. The SDK's persistent-login fallback may refresh it first.
                credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/earthengine"])
                ee.Initialize(credentials=credentials, project=settings.gee_project)
            elif settings.gee_project:
                ee.Initialize(project=settings.gee_project)
            else:
                ee.Initialize()
            return ee
        except Exception as exc:
            raise ConnectorError("Google Earth Engine credentials/initialization unavailable: %s" % exc) from exc

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        ee = self._earth_engine()
        bbox = settings.bangkok_bbox
        geometry = ee.Geometry.Rectangle(bbox)
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        ids = self.collection_ids or [self.collection_id]
        collection = ee.ImageCollection(ids[0])
        for collection_id in ids[1:]:
            collection = collection.merge(ee.ImageCollection(collection_id))
        collection = collection.filterBounds(geometry).filterDate(since.isoformat(), datetime.now(timezone.utc).isoformat())
        if self.cloud_property:
            collection = collection.filter(ee.Filter.lte(self.cloud_property, self.max_cloud_percent))
        try:
            raw = collection.sort("system:time_start", False).limit(25).getInfo()
        except Exception as exc:
            raise ConnectorError("Google Earth Engine catalogue query failed: %s" % exc) from exc
        assets: List[SourceAsset] = []
        for feature in raw.get("features", []):
            asset_id = feature.get("id")
            if not asset_id or asset_id == latest_asset_id:
                continue
            properties = feature.get("properties", {})
            band_ids = {band.get("id") for band in feature.get("bands", [])}
            if any(band not in band_ids for band in self.required_bands):
                continue
            observed = datetime.fromtimestamp(properties["system:time_start"] / 1000.0, tz=timezone.utc)
            extent = {
                "type": "Polygon",
                "coordinates": [[[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]],
            }
            assets.append(
                SourceAsset.with_checksum(
                    asset_id,
                    properties.get("system:index", asset_id),
                    observed,
                    extent,
                    metadata={
                        "collection": feature.get("id", "").rsplit("/", 1)[0],
                        "cloud_percent": properties.get(self.cloud_property) if self.cloud_property else None,
                        "required_bands": self.required_bands,
                        "processing_recipe": self.processing_recipe,
                        "processor_version": self.processor_version,
                    },
                )
            )
        return sorted(assets, key=lambda item: item.observed_at)

    def _analysis(self, ee: Any, image: Any) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _safe_number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _record_from_properties(self, properties: Dict[str, Any]) -> AreaProcessingRecord:
        raise NotImplementedError

    def process(self, asset: SourceAsset, areas: Sequence[Dict[str, Any]]) -> List[AreaProcessingRecord]:
        if not areas:
            raise ConnectorError("No geographic areas are available for Earth Engine aggregation")
        ee = self._earth_engine()
        image = ee.Image(asset.source_asset_id)
        analysis = self._analysis(ee, image)
        data_image = analysis["image"]
        reducer = analysis["reducer"]
        mask = analysis["mask"]
        features = {
            area["id"]: ee.Feature(ee.Geometry(area["geometry"]), {"area_id": area["id"]})
            for area in areas
            if area.get("geometry")
        }
        if not features:
            raise ConnectorError("Geographic areas do not contain usable geometries")
        options = {"scale": self.analysis_scale_m, "maxPixelsPerRegion": 50_000_000, "tileScale": 4}

        def coverage_row(feature):
            valid_area = feature.get("area")
            coverage = ee.Number(ee.Algorithms.If(valid_area, valid_area, 0)).divide(
                feature.geometry().area(maxError=1)
            ).min(1)
            return ee.Feature(None, {"area_id": feature.get("area_id"), "coverage_fraction": coverage})

        try:
            # A reduceRegion mapped across districts starts many separate
            # aggregations. Finish one collection reduction before sending the next.
            coverage = ee.Image.pixelArea().updateMask(mask).reduceRegions(
                collection=ee.FeatureCollection(list(features.values())), reducer=ee.Reducer.sum(), **options,
            ).map(coverage_row).getInfo()
            coverage_rows = self._regional_rows(coverage, features)
            valid_coverage = {}
            for area_id, row in coverage_rows.items():
                fraction = self._safe_number(row.get("coverage_fraction"))
                if fraction is None or not 0 <= fraction <= 1:
                    raise ConnectorError("Earth Engine returned an invalid district coverage fraction")
                if fraction >= self.min_publishable_coverage:
                    valid_coverage[area_id] = fraction
            if not valid_coverage:
                return []
            eligible = {area_id: features[area_id] for area_id in valid_coverage}
            statistics = data_image.reduceRegions(
                collection=ee.FeatureCollection(list(eligible.values())), reducer=reducer, **options,
            ).map(lambda feature: ee.Feature(None, feature.toDictionary())).getInfo()
            rows = self._regional_rows(statistics, eligible)
        except Exception as exc:
            raise ConnectorError("Earth Engine regional aggregation failed: %s" % exc) from exc

        records = [self._record_from_properties({**row, "coverage_fraction": valid_coverage[area_id]})
                   for area_id, row in rows.items()]
        for record in records:
            record.metadata["boundary_simplify_degrees"] = self.analysis_geometry_simplify_degrees
        return records

    @staticmethod
    def _regional_rows(response: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        rows = [feature["properties"] for feature in response.get("features", [])]
        by_area = {row["area_id"]: row for row in rows}
        if len(rows) != len(expected) or by_area.keys() != expected.keys():
            raise ConnectorError("Earth Engine did not return exactly one row per requested district")
        return by_area

    def validate_processed(
        self, asset: SourceAsset, records: Sequence[AreaProcessingRecord]
    ) -> Dict[str, Any]:
        base = super().validate_processed(asset, records)
        errors = list(base["errors"])
        area_ids = [record.area_id for record in records]
        if len(area_ids) != len(set(area_ids)):
            errors.append("processing returned duplicate area ids")
        invalid_coverage = [
            record.area_id
            for record in records
            if record.coverage_fraction is None or not 0 <= record.coverage_fraction <= 1
        ]
        if invalid_coverage:
            errors.append("invalid coverage fraction for area(s): %s" % ", ".join(invalid_coverage))
        warnings = []
        limited = [record.area_id for record in records if (record.coverage_fraction or 0) < 0.25]
        if limited:
            warnings.append("limited clear-pixel coverage for %d area(s)" % len(limited))
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "metrics": {
                "processed_areas": len(records),
                "limited_coverage_areas": len(limited),
                "minimum_coverage_fraction": min((record.coverage_fraction or 0) for record in records) if records else 0,
            },
        }


class LandsatConnector(EarthEngineConnector):
    source_name = "Landsat 8/9"
    dataset_id = "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"
    collection_id = "LANDSAT/LC09/C02/T1_L2"
    collection_ids = ["LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"]
    layer_key = "temperature"
    required_bands = ["ST_B10", "QA_PIXEL", "QA_RADSAT"]
    cloud_property = "CLOUD_COVER"
    analysis_scale_m = 30
    processing_recipe = {
        "cloud_mask": "QA_PIXEL bits 1, 2, 3, 4 clear; QA_RADSAT == 0",
        "surface_temperature_kelvin": "ST_B10 * 0.00341802 + 149.0",
        "surface_temperature_celsius": "kelvin - 273.15",
        "publication_guard": "valid_pixel_fraction and range validation required",
    }

    def _analysis(self, ee: Any, image: Any) -> Dict[str, Any]:
        qa_clear = image.select("QA_PIXEL").bitwiseAnd(30).eq(0)
        mask = qa_clear.And(image.select("QA_RADSAT").eq(0))
        temperature = image.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("lst_c").updateMask(mask)
        return {
            "image": temperature,
            "mask": mask,
            "reducer": ee.Reducer.mean().combine(ee.Reducer.percentile([90]), sharedInputs=True),
        }

    def _record_from_properties(self, properties: Dict[str, Any]) -> AreaProcessingRecord:
        mean = self._safe_number(properties.get("lst_c_mean"))
        p90 = self._safe_number(properties.get("lst_c_p90"))
        coverage = self._safe_number(properties.get("coverage_fraction")) or 0.0
        return AreaProcessingRecord(
            area_id=str(properties["area_id"]),
            indicators={"lst_c_mean": mean, "lst_c_p90": p90},
            units={"lst_c_mean": "°C", "lst_c_p90": "°C"},
            coverage_fraction=coverage,
            quality_status="valid" if coverage >= 0.25 else "limited_coverage",
            metadata={"aggregation": "mean_and_p90", "scale_m": self.analysis_scale_m, "processor_version": self.processor_version},
        )

    def validate_processed(self, asset: SourceAsset, records: Sequence[AreaProcessingRecord]) -> Dict[str, Any]:
        report = super().validate_processed(asset, records)
        errors = list(report["errors"])
        for record in records:
            mean = record.indicators.get("lst_c_mean")
            p90 = record.indicators.get("lst_c_p90")
            if mean is None or not -10 <= mean <= 75:
                errors.append("surface temperature mean outside valid range for %s" % record.area_id)
            if p90 is None or not -10 <= p90 <= 80:
                errors.append("surface temperature p90 outside valid range for %s" % record.area_id)
        return {**report, "passed": not errors, "errors": errors}


class SentinelConnector(EarthEngineConnector):
    source_name = "Sentinel-2"
    dataset_id = "COPERNICUS/S2_SR_HARMONIZED"
    collection_id = "COPERNICUS/S2_SR_HARMONIZED"
    layer_key = "ndvi"
    required_bands = ["B4", "B8", "SCL"]
    cloud_property = "CLOUDY_PIXEL_PERCENTAGE"
    lookback_days = 20
    analysis_scale_m = 10
    processing_recipe = {
        "cloud_mask": "SCL excludes 3, 8, 9, 10, 11",
        "reflectance_scale": 0.0001,
        "ndvi": "(B8 - B4) / (B8 + B4)",
    }

    def _analysis(self, ee: Any, image: Any) -> Dict[str, Any]:
        scl = image.select("SCL")
        mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("ndvi").updateMask(mask)
        return {"image": ndvi, "mask": mask, "reducer": ee.Reducer.mean()}

    def _record_from_properties(self, properties: Dict[str, Any]) -> AreaProcessingRecord:
        ndvi = self._safe_number(properties.get("ndvi"))
        coverage = self._safe_number(properties.get("coverage_fraction")) or 0.0
        return AreaProcessingRecord(
            area_id=str(properties["area_id"]),
            indicators={"ndvi": ndvi},
            units={"ndvi": "index"},
            coverage_fraction=coverage,
            quality_status="valid" if coverage >= 0.25 else "limited_coverage",
            metadata={"aggregation": "mean", "scale_m": self.analysis_scale_m, "processor_version": self.processor_version},
        )

    def validate_processed(self, asset: SourceAsset, records: Sequence[AreaProcessingRecord]) -> Dict[str, Any]:
        report = super().validate_processed(asset, records)
        errors = list(report["errors"])
        for record in records:
            value = record.indicators.get("ndvi")
            if value is None or not -1 <= value <= 1:
                errors.append("NDVI outside valid range for %s" % record.area_id)
        return {**report, "passed": not errors, "errors": errors}


class DynamicWorldConnector(EarthEngineConnector):
    source_name = "Google Dynamic World"
    dataset_id = "GOOGLE/DYNAMICWORLD/V1"
    collection_id = "GOOGLE/DYNAMICWORLD/V1"
    layer_key = "landcover"
    required_bands = ["trees", "built", "label"]
    lookback_days = 20
    analysis_scale_m = 10
    probability_bands = ["water", "trees", "grass", "flooded_vegetation", "crops", "shrub_and_scrub", "built", "bare", "snow_and_ice"]
    processing_recipe = {
        "aggregation": "mean class probabilities and modal label over valid coverage",
        "vegetation_classes": ["trees", "grass", "shrub_and_scrub", "crops"],
        "confidence": "preserve maximum class probability and per-class means",
    }

    def _analysis(self, ee: Any, image: Any) -> Dict[str, Any]:
        mask = image.select("label").mask()
        confidence = image.select(self.probability_bands).reduce(ee.Reducer.max()).rename("confidence")
        layers = image.select(["trees", "built"]).addBands(confidence).updateMask(mask)
        return {"image": layers, "mask": mask, "reducer": ee.Reducer.mean()}

    def _record_from_properties(self, properties: Dict[str, Any]) -> AreaProcessingRecord:
        tree_fraction = self._safe_number(properties.get("trees"))
        built_fraction = self._safe_number(properties.get("built"))
        confidence = self._safe_number(properties.get("confidence"))
        coverage = self._safe_number(properties.get("coverage_fraction")) or 0.0
        return AreaProcessingRecord(
            area_id=str(properties["area_id"]),
            indicators={
                "tree_cover_fraction": tree_fraction * 100 if tree_fraction is not None else None,
                "built_fraction": built_fraction * 100 if built_fraction is not None else None,
            },
            units={"tree_cover_fraction": "%", "built_fraction": "%"},
            coverage_fraction=coverage,
            quality_status="valid" if coverage >= 0.25 else "limited_coverage",
            metadata={
                "confidence": confidence,
                "aggregation": "mean_probability",
                "scale_m": self.analysis_scale_m,
                "processor_version": self.processor_version,
            },
        )

    def validate_processed(self, asset: SourceAsset, records: Sequence[AreaProcessingRecord]) -> Dict[str, Any]:
        report = super().validate_processed(asset, records)
        errors = list(report["errors"])
        for record in records:
            for key in ("tree_cover_fraction", "built_fraction"):
                value = record.indicators.get(key)
                if value is None or not 0 <= value <= 100:
                    errors.append("%s outside valid range for %s" % (key, record.area_id))
            confidence = record.metadata.get("confidence")
            if confidence is None or not 0 <= float(confidence) <= 1:
                errors.append("land-cover confidence outside valid range for %s" % record.area_id)
        return {**report, "passed": not errors, "errors": errors}
