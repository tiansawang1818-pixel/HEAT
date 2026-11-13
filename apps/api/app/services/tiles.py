import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import DatasetVersion, RasterAssetMetadata
from app.services.connectors import DynamicWorldConnector, LandsatConnector, SentinelConnector
from app.services.connectors.base import ConnectorError
from app.services.versions import indicator_version_pool, published_version


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


logger = logging.getLogger(__name__)
_SHARED_TILE_PREFIX = "heatshift:tiles:"


def _shared_key(spec: "TileSpec", cache_key: str) -> str:
    # The connector recipe is part of the key, so a changed cloud mask or
    # band never reuses a tile URL rendered with the previous recipe.
    recipe = json.dumps(getattr(spec.connector, "processing_recipe", {}), sort_keys=True)
    return _SHARED_TILE_PREFIX + hashlib.sha256((cache_key + "|" + recipe).encode()).hexdigest()


def _shared_get(key: str) -> Optional[str]:
    try:
        import redis
        with redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
            value = client.get(key)
    except Exception as exc:
        logger.warning("Shared tile cache read failed: %s", type(exc).__name__)
        return None
    return value.decode("utf-8", "replace") if value else None


def _shared_set(key: str, url: str, seconds: int) -> None:
    try:
        import redis
        with redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
            client.set(key, url, ex=seconds)
    except Exception as exc:
        logger.warning("Shared tile cache write failed: %s", type(exc).__name__)


# Enough recent scenes that cloud gaps in one are filled by another.
MAX_COMPOSITE_SCENES = 24


class EarthEngineTileService:
    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _latest_version(db: Session, dataset_id: str, version_id: Optional[str] = None) -> Optional[DatasetVersion]:
        return published_version(db, dataset_id, version_id)

    @staticmethod
    def _validated_tile_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "earthengine.googleapis.com":
            raise ConnectorError("Earth Engine returned an unexpected tile host")
        if "{z}" not in value or "{x}" not in value or "{y}" not in value:
            raise ConnectorError("Earth Engine tile URL does not contain XYZ placeholders")
        return value

    def _render_url(self, spec: TileSpec, version: DatasetVersion, composite_assets: Optional[List[str]] = None) -> str:
        try:
            connector = spec.connector()
            ee = connector._earth_engine()
            if composite_assets:
                # Each scene covers part of Bangkok and loses pixels to cloud; mosaic
                # the masked scenes so the newest clear pixel shows everywhere.
                layers = [connector._analysis(ee, ee.Image(asset))["image"].select(spec.band) for asset in composite_assets]
                raster = ee.ImageCollection(layers).mosaic()
            else:
                image = ee.Image(version.source_asset_id)
                analysis = connector._analysis(ee, image)
                raster = analysis["image"].select(spec.band)
            west, south, east, north = settings.bangkok_bbox
            raster = raster.clip(ee.Geometry.Rectangle([west, south, east, north]))
            map_id = raster.getMapId(
                {
                    "min": spec.minimum,
                    "max": spec.maximum,
                    "palette": spec.palette,
                    "format": "png",
                }
            )
            url = map_id["tile_fetcher"].url_format
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError("Earth Engine tile descriptor generation failed: %s" % exc) from exc
        return self._validated_tile_url(str(url))

    def describe(self, db: Session, layer: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        spec = TILE_SPECS.get(layer)
        if spec is None:
            return {"available": False, "layer": layer, "reason": "Layer does not use a raster tile source"}
        version = self._latest_version(db, spec.dataset_id, version_id)
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
        composite: List[str] = []
        if version_id is None:
            pool = db.scalars(select(DatasetVersion).where(
                DatasetVersion.id.in_(indicator_version_pool(db, spec.dataset_id))
            ).order_by(DatasetVersion.observed_at.desc()).limit(MAX_COMPOSITE_SCENES)).all()
            if len(pool) > 1:
                composite = [item.source_asset_id for item in reversed(pool)]  # newest drawn last, on top
        cache_key = "%s:%s:%s" % (layer, version.id, ",".join(composite))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                tile_url = cached[1]
            else:
                tile_url = ""
        ttl = max(60, settings.gee_tile_descriptor_ttl_seconds)
        shared_key = _shared_key(spec, cache_key)
        if not tile_url:
            # The API worker is replaced every few minutes to bound its memory,
            # which emptied this cache; a cold multi-scene composite takes
            # longer than the proxy waits. Workers share rendered URLs in Redis.
            shared = _shared_get(shared_key)
            if shared:
                try:
                    tile_url = self._validated_tile_url(shared)
                except ConnectorError:
                    tile_url = ""
                if tile_url:
                    with self._lock:
                        self._cache[cache_key] = (now + ttl, tile_url)
        if not tile_url:
            try:
                tile_url = self._render_url(spec, version, composite) if composite else self._render_url(spec, version)
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
                self._cache[cache_key] = (now + ttl, tile_url)
            _shared_set(shared_key, tile_url, ttl)
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
            "composite_scene_count": len(composite) or 1,
        }


earth_engine_tiles = EarthEngineTileService()
