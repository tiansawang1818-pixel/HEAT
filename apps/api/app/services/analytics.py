from datetime import timezone
from typing import Any, Dict, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, defer

from app.models.entities import (
    AreaIndicator,
    CandidateIntervention,
    GeographicArea,
    PriorityScore,
    TemperatureObservation,
)
from app.services.priority import calculate_priority
from app.services.versions import INDICATOR_DATASETS, current_versions, indicator_version_pool
from app.services.scientific_history import scientific_temperature_query, scientific_temperature_versions


EXCLUDED_QUALITY = ["synthetic_demo", "demo_only", "test_only", "rejected", "invalid"]


def _latest_indicator(db: Session, area_id: str, key: str, versions: Dict[str, Any],
                      pools: Optional[Dict[str, list]] = None) -> Optional[AreaIndicator]:
    query = select(AreaIndicator)
    dataset = INDICATOR_DATASETS.get(key, "")
    active = versions.get(dataset)
    if key in INDICATOR_DATASETS and not active:
        return None
    if active:
        allowed = (pools or {}).get(dataset) or [active["version_id"]]
        query = query.where(AreaIndicator.dataset_version_id.in_(allowed))
    return db.scalar(
        query
        .where(
            AreaIndicator.area_id == area_id, AreaIndicator.indicator_key == key,
            AreaIndicator.value.is_not(None), AreaIndicator.quality_status.notin_(EXCLUDED_QUALITY),
        )
        .order_by(desc(AreaIndicator.observed_at))
        .limit(1)
    )


def _temperature_trend(db: Session, area_id: str, history_versions=None) -> Optional[float]:
    rows = db.scalars(
        scientific_temperature_query(db, versions=history_versions)
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
    return current_versions(db)


def recalculate_priority_scores(db: Session) -> int:
    """Recalculate explainable scores from the latest published regional inputs."""
    areas = list(db.scalars(select(GeographicArea)).all())
    input_versions = _published_versions(db)
    pools = {dataset: indicator_version_pool(db, dataset) for dataset in set(INDICATOR_DATASETS.values())}
    history_versions = scientific_temperature_versions(db)
    latest_temperatures = {
        area.id: _latest_indicator(db, area.id, "lst_c_mean", input_versions, pools)
        for area in areas
    }
    temperature_values = [
        float(row.value)
        for row in latest_temperatures.values()
        if row is not None and row.value is not None
    ]
    city_temperature_mean = sum(temperature_values) / len(temperature_values) if temperature_values else None
    updated = 0

    for area in areas:
        temperature = latest_temperatures[area.id]
        population = _latest_indicator(db, area.id, "population_density", input_versions, pools)
        tree_cover = _latest_indicator(db, area.id, "tree_cover_fraction", input_versions, pools)
        ndvi = _latest_indicator(db, area.id, "ndvi", input_versions, pools)
        built = _latest_indicator(db, area.id, "built_fraction", input_versions, pools)
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
            "historical_change": _temperature_trend(db, area.id, history_versions),
        }
        result = calculate_priority(components)
        score = PriorityScore(
            area_id=area.id,
            score=result.score,
            components=result.components,
            weights=result.effective_weights,
            missing_inputs=result.missing_inputs,
            input_versions={
                **input_versions,
                "_temperature_history": {"dataset_version_ids": [v.id for v in history_versions]},
                # Each district may draw on a different clear scene inside the
                # freshness window; keep exactly which one fed each input.
                "_area_observations": {
                    key: {"version_id": row.dataset_version_id, "observed_at": row.observed_at.isoformat() if row.observed_at else None}
                    for key, row in (("lst_c_mean", temperature), ("population_density", population),
                                     ("tree_cover_fraction", tree_cover), ("ndvi", ndvi), ("built_fraction", built))
                    if row is not None
                },
            },
            formula_version=result.formula_version,
        )
        db.add(score)
        # Candidates mirror their district's score and dataset-level versions.
        # Skip the geometry and rewrite only rows whose values change: a
        # recalculation used to rewrite every candidate's JSON (1,482 rows),
        # which dominated load on the 1 GB managed database.
        for candidate in db.scalars(
            select(CandidateIntervention)
            .options(defer(CandidateIntervention.geometry))
            .where(CandidateIntervention.area_id == area.id, CandidateIntervention.active.is_(True))
        ).all():
            versions = {**(candidate.dataset_versions or {}), **input_versions}
            if candidate.priority_score != result.score:
                candidate.priority_score = result.score
            if versions != (candidate.dataset_versions or {}):
                candidate.dataset_versions = versions
        updated += 1
    return updated


def scores_need_method_refresh(db: Session) -> bool:
    latest = db.scalar(select(PriorityScore).order_by(desc(PriorityScore.calculated_at)).limit(1))
    return latest is not None and "_area_observations" not in (latest.input_versions or {})


def refresh_scores_from_older_method(db: Session) -> int:
    """Recalculate once when the newest scores predate per-district inputs.

    Scores otherwise refresh only after a publication; this lets a method
    change reach the map within the next hourly run instead of waiting for
    the next clear satellite scene.
    """
    if not scores_need_method_refresh(db):
        return 0
    updated = recalculate_priority_scores(db)
    db.commit()
    return updated
