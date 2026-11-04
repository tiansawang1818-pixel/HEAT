from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.core.config import settings
from app.services.connectors.base import AreaProcessingRecord, ConnectorError, SourceAsset, SourceConnector


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
    processor_version = "gee-zonal-v1"
    requires_area_processing = True

    def _earth_engine(self):
        try:
            import ee

            if settings.gee_project:
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
        features = [
            ee.Feature(ee.Geometry(area["geometry"]), {"area_id": area["id"]})
            for area in areas
            if area.get("geometry")
        ]
        if not features:
            raise ConnectorError("Geographic areas do not contain usable geometries")
        pixel_area = ee.Image.pixelArea()

        def aggregate(feature):
            geometry = feature.geometry()
            stats = data_image.reduceRegion(
                reducer=reducer,
                geometry=geometry,
                scale=self.analysis_scale_m,
                maxPixels=50_000_000,
                tileScale=4,
            )
            valid_area = pixel_area.updateMask(mask).reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=geometry,
                scale=self.analysis_scale_m,
                maxPixels=50_000_000,
                tileScale=4,
            ).get("area")
            coverage = ee.Number(ee.Algorithms.If(valid_area, valid_area, 0)).divide(geometry.area(maxError=1)).min(1)
            return ee.Feature(None, stats.set("area_id", feature.get("area_id")).set("coverage_fraction", coverage))

        try:
            response = ee.FeatureCollection(features).map(aggregate).getInfo()
        except Exception as exc:
            raise ConnectorError("Earth Engine regional aggregation failed: %s" % exc) from exc

        records: List[AreaProcessingRecord] = []
        for feature in response.get("features", []):
            properties = feature.get("properties", {})
            coverage = self._safe_number(properties.get("coverage_fraction")) or 0.0
            if coverage < self.min_publishable_coverage:
                continue
            records.append(self._record_from_properties(properties))
        return records

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
