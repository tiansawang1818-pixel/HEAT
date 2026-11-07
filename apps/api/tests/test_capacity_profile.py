"""Incomplete or low-load measurements must never become free-hosting approval."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "capacity_profile", Path(__file__).resolve().parents[3] / "scripts" / "profile_compose_capacity.py"
)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def sample(current_mib, anonymous_mib):
    return {name: {"current_bytes": current_mib * profile.MIB, "anonymous_bytes": anonymous_mib * profile.MIB} for name in profile.SERVICES}


def test_below_pool_idle_baseline_does_not_certify_production():
    report = profile.assess_capacity([sample(80, 50), sample(90, 60)])
    assert report["status"] == "production_fit_not_established"
    assert report["complete_hosting_fit_verified"] is False
    assert report["ingestion_and_optimization_peak_verified"] is False


def test_anonymous_memory_over_pool_is_distinct_from_reclaimable_cache():
    report = profile.assess_capacity([sample(200, 160), sample(210, 170)])
    assert report["status"] == "baseline_anonymous_memory_exceeds_allowance"
    pressure = profile.assess_capacity([sample(200, 80), sample(210, 90)])
    assert pressure["status"] == "baseline_memory_pressure_exceeds_allowance"


def test_totals_use_concurrent_samples_not_sum_of_different_service_peaks():
    first, second = sample(50, 40), sample(50, 40)
    first["api"]["current_bytes"] = 500 * profile.MIB
    second["worker"]["current_bytes"] = 500 * profile.MIB
    report = profile.assess_capacity([first, second])
    assert report["peak_sampled_total_bytes"] == 800 * profile.MIB


def test_missing_services_are_not_counted_as_zero_memory():
    incomplete = sample(50, 40)
    incomplete.pop("dispatcher")
    with pytest.raises(ValueError, match="all seven"):
        profile.assess_capacity([sample(50, 40), incomplete])


def test_cgroup_parser_requires_positive_memory_and_cpu_oom_fields():
    parsed = profile.parse_cgroup("12345\nanon 1000\nfile 2000\noom_kill 0\nusage_usec 4000\n")
    assert parsed["current_bytes"] == 12345 and parsed["anonymous_bytes"] == 1000
    with pytest.raises(ValueError):
        profile.parse_cgroup("12345\nanon 1000\nfile 2000\n")
