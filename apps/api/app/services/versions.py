from typing import Any, Dict, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import DataSource, DatasetVersion


LAYER_DATASETS = {
    "temperature": "LANDSAT/LC08_C02_T1_L2+LANDSAT/LC09_C02_T1_L2",
    "ndvi": "COPERNICUS/S2_SR_HARMONIZED",
    "vegetation": "GOOGLE/DYNAMICWORLD/V1",
    "built": "GOOGLE/DYNAMICWORLD/V1",
    "population": "worldpop/global",
}

INDICATOR_DATASETS = {
    "lst_c_mean": LAYER_DATASETS["temperature"], "lst_c_p90": LAYER_DATASETS["temperature"],
    "ndvi": LAYER_DATASETS["ndvi"], "tree_cover_fraction": LAYER_DATASETS["vegetation"],
    "built_fraction": LAYER_DATASETS["built"], "population_density": LAYER_DATASETS["population"],
    "population_estimate": LAYER_DATASETS["population"], "population_reference_year": LAYER_DATASETS["population"],
}


def published_version(db: Session, dataset_id: str, version_id: Optional[str] = None) -> Optional[DatasetVersion]:
    query = select(DatasetVersion).join(DataSource).where(
        DataSource.dataset_id == dataset_id, DatasetVersion.status == "published",
    )
    if version_id:
        query = query.where(DatasetVersion.id == version_id)
    return db.scalar(query.order_by(
        desc(DatasetVersion.observed_at).nullslast(),
        desc(DatasetVersion.published_at).nullslast(), DatasetVersion.id,
    ).limit(1))


def current_versions(db: Session) -> Dict[str, Any]:
    """Observation time chooses the active snapshot; late backfills stay historical."""
    result = {}
    for source in db.scalars(select(DataSource)).all():
        latest = published_version(db, source.dataset_id)
        if latest:
            result[source.dataset_id] = {
                "version_id": latest.id, "source_version": latest.source_version,
                "layer_version": latest.layer_version,
            }
    return result
