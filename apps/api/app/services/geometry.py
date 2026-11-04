import json
from typing import Any, Dict, Optional

from app.core.config import settings


def to_geojson(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize SQLite JSON or PostGIS WKB values to a GeoJSON mapping."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    try:
        from geoalchemy2.shape import to_shape
        from shapely.geometry import mapping

        return json.loads(json.dumps(mapping(to_shape(value))))
    except (ImportError, TypeError, ValueError) as exc:
        raise ValueError("Unsupported database geometry value") from exc


def to_db_geometry(value: Optional[Dict[str, Any]]) -> Any:
    """Convert GeoJSON to a PostGIS bind value while keeping SQLite test data simple."""
    if value is None or not settings.database_url.startswith("postgresql"):
        return value
    from geoalchemy2.shape import from_shape
    from shapely.geometry import shape

    return from_shape(shape(value), srid=4326)
