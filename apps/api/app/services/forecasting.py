import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.models.entities import TemperatureObservation


SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60
SHRINK_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)


def _decimal_year(value: datetime) -> float:
    return value.timestamp() / SECONDS_PER_YEAR


def _chronological_split(observations: Sequence[TemperatureObservation]):
    test_size = max(2, math.ceil(len(observations) * 0.2))
    return observations[:-test_size], observations[-test_size:]


@dataclass(frozen=True)
class ClimatologyTrendModel:
    """Each area's month-of-year mean plus one city-wide warming rate.

    The rate is the pooled within-(area, month) slope, so seasonal sampling
    and district differences cannot masquerade as a trend. It is multiplied
    by a shrink factor chosen on an inner chronological split of the training
    data only; a factor of 0 reduces the model to the climatology baseline.
    """
    group_means: Dict[Tuple[str, Optional[int]], Tuple[float, float]]
    slope_c_per_year: float

    def predict(self, area_id: str, row: TemperatureObservation) -> Optional[float]:
        key = (area_id, row.observed_at.month)
        mean = self.group_means.get(key) or self.group_means.get((area_id, None))
        if mean is None:
            return None
        mean_year, mean_value = mean
        return mean_value + self.slope_c_per_year * (_decimal_year(row.observed_at) - mean_year)


def _fit_climatology_trend(training: Dict[str, Sequence[TemperatureObservation]], shrink: float) -> ClimatologyTrendModel:
    means: Dict[Tuple[str, Optional[int]], Tuple[float, float]] = {}
    numerator = denominator = 0.0
    for area_id, observations in training.items():
        if not observations:
            continue
        by_month: Dict[int, List[TemperatureObservation]] = {}
        for row in observations:
            by_month.setdefault(row.observed_at.month, []).append(row)
        for month, rows in by_month.items():
            mean_year = _mean(_decimal_year(row.observed_at) for row in rows)
            mean_value = _mean(float(row.lst_c_mean) for row in rows)
            means[(area_id, month)] = (mean_year, mean_value)
            for row in rows:
                offset = _decimal_year(row.observed_at) - mean_year
                numerator += offset * (float(row.lst_c_mean) - mean_value)
                denominator += offset * offset
        means[(area_id, None)] = (
            _mean(_decimal_year(row.observed_at) for row in observations),
            _mean(float(row.lst_c_mean) for row in observations),
        )
    slope = numerator / denominator if denominator > 1e-9 else 0.0
    return ClimatologyTrendModel(means, slope * shrink)


def _mae(model: ClimatologyTrendModel, testing: Dict[str, Sequence[TemperatureObservation]]) -> List[float]:
    errors = []
    for area_id, rows in testing.items():
        for row in rows:
            predicted = model.predict(area_id, row)
            if predicted is not None:
                errors.append(abs(predicted - float(row.lst_c_mean)))
    return errors


def _select_shrink(history: Dict[str, Sequence[TemperatureObservation]]) -> float:
    """Pick the shrink factor on an inner chronological split of ``history``."""
    inner_training, inner_testing = {}, {}
    for area_id, observations in history.items():
        if len(observations) >= 4:
            inner_training[area_id], inner_testing[area_id] = _chronological_split(observations)
    if not inner_testing:
        return 0.0
    scores = {shrink: _mean(_mae(_fit_climatology_trend(inner_training, shrink), inner_testing)) for shrink in SHRINK_GRID}
    return min(SHRINK_GRID, key=lambda shrink: (round(scores[shrink], 9), shrink))


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _eligible_groups(rows: Sequence[TemperatureObservation]) -> Dict[str, List[TemperatureObservation]]:
    grouped: Dict[str, List[TemperatureObservation]] = {}
    for row in rows:
        if row.lst_c_mean is None or not math.isfinite(row.lst_c_mean) or row.coverage_fraction < 0.25:
            continue
        grouped.setdefault(row.area_id, []).append(row)
    return {
        area_id: sorted(observations, key=lambda item: item.observed_at)
        for area_id, observations in grouped.items()
        if len(observations) >= 8
        and len({item.observed_at.year for item in observations}) >= settings.forecast_min_years
    }


