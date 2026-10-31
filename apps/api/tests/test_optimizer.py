from app.services.optimizer import Candidate, optimize_portfolio


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
