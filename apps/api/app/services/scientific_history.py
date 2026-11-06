from sqlalchemy import select

from app.models.entities import DataSource, DatasetVersion, TemperatureObservation
from app.services.versions import LAYER_DATASETS


def scientific_temperature_versions(db):
    versions = db.scalars(select(DatasetVersion).join(DataSource).where(
        DataSource.dataset_id == LAYER_DATASETS["temperature"], DatasetVersion.status == "published",
        DatasetVersion.quality_status.in_(["valid", "verified"]),
    ).order_by(DatasetVersion.id))
    return [version for version in versions if not (version.metadata_json or {}).get("fixture")
            and (version.metadata_json or {}).get("scientific_use") is not False]


def scientific_temperature_query(db, area_ids=None, *, versions=None):
    versions = scientific_temperature_versions(db) if versions is None else versions
    query = select(TemperatureObservation).where(TemperatureObservation.dataset_version_id.in_([v.id for v in versions]))
    if area_ids:
        query = query.where(TemperatureObservation.area_id.in_(area_ids))
    return query
