import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import DataSource, DatasetVersion, RasterAssetMetadata
from app.services.connectors import DynamicWorldConnector, LandsatConnector, SentinelConnector
from app.services.connectors.base import ConnectorError


@dataclass(frozen=True)
class TileSpec:
    dataset_id: str
    connector: Any
    band: str
    minimum: float
    maximum: float
    palette: list[str]
    attribution: str
    legend_unit: str


TILE_SPECS = {
    "temperature": TileSpec(
        "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2",
        LandsatConnector,
        "lst_c",
        20,
        50,
        ["313695", "4575b4", "74add1", "fdae61", "f46d43", "a50026"],
        "USGS Landsat / Google Earth Engine",
        "°C (Land Surface Temperature)",
    ),
    "ndvi": TileSpec(
        "COPERNICUS/S2_SR_HARMONIZED",
        SentinelConnector,
        "ndvi",
        -0.2,
        0.8,
        ["8c510a", "dfc27d", "f6e8c3", "c7eae5", "5ab4ac", "01665e"],
        "Copernicus Sentinel-2 / Google Earth Engine",
        "NDVI",
    ),
    "vegetation": TileSpec(
        "GOOGLE/DYNAMICWORLD/V1",
        DynamicWorldConnector,
        "trees",
        0,
        1,
        ["071016", "1b5e20", "56e39f"],
        "Google Dynamic World / WRI / Google Earth Engine",
        "Tree probability",
    ),
    "built": TileSpec(
        "GOOGLE/DYNAMICWORLD/V1",
        DynamicWorldConnector,
        "built",
        0,
        1,
        ["071016", "4f3a67", "aa8cff"],
        "Google Dynamic World / WRI / Google Earth Engine",
        "Built probability",
    ),
}


class EarthEngineTileService:
    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _latest_version(db: Session, dataset_id: str) -> Optional[DatasetVersion]:
        source = db.scalar(select(DataSource).where(DataSource.dataset_id == dataset_id))
        if source is None:
            return None
        return db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.source_id == source.id, DatasetVersion.status == "published")
            .order_by(desc(DatasetVersion.published_at))
            .limit(1)
        )

    @staticmethod
    def _validated_tile_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "earthengine.googleapis.com":
            raise ConnectorError("Earth Engine returned an unexpected tile host")
        if "{z}" not in value or "{x}" not in value or "{y}" not in value:
            raise ConnectorError("Earth Engine tile URL does not contain XYZ placeholders")
        return value

    def _render_url(self, spec: TileSpec, version: DatasetVersion) -> str:
        connector = spec.connector()
        ee = connector._earth_engine()
        image = ee.Image(version.source_asset_id)
        analysis = connector._analysis(ee, image)
        raster = analysis["image"].select(spec.band)
        west, south, east, north = settings.bangkok_bbox
        raster = raster.clip(ee.Geometry.Rectangle([west, south, east, north]))
        try:
            map_id = raster.getMapId(
                {
                    "min": spec.minimum,
                    "max": spec.maximum,
                    "palette": spec.palette,
                    "format": "png",
                }
            )
            url = map_id["tile_fetcher"].url_format
        except Exception as exc:
            raise ConnectorError("Earth Engine tile descriptor generation failed: %s" % exc) from exc
        return self._validated_tile_url(str(url))

    def describe(self, db: Session, layer: str) -> Dict[str, Any]:
        spec = TILE_SPECS.get(layer)
        if spec is None:
            return {"available": False, "layer": layer, "reason": "Layer does not use a raster tile source"}
        version = self._latest_version(db, spec.dataset_id)
        if version is None:
            return {"available": False, "layer": layer, "reason": "No validated published raster version"}
        metadata = db.scalar(
            select(RasterAssetMetadata)
            .where(RasterAssetMetadata.dataset_version_id == version.id)
            .limit(1)
        )
        if metadata is None:
            return {
                "available": False,
                "layer": layer,
                "reason": "Published version has no validated raster metadata",
                "dataset_version_id": version.id,
            }
        cache_key = "%s:%s" % (layer, version.id)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                tile_url = cached[1]
            else:
                tile_url = ""
        if not tile_url:
            try:
                tile_url = self._render_url(spec, version)
            except ConnectorError as exc:
                return {
                    "available": False,
                    "layer": layer,
                    "reason": str(exc),
                    "dataset_version_id": version.id,
                }
            with self._lock:
                self._cache = {
                    key: value for key, value in self._cache.items() if value[0] > now
                }
                self._cache[cache_key] = (
                    now + max(60, settings.gee_tile_descriptor_ttl_seconds),
                    tile_url,
                )
        return {
            "available": True,
            "layer": layer,
            "tiles": [tile_url],
            "tile_size": 256,
            "min_zoom": 8,
            "max_zoom": 18,
            "bounds": settings.bangkok_bbox,
            "attribution": spec.attribution,
            "dataset_version_id": version.id,
            "source_asset_id": version.source_asset_id,
            "source_version": version.source_version,
            "layer_version": version.layer_version,
            "observed_at": version.observed_at.isoformat() if version.observed_at else None,
            "quality_status": version.quality_status,
            "legend": {
                "min": spec.minimum,
                "max": spec.maximum,
                "palette": spec.palette,
                "unit": spec.legend_unit,
            },
            "delivery": "google-earth-engine-provider-xyz",
        }


earth_engine_tiles = EarthEngineTileService()
