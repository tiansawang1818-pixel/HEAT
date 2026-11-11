from typing import Dict, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import CandidateIntervention, DataSource, GeographicArea
from app.services.geometry import to_db_geometry


SOURCE_DEFINITIONS = [
    ("Bangkok district boundaries", "Bangkok Metropolitan Administration / data.go.th", "bma/bangkok-district-boundaries", "boundary", 7 * 86400, 730 * 86400),
    ("Landsat 8/9", "USGS / Google Earth Engine", "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2", "satellite", 86400, 45 * 86400),
    ("Sentinel-2", "ESA / Google Earth Engine", "COPERNICUS/S2_SR_HARMONIZED", "satellite", 21600, 21 * 86400),
    ("Google Dynamic World", "Google / WRI", "GOOGLE/DYNAMICWORLD/V1", "land_cover", 21600, 365 * 86400),
    ("WorldPop", "WorldPop", "worldpop/global", "population", 30 * 86400, 400 * 86400),
    ("Open-Meteo", "Open-Meteo", "open-meteo/weather-context", "weather", 3600, 3 * 3600),
    ("OpenStreetMap", "OpenStreetMap contributors", "osm/bangkok", "vector", 7 * 86400, 14 * 86400),
    ("Integration fixture", "Local test fixture", "local/integration-fixture", "test_fixture", 0, 86400),
]

# Provider terms for sources whose ingested metadata carries no licence field.
# Landsat: U.S. public domain; Sentinel-2: Copernicus Sentinel data terms;
# Dynamic World: CC BY 4.0; Open-Meteo: CC BY 4.0 (free tier is non-commercial).
LICENSE_URLS: Dict[str, str] = {
    "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2":
        "https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC09_C02_T1_L2#terms-of-use",
    "COPERNICUS/S2_SR_HARMONIZED":
        "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED#terms-of-use",
    "GOOGLE/DYNAMICWORLD/V1":
        "https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1#terms-of-use",
    "open-meteo/weather-context": "https://open-meteo.com/en/license",
}


def ensure_sources(db: Session) -> None:
    existing = {row.dataset_id: row for row in db.scalars(select(DataSource)).all()}
    for name, provider, dataset_id, source_type, interval, stale in SOURCE_DEFINITIONS:
        row = existing.get(dataset_id)
        if row is None:
            db.add(
                DataSource(
                    name=name,
                    provider=provider,
                    dataset_id=dataset_id,
                    source_type=source_type,
                    check_interval_seconds=interval,
                    stale_after_seconds=stale,
                    enabled=source_type != "test_fixture",
                    license_url=LICENSE_URLS.get(dataset_id),
                )
            )
        else:
            if not row.license_url and dataset_id in LICENSE_URLS:
                row.license_url = LICENSE_URLS[dataset_id]
            # Freshness windows are part of the scientific method, owned here.
            if row.stale_after_seconds != stale:
                row.stale_after_seconds = stale
    db.commit()


def seed_demo_data(db: Session) -> None:
    """Opt-in synthetic fixtures. Every record is labelled and must never be shown as observation data."""
    if not settings.demo_mode or db.scalar(select(GeographicArea.id).limit(1)):
        return
    demo_areas = [
        ("demo-pathumwan", "ปทุมวัน (สาธิต)", "Pathum Wan (demo)", 100.533, 13.744),
        ("demo-khlong-toei", "คลองเตย (สาธิต)", "Khlong Toei (demo)", 100.569, 13.716),
        ("demo-bang-khen", "บางเขน (สาธิต)", "Bang Khen (demo)", 100.605, 13.872),
    ]
    for area_id, name_th, name_en, lon, lat in demo_areas:
        d = 0.018
        polygon = {
            "type": "MultiPolygon",
            "coordinates": [[[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]],
        }
        db.add(
            GeographicArea(
                id=area_id,
                name_th=name_th,
                name_en=name_en,
                geometry=to_db_geometry(polygon),
                centroid=to_db_geometry({"type": "Point", "coordinates": [lon, lat]}),
                source_name="Synthetic UX fixture — not an official boundary",
                source_version="demo-v1",
                quality_status="synthetic_demo",
            )
        )
    db.commit()
