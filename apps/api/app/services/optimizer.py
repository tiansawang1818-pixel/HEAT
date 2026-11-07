from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Candidate:
    id: str
    cost_thb: int
    beneficiaries: float
    priority_score: float
    benefit_score: float
    intervention_type: str
    feasible: bool = True
    surface_temperature_c: Optional[float] = None
    beneficiaries_available: bool = True


@dataclass(frozen=True)
class Portfolio:
    selected_ids: List[str]
    allocated_thb: int
    beneficiaries: Optional[float]
    objective_value: float
    solver: str
    contributions: Dict[str, float]
    status: str = "completed"
    limitations: List[str] = field(default_factory=list)


def candidate_value(candidate: Candidate, weights: Dict[str, float]) -> float:
    beneficiaries = min(1.0, max(0.0, candidate.beneficiaries) / 100000.0)
    priority = min(1.0, max(0.0, candidate.priority_score) / 100.0)
    benefit = min(1.0, max(0.0, candidate.benefit_score) / 100.0)
    cost_efficiency = min(1.0, (max(candidate.beneficiaries, 0.0) / max(candidate.cost_thb, 1)) * 500.0)
    return (
        weights.get("beneficiaries", 0.0) * beneficiaries
        + weights.get("priority", 0.0) * priority
        + weights.get("benefit", 0.0) * benefit
        + weights.get("cost_effectiveness", 0.0) * cost_efficiency
    )


def _solve_ortools(candidates: Sequence[Candidate], budget_thb: int, values: Sequence[float]) -> Optional[Tuple[List[int], str]]:
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None
    model = cp_model.CpModel()
    selected = [model.new_bool_var("candidate_%d" % index) for index in range(len(candidates))]
    model.add(sum(candidates[i].cost_thb * selected[i] for i in range(len(candidates))) <= budget_thb)
    scaled = [int(round(value * 1_000_000)) for value in values]
    model.maximize(sum(scaled[i] * selected[i] for i in range(len(candidates))))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 8.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return ([], "ortools-cp-sat")
    return ([i for i in range(len(candidates)) if solver.value(selected[i])], "ortools-cp-sat")


def _solve_dynamic(candidates: Sequence[Candidate], budget_thb: int, values: Sequence[float]) -> Tuple[List[int], str]:
    """Deterministic Pareto-frontier knapsack fallback (no fabricated selection)."""
    states: Dict[int, Tuple[float, Tuple[int, ...]]] = {0: (0.0, ())}
    for index, candidate in enumerate(candidates):
        additions: Dict[int, Tuple[float, Tuple[int, ...]]] = {}
        for cost, (value, indexes) in list(states.items()):
            new_cost = cost + candidate.cost_thb
            if new_cost <= budget_thb:
                new_value = value + values[index]
                previous = states.get(new_cost) or additions.get(new_cost)
                if previous is None or new_value > previous[0]:
                    additions[new_cost] = (new_value, indexes + (index,))
        states.update(additions)
        best = -1.0
        for cost in sorted(list(states)):
            value = states[cost][0]
            if value <= best:
                del states[cost]
            else:
                best = value
    _, (_, indexes) = max(states.items(), key=lambda item: (item[1][0], -item[0]))
    return (list(indexes), "deterministic-knapsack")


def eligible_candidates(candidates: Iterable[Candidate], enabled_types=None) -> List[Candidate]:
    allowed = set(enabled_types) if enabled_types is not None else None
    return sorted([c for c in candidates if c.feasible and c.cost_thb > 0
                   and (allowed is None or c.intervention_type in allowed)], key=lambda c: c.id)


def _portfolio(selected, weights, solver):
    contributions = {c.id: round(candidate_value(c, weights), 6) for c in selected}
    return Portfolio(
        selected_ids=[c.id for c in selected], allocated_thb=sum(c.cost_thb for c in selected),
        beneficiaries=sum(c.beneficiaries for c in selected) if all(c.beneficiaries_available for c in selected) else None,
        objective_value=round(sum(contributions.values()), 6), solver=solver, contributions=contributions,
        limitations=["จำนวนผู้รับประโยชน์เป็นผลรวมประมาณการรายโครงการ ยังไม่หักความซ้ำของคนหรือพื้นที่บริการ"],
    )


def baseline_portfolio(candidates, budget_thb, strategy, weights, enabled_types=None) -> Portfolio:
    """Budget-feasible greedy baselines, with missing ranking evidence explicit."""
    if not 100_000 <= budget_thb <= 100_000_000:
        raise ValueError("Budget must be between THB 100,000 and THB 100,000,000")
    eligible = eligible_candidates(candidates, enabled_types)
    if strategy == "hottest_first":
        unavailable = any(c.surface_temperature_c is None for c in eligible)
        key = lambda c: (-c.surface_temperature_c, c.id)
        reason = "ยังไม่มีอุณหภูมิพื้นผิวที่ผ่านการตรวจสำหรับโครงการที่มีสิทธิ์ทุกจุด จึงยังเทียบแบบร้อนที่สุดก่อนไม่ได้"
    elif strategy == "population_first":
        unavailable = any(not c.beneficiaries_available for c in eligible)
        key = lambda c: (-c.beneficiaries, c.id)
        reason = "ยังไม่มีประมาณการผู้รับประโยชน์ที่มีหลักฐานสำหรับโครงการที่มีสิทธิ์ทุกจุด จึงยังเทียบแบบประชากรก่อนไม่ได้"
    else:
        raise ValueError("Unsupported allocation baseline")
    if unavailable:
        return Portfolio([], 0, None, 0, "deterministic-greedy", {}, "insufficient_data", [reason])
    selected, remaining = [], budget_thb
    for candidate in sorted(eligible, key=key):
        if candidate.cost_thb <= remaining:
            selected.append(candidate)
            remaining -= candidate.cost_thb
    return _portfolio(selected, weights, "deterministic-greedy")


def optimize_portfolio(
    candidates: Iterable[Candidate],
    budget_thb: int,
    weights: Optional[Dict[str, float]] = None,
    enabled_types: Optional[Iterable[str]] = None,
) -> Portfolio:
    if budget_thb < 100_000 or budget_thb > 100_000_000:
        raise ValueError("Budget must be between THB 100,000 and THB 100,000,000")
    weights = weights or {"beneficiaries": 0.35, "priority": 0.30, "benefit": 0.20, "cost_effectiveness": 0.15}
    eligible = eligible_candidates(candidates, enabled_types)
    values = [candidate_value(candidate, weights) for candidate in eligible]
    solution = _solve_ortools(eligible, budget_thb, values)
    indexes, solver_name = solution if solution is not None else _solve_dynamic(eligible, budget_thb, values)
    selected = [eligible[index] for index in indexes]
    return _portfolio(selected, weights, solver_name)
