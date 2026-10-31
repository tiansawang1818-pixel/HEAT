import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional


DEFAULT_WEIGHTS: Dict[str, float] = {
    "temperature_anomaly": 0.35,
    "population_exposure": 0.25,
    "vegetation_deficit": 0.18,
    "built_intensity": 0.12,
    "historical_change": 0.10,
}


@dataclass(frozen=True)
class PriorityResult:
    score: float
    components: Dict[str, Optional[float]]
    normalized_components: Dict[str, Optional[float]]
    effective_weights: Dict[str, float]
    missing_inputs: List[str]
    formula_version: str = "priority-v1"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalize_components(values: Mapping[str, Optional[float]]) -> Dict[str, Optional[float]]:
    transforms = {
        "temperature_anomaly": lambda x: clamp01((x + 1.0) / 9.0),
        "population_exposure": lambda x: clamp01(math.log1p(max(x, 0.0)) / math.log1p(30000.0)),
        "vegetation_deficit": lambda x: clamp01(x),
        "built_intensity": lambda x: clamp01(x),
        "historical_change": lambda x: clamp01((x + 0.1) / 0.7),
    }
    return {key: None if values.get(key) is None else transforms[key](float(values[key])) for key in transforms}


def calculate_priority(
    components: Mapping[str, Optional[float]], weights: Optional[Mapping[str, float]] = None
) -> PriorityResult:
    selected_weights = dict(DEFAULT_WEIGHTS)
    if weights:
        unknown = set(weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise ValueError("Unknown priority weight(s): %s" % ", ".join(sorted(unknown)))
        selected_weights.update({key: max(0.0, float(value)) for key, value in weights.items()})

    normalized = normalize_components(components)
    available = [key for key, value in normalized.items() if value is not None]
    missing = [key for key, value in normalized.items() if value is None]
    weight_total = sum(selected_weights[key] for key in available)
    if not available or weight_total <= 0:
        return PriorityResult(0.0, dict(components), normalized, {}, missing)

    effective = {key: selected_weights[key] / weight_total for key in available}
    score = 100.0 * sum(float(normalized[key]) * effective[key] for key in available)
    return PriorityResult(round(score, 2), dict(components), normalized, effective, missing)


def sensitivity_range(result: PriorityResult, delta: float = 0.10) -> Dict[str, float]:
    """Simple, reproducible local sensitivity range for transparent UI display."""
    available = [v for v in result.normalized_components.values() if v is not None]
    if not available:
        return {"low": 0.0, "high": 0.0}
    spread = (max(available) - min(available)) * delta * 100.0
    return {"low": round(max(0.0, result.score - spread), 2), "high": round(min(100.0, result.score + spread), 2)}
