import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.models.entities import TemperatureObservation


SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60


@dataclass(frozen=True)
class Sample:
    x_year: float
    seasonal_phase: float
    value: float


@dataclass(frozen=True)
class HarmonicTrendModel:
    origin_year: float
    coefficients: Tuple[float, float, float, float]

    def predict(self, sample: Sample) -> float:
        angle = 2 * math.pi * sample.seasonal_phase
        features = (1.0, sample.x_year - self.origin_year, math.sin(angle), math.cos(angle))
        return sum(coefficient * feature for coefficient, feature in zip(self.coefficients, features))


def _decimal_year(value: datetime) -> float:
    return value.timestamp() / SECONDS_PER_YEAR


def _phase(value: datetime) -> float:
    return (value.timetuple().tm_yday - 1) / 365.25


def _solve(matrix: List[List[float]], vector: List[float]) -> Optional[List[float]]:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(augmented[index][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column]
            augmented[row_index] = [
                current - factor * pivot_value
                for current, pivot_value in zip(augmented[row_index], augmented[column])
            ]
    return [augmented[index][-1] for index in range(size)]


def _fit(samples: Sequence[Sample]) -> Optional[HarmonicTrendModel]:
    if len(samples) < 4:
        return None
    origin = min(sample.x_year for sample in samples)
    features = []
    for sample in samples:
        angle = 2 * math.pi * sample.seasonal_phase
        features.append((1.0, sample.x_year - origin, math.sin(angle), math.cos(angle)))
    matrix = [[0.0 for _ in range(4)] for _ in range(4)]
    vector = [0.0 for _ in range(4)]
    for row, sample in zip(features, samples):
        for left in range(4):
            vector[left] += row[left] * sample.value
            for right in range(4):
                matrix[left][right] += row[left] * row[right]
    for index in range(4):
        matrix[index][index] += 1e-6
    coefficients = _solve(matrix, vector)
    if coefficients is None or not all(math.isfinite(value) for value in coefficients):
        return None
    return HarmonicTrendModel(origin, tuple(coefficients))


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _sample(row: TemperatureObservation) -> Sample:
    return Sample(_decimal_year(row.observed_at), _phase(row.observed_at), float(row.lst_c_mean))


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
        "model": "linear trend plus annual sine/cosine seasonality with fixed ridge stabilization",
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

    temporal_model_errors: List[float] = []
    temporal_baseline_errors: List[float] = []
    for observations in groups.values():
        test_size = max(2, math.ceil(len(observations) * 0.2))
        training, testing = observations[:-test_size], observations[-test_size:]
        model = _fit([_sample(row) for row in training])
        if model is None:
            continue
        month_means = {
            month: _mean(row.lst_c_mean for row in training if row.observed_at.month == month and row.lst_c_mean is not None)
            for month in {row.observed_at.month for row in training}
        }
        fallback = _mean(row.lst_c_mean for row in training if row.lst_c_mean is not None)
        for row in testing:
            actual = float(row.lst_c_mean)
            temporal_model_errors.append(abs(model.predict(_sample(row)) - actual))
            temporal_baseline_errors.append(abs(month_means.get(row.observed_at.month, fallback) - actual))

    area_ids = sorted(groups)
    holdout_ids = {area_id for index, area_id in enumerate(area_ids) if index % 5 == 0}
    spatial_training: List[Sample] = []
    for area_id in area_ids:
        if area_id in holdout_ids:
            continue
        first = groups[area_id][0]
        baseline = float(first.lst_c_mean)
        first_year = _decimal_year(first.observed_at)
        spatial_training.extend(
            Sample(_decimal_year(row.observed_at) - first_year, _phase(row.observed_at), float(row.lst_c_mean) - baseline)
            for row in groups[area_id][1:]
        )
    spatial_model = _fit(spatial_training)
    spatial_model_errors: List[float] = []
    spatial_baseline_errors: List[float] = []
    if spatial_model:
        for area_id in holdout_ids:
            observations = groups[area_id]
            first = observations[0]
            baseline = float(first.lst_c_mean)
            first_year = _decimal_year(first.observed_at)
            for row in observations[1:]:
                actual_change = float(row.lst_c_mean) - baseline
                sample = Sample(_decimal_year(row.observed_at) - first_year, _phase(row.observed_at), actual_change)
                spatial_model_errors.append(abs(spatial_model.predict(sample) - actual_change))
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

    projections = []
    uncertainty = interval_90 * math.sqrt(horizon_years)
    for area_id, observations in groups.items():
        model = _fit([_sample(row) for row in observations])
        if model is None:
            continue
        last = observations[-1]
        try:
            future_date = last.observed_at.replace(year=last.observed_at.year + horizon_years)
        except ValueError:
            future_date = last.observed_at.replace(month=2, day=28, year=last.observed_at.year + horizon_years)
        change = model.predict(Sample(_decimal_year(future_date), _phase(future_date), 0.0)) - model.predict(_sample(last))
        projections.append(
            {
                "area_id": area_id,
                "horizon_years": horizon_years,
                "scenario_change_c": round(change, 2),
                "uncertainty_c": round(uncertainty, 2),
                "baseline_observed_at": last.observed_at.isoformat(),
                "label": "validated_historical_trend_scenario_not_forecast",
            }
        )
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