def evaluate_temperature_scenario(
    rows: Sequence[TemperatureObservation],
    horizon_years: int,
) -> Dict[str, Any]:
    groups = _eligible_groups(rows)
    available_years = len({row.observed_at.year for observations in groups.values() for row in observations})
    assumptions = {
        "output_classification": "scenario_projection_not_forecast",
        "minimum_valid_coverage_fraction": 0.25,
        "minimum_observations_per_area": 8,
        "minimum_historical_years": settings.forecast_min_years,
        "minimum_eligible_areas": settings.forecast_min_areas,
        "temporal_validation": "chronological final 20 percent holdout per area",
        "spatial_validation": "deterministic 20 percent district holdout",
        "temporal_baseline": "month-of-year mean from training period",
        "spatial_baseline": "no-change from first valid district observation",
        "model": "per-area month-of-year mean plus a pooled city-wide within-month warming rate",
        "trend_shrinkage": "factor from %s chosen on an inner chronological split of training data only" % (SHRINK_GRID,),
        "uncertainty": "empirical 90th percentile absolute holdout error scaled by square root of horizon",
        "exogenous_climate_or_land_use_scenario": "not included",
    }
    if available_years < settings.forecast_min_years or len(groups) < settings.forecast_min_areas:
        return {
            "status": "insufficient_data",
            "mode": "scenario_only",
            "message_th": "ข้อมูลที่ผ่านเกณฑ์ coverage ยังไม่ครอบคลุมเวลาและพื้นที่พอสำหรับฉากทัศน์แนวโน้มที่ผ่าน validation",
            "available_years": available_years,
            "required_years": settings.forecast_min_years,
            "eligible_areas": len(groups),
            "required_areas": settings.forecast_min_areas,
            "projections": [],
            "metrics": {"validation_passed": False, "reason": "minimum_history_or_spatial_coverage_not_met"},
            "assumptions": assumptions,
        }

    # Temporal holdout: the final 20 percent of each area, never seen while
    # fitting or while choosing the shrink factor.
    temporal_training, temporal_testing = {}, {}
    for area_id, observations in groups.items():
        temporal_training[area_id], temporal_testing[area_id] = _chronological_split(observations)
    temporal_shrink = _select_shrink(temporal_training)
    temporal_model = _fit_climatology_trend(temporal_training, temporal_shrink)
    climatology = _fit_climatology_trend(temporal_training, 0.0)
    temporal_model_errors: List[float] = []
    temporal_baseline_errors: List[float] = []
    for area_id, rows in temporal_testing.items():
        for row in rows:
            predicted = temporal_model.predict(area_id, row)
            baseline = climatology.predict(area_id, row)
            if predicted is None or baseline is None:
                continue
            actual = float(row.lst_c_mean)
            temporal_model_errors.append(abs(predicted - actual))
            temporal_baseline_errors.append(abs(baseline - actual))

    # Spatial holdout: districts the model never saw predict their change from
    # their own first observation using city-wide seasonal offsets and rate.
    area_ids = sorted(groups)
    holdout_ids = {area_id for index, area_id in enumerate(area_ids) if index % 5 == 0}
    spatial_history = {area_id: groups[area_id] for area_id in area_ids if area_id not in holdout_ids}
    spatial_model = _fit_climatology_trend(spatial_history, _select_shrink(spatial_history))
    month_offsets: Dict[int, List[float]] = {}
    for (area_id, month), (_, value) in spatial_model.group_means.items():
        if month is not None:
            month_offsets.setdefault(month, []).append(value - spatial_model.group_means[(area_id, None)][1])
    city_offset = {month: _mean(values) for month, values in month_offsets.items()}
    spatial_model_errors: List[float] = []
    spatial_baseline_errors: List[float] = []
    for area_id in sorted(holdout_ids):
        first, *later = groups[area_id]
        for row in later:
            actual_change = float(row.lst_c_mean) - float(first.lst_c_mean)
            predicted_change = (
                city_offset.get(row.observed_at.month, 0.0) - city_offset.get(first.observed_at.month, 0.0)
                + spatial_model.slope_c_per_year * (_decimal_year(row.observed_at) - _decimal_year(first.observed_at))
            )
            spatial_model_errors.append(abs(predicted_change - actual_change))
            spatial_baseline_errors.append(abs(actual_change))

    temporal_model_mae = _mean(temporal_model_errors)
    temporal_baseline_mae = _mean(temporal_baseline_errors)
    spatial_model_mae = _mean(spatial_model_errors)
    spatial_baseline_mae = _mean(spatial_baseline_errors)
    temporal_improvement = (
        (temporal_baseline_mae - temporal_model_mae) / temporal_baseline_mae
        if temporal_baseline_mae > 0 else 0.0
    )
    spatial_improvement = (
        (spatial_baseline_mae - spatial_model_mae) / spatial_baseline_mae
        if spatial_baseline_mae > 0 else 0.0
    )
    combined_errors = temporal_model_errors + spatial_model_errors
    interval_90 = _quantile(combined_errors, 0.9)
    coverage_90 = _mean(1.0 if error <= interval_90 else 0.0 for error in combined_errors)
    validation_passed = (
        len(temporal_model_errors) >= settings.forecast_min_validation_samples
        and len(spatial_model_errors) >= settings.forecast_min_validation_samples
        and temporal_improvement > settings.forecast_min_baseline_improvement
        and spatial_improvement > settings.forecast_min_baseline_improvement
        and coverage_90 >= 0.75
    )
    metrics = {
        "validation_passed": validation_passed,
        "eligible_areas": len(groups),
        "holdout_areas": len(holdout_ids),
        "available_years": available_years,
        "temporal_holdout_samples": len(temporal_model_errors),
        "spatial_holdout_samples": len(spatial_model_errors),
        "temporal_model_mae_c": round(temporal_model_mae, 4),
        "temporal_baseline_mae_c": round(temporal_baseline_mae, 4),
        "temporal_baseline_improvement_fraction": round(temporal_improvement, 4),
        "spatial_model_mae_c": round(spatial_model_mae, 4),
        "spatial_baseline_mae_c": round(spatial_baseline_mae, 4),
        "spatial_baseline_improvement_fraction": round(spatial_improvement, 4),
        "empirical_interval_90_c": round(interval_90, 4),
        "empirical_interval_coverage_fraction": round(coverage_90, 4),
        "temporal_trend_shrink": temporal_shrink,
        "temporal_trend_c_per_year": round(temporal_model.slope_c_per_year, 4),
    }
    if not validation_passed:
        return {
            "status": "insufficient_data",
            "mode": "scenario_only",
            "message_th": "แบบจำลองแนวโน้มยังไม่ผ่าน temporal/spatial holdout และ baseline gates จึงไม่เผยแพร่แผนที่คาดการณ์",
            "available_years": available_years,
            "required_years": settings.forecast_min_years,
            "eligible_areas": len(groups),
            "required_areas": settings.forecast_min_areas,
            "projections": [],
            "metrics": metrics,
            "assumptions": assumptions,
        }

    full_model = _fit_climatology_trend(groups, _select_shrink(groups))
    metrics["projection_trend_c_per_year"] = round(full_model.slope_c_per_year, 4)
    uncertainty = interval_90 * math.sqrt(horizon_years)
    change = full_model.slope_c_per_year * horizon_years
    projections = [
        {
            "area_id": area_id,
            "horizon_years": horizon_years,
            "scenario_change_c": round(change, 2),
            "uncertainty_c": round(uncertainty, 2),
            "baseline_observed_at": observations[-1].observed_at.isoformat(),
            "label": "validated_historical_trend_scenario_not_forecast",
        }
        for area_id, observations in groups.items()
    ]
    return {
        "status": "completed",
        "mode": "trend_scenario",
        "message_th": "ฉากทัศน์แนวโน้มย้อนหลังผ่าน baseline, temporal และ spatial holdout gates แต่ยังไม่ใช่คำพยากรณ์สภาพภูมิอากาศ",
        "available_years": available_years,
        "required_years": settings.forecast_min_years,
        "eligible_areas": len(groups),
        "required_areas": settings.forecast_min_areas,
        "projections": projections,
        "metrics": metrics,
        "assumptions": assumptions,
    }
