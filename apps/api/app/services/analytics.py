from datetime import timezone
from typing import Any, Dict, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import (
    AreaIndicator,
    CandidateIntervention,
    DataSource,
    DatasetVersion,
    GeographicArea,
    PriorityScore,
    TemperatureObservation,
)
from app.services.priority import calculate_priority


def _latest_indicator(db: Session, area_id: str, key: str) -> Optional[AreaIndicator]:
    return db.scalar(
        select(AreaIndicator)
        .where(
            AreaIndicator.area_id == area_id, AreaIndicator.indicator_key == key,
            AreaIndicator.quality_status.notin_(["synthetic_demo", "demo_only", "test_only"]),
        )
        .order_by(desc(AreaIndicator.observed_at))
        .limit(1)
    )


def _temperature_trend(db: Session, area_id: str) -> Optional[float]:
    rows = db.scalars(
        select(TemperatureObservation)
        .where(TemperatureObservation.area_id == area_id, TemperatureObservation.lst_c_mean.is_not(None))
        .order_by(TemperatureObservation.observed_at)
    ).all()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    first_time = first.observed_at if first.observed_at.tzinfo else first.observed_at.replace(tzinfo=timezone.utc)
    last_time = last.observed_at if last.observed_at.tzinfo else last.observed_at.replace(tzinfo=timezone.utc)
    elapsed_years = (last_time - first_time).total_seconds() / (365.25 * 86400)
    if elapsed_years < 0.25:
        return None
    return (float(last.lst_c_mean) - float(first.lst_c_mean)) / elapsed_years


def _published_versions(db: Session) -> Dict[str, Any]:
    versions: Dict[str, Any] = {}
    for source in db.scalars(select(DataSource)).all():
        version = db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.source_id == source.id, DatasetVersion.status == "published")
            .order_by(desc(DatasetVersion.published_at))
            .limit(1)
        )
        if version:
            versions[source.dataset_id] = {
                "version_id": version.id,
                "source_version": version.source_version,
                "layer_version": version.layer_version,
            }
    return versions


def recalculate_priority_scores(db: Session) -> int:
    """Recalculate explainable scores from the latest published regional inputs."""
    areas = list(db.scalars(select(GeographicArea)).all())
    latest_temperatures = {
        area.id: _latest_indicator(db, area.id, "lst_c_mean")
        for area in areas
    }
    temperature_values = [
        float(row.value)
        for row in latest_temperatures.values()
        if row is not None and row.value is not None
    ]
    city_temperature_mean = sum(temperature_values) / len(temperature_values) if temperature_values else None
    input_versions = _published_versions(db)
    updated = 0

    for area in areas:
        temperature = latest_temperatures[area.id]
        population = _latest_indicator(db, area.id, "population_density")
        tree_cover = _latest_indicator(db, area.id, "tree_cover_fraction")
        ndvi = _latest_indicator(db, area.id, "ndvi")
        built = _latest_indicator(db, area.id, "built_fraction")
        has_input = any(row is not None and row.value is not None for row in (temperature, population, tree_cover, ndvi, built))
        if not has_input:
            continue

        vegetation_deficit: Optional[float] = None
        if tree_cover and tree_cover.value is not None:
            vegetation_deficit = 1 - max(0.0, min(100.0, float(tree_cover.value))) / 100.0
        elif ndvi and ndvi.value is not None:
            vegetation_deficit = 1 - max(0.0, min(1.0, (float(ndvi.value) + 1.0) / 2.0))

        components = {
            "temperature_anomaly": (
                float(temperature.value) - city_temperature_mean
                if temperature and temperature.value is not None and city_temperature_mean is not None
                else None
            ),
            "population_exposure": float(population.value) if population and population.value is not None else None,
            "vegetation_deficit": vegetation_deficit,
            "built_intensity": (
                max(0.0, min(100.0, float(built.value))) / 100.0
                if built and built.value is not None
                else None
            ),
            "historical_change": _temperature_trend(db, area.id),
        }
        result = calculate_priority(components)
        score = PriorityScore(
            area_id=area.id,
            score=result.score,
            components=result.components,
            weights=result.effective_weights,
            missing_inputs=result.missing_inputs,
            input_versions=input_versions,
            formula_version=result.formula_version,
        )
        db.add(score)
        for candidate in db.scalars(
            select(CandidateIntervention).where(
                CandidateIntervention.area_id == area.id,
                CandidateIntervention.active.is_(True),
            )
        ).all():
            candidate.priority_score = result.score
            candidate.dataset_versions = input_versions
        updated += 1
    return updated
