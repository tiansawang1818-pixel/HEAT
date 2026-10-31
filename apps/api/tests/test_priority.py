from app.services.priority import calculate_priority, sensitivity_range


def test_priority_is_reproducible_and_bounded():
    result = calculate_priority(
        {
            "temperature_anomaly": 4.0,
            "population_exposure": 12000,
            "vegetation_deficit": 0.8,
            "built_intensity": 0.7,
            "historical_change": 0.2,
        }
    )
    assert 0 <= result.score <= 100
    assert round(sum(result.effective_weights.values()), 8) == 1.0
    assert result.formula_version == "priority-v1"
    assert sensitivity_range(result)["low"] <= result.score <= sensitivity_range(result)["high"]


def test_priority_redistributes_missing_data_weights():
    result = calculate_priority(
        {
            "temperature_anomaly": 3.0,
            "population_exposure": None,
            "vegetation_deficit": 0.5,
            "built_intensity": None,
            "historical_change": 0.1,
        }
    )
    assert set(result.missing_inputs) == {"population_exposure", "built_intensity"}
    assert round(sum(result.effective_weights.values()), 8) == 1.0
