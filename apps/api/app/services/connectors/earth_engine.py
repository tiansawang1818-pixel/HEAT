from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Type

from app.core.config import settings
from app.services.connectors.base import ConnectorError, SourceAsset, SourceConnector


class EarthEngineConnector(SourceConnector):
    collection_id: str
    collection_ids: Optional[List[str]] = None
    required_bands: List[str] = []
    cloud_property: Optional[str] = None
    max_cloud_percent: float = 65.0
    lookback_days: int = 45
    processing_recipe: Dict[str, Any] = {}

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        try:
            import ee

            ee.Initialize()
        except Exception as exc:
            raise ConnectorError("Google Earth Engine credentials/initialization unavailable: %s" % exc) from exc
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
        raw = collection.sort("system:time_start", False).limit(25).getInfo()
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
                    },
                )
            )
        return sorted(assets, key=lambda item: item.observed_at)


class LandsatConnector(EarthEngineConnector):
    source_name = "Landsat 8/9"
    dataset_id = "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2"
    collection_id = "LANDSAT/LC09/C02/T1_L2"
    collection_ids = ["LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"]
    layer_key = "temperature"
    required_bands = ["ST_B10", "QA_PIXEL"]
    cloud_property = "CLOUD_COVER"
    processing_recipe = {
        "cloud_mask": "QA_PIXEL bits 1, 2, 3, 4 clear; QA_RADSAT == 0",
        "surface_temperature_kelvin": "ST_B10 * 0.00341802 + 149.0",
        "surface_temperature_celsius": "kelvin - 273.15",
        "publication_guard": "valid_pixel_fraction and range validation required",
    }


class SentinelConnector(EarthEngineConnector):
    source_name = "Sentinel-2"
    dataset_id = "COPERNICUS/S2_SR_HARMONIZED"
    collection_id = "COPERNICUS/S2_SR_HARMONIZED"
    layer_key = "ndvi"
    required_bands = ["B4", "B8", "SCL"]
    cloud_property = "CLOUDY_PIXEL_PERCENTAGE"
    lookback_days = 20
    processing_recipe = {
        "cloud_mask": "SCL excludes 3, 8, 9, 10, 11",
        "reflectance_scale": 0.0001,
        "ndvi": "(B8 - B4) / (B8 + B4)",
    }


class DynamicWorldConnector(EarthEngineConnector):
    source_name = "Google Dynamic World"
    dataset_id = "GOOGLE/DYNAMICWORLD/V1"
    collection_id = "GOOGLE/DYNAMICWORLD/V1"
    layer_key = "landcover"
    required_bands = ["trees", "built", "label"]
    lookback_days = 20
    processing_recipe = {
        "aggregation": "mean class probabilities and modal label over valid coverage",
        "vegetation_classes": ["trees", "grass", "shrub_and_scrub", "crops"],
        "confidence": "preserve maximum class probability and per-class means",
    }
