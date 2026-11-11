import itertools
import random

import pytest

from app.services.optimizer import Candidate, baseline_portfolio, candidate_value, optimize_portfolio


def sample_candidates():
    return [
        Candidate("a", 100_000, 2000, 80, 70, "tree"),
        Candidate("b", 200_000, 8000, 65, 75, "shade"),
        Candidate("c", 300_000, 10000, 90, 90, "walkway"),
        Candidate("blocked", 10_000, 999999, 100, 100, "tree", feasible=False),
    ]


def test_optimizer_respects_budget_and_feasibility():
    result = optimize_portfolio(sample_candidates(), 400_000)
    assert result.allocated_thb <= 400_000
    assert "blocked" not in result.selected_ids
    assert result.selected_ids


def test_optimizer_is_deterministic():
    first = optimize_portfolio(sample_candidates(), 300_000)
    second = optimize_portfolio(reversed(sample_candidates()), 300_000)
    assert first.selected_ids == second.selected_ids
    assert first.objective_value == second.objective_value


def test_empty_type_selection_disables_all_projects_and_custom_weights_have_no_hidden_defaults():
    result = optimize_portfolio(sample_candidates(), 1_000_000, enabled_types=[])
    assert result.selected_ids == [] and result.allocated_thb == 0
    assert candidate_value(sample_candidates()[0], {"priority": 1.0}) == .8


@pytest.mark.parametrize("budget", [100_000, 375_000, 100_000_000])
def test_ortools_matches_exhaustive_feasible_portfolios(budget):
    randomizer = random.Random(20261107)
    candidates = [Candidate(str(i), randomizer.randint(50_000, 200_000), randomizer.randint(0, 10000),
                  randomizer.randint(0, 100), randomizer.randint(0, 100), "tree", feasible=i != 5) for i in range(7)]
    weights = {"beneficiaries": .5, "priority": .5}
    valid = [c for c in candidates if c.feasible]
    expected = max(sum(candidate_value(c, weights) for c in group)
        for size in range(len(valid) + 1) for group in itertools.combinations(valid, size)
        if sum(c.cost_thb for c in group) <= budget)
    actual = optimize_portfolio(candidates, budget, weights)
    assert actual.solver == "ortools-cp-sat"
    assert abs(actual.objective_value - expected) < .00001
    assert actual.allocated_thb <= budget
    assert len(actual.selected_ids) == len(set(actual.selected_ids))


def test_baselines_use_actual_ranking_evidence_and_identical_constraints():
    candidates = [
        Candidate("hot", 200_000, 1000, 20, 10, "tree", surface_temperature_c=40),
        Candidate("pop", 200_000, 5000, 90, 50, "shade", surface_temperature_c=35),
        Candidate("blocked", 10_000, 1e6, 100, 100, "tree", feasible=False, surface_temperature_c=70),
    ]
    weights = {"priority": .5, "beneficiaries": .5}
    hot = baseline_portfolio(candidates, 200_000, "hottest_first", weights)
    pop = baseline_portfolio(candidates, 200_000, "population_first", weights)
    assert hot.selected_ids == ["hot"] and pop.selected_ids == ["pop"]
    assert hot.allocated_thb == pop.allocated_thb == 200_000
    assert baseline_portfolio(candidates, 200_000, "hottest_first", weights, ["shade"]).selected_ids == ["pop"]
    assert baseline_portfolio(candidates, 200_000, "hottest_first", weights, []).selected_ids == []


def test_missing_ranking_evidence_is_not_replaced_with_priority_or_zero_people():
    candidates = [Candidate("unknown", 100_000, 0, 100, 0, "tree", beneficiaries_available=False)]
    for strategy in ("hottest_first", "population_first"):
        result = baseline_portfolio(candidates, 100_000, strategy, {"priority": 1})
        assert result.status == "insufficient_data" and result.selected_ids == []
        assert result.beneficiaries is None
    assert optimize_portfolio(candidates, 100_000, {"priority": 1}).beneficiaries is None


def test_solver_never_returns_worse_than_greedy_when_time_runs_out(monkeypatch):
    from app.services import optimizer
    candidates = [optimizer.Candidate(id=str(i), cost_thb=8000, beneficiaries=0, priority_score=50,
                                      benefit_score=0, intervention_type="tree_planting") for i in range(20)]
    original = optimizer._greedy_indexes
    monkeypatch.setattr(optimizer, "_greedy_indexes", lambda c, b, v: original(c, b, v))

    class EmptySolver:
        parameters = type("P", (), {})()
        def solve(self, model): return 2  # FEASIBLE
        def value(self, variable): return 0
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model, "CpSolver", EmptySolver)
    portfolio = optimizer.optimize_portfolio(candidates, 100_000, {"priority": 1.0})
    assert len(portfolio.selected_ids) == 12 and portfolio.solver == "greedy-value-per-baht"
