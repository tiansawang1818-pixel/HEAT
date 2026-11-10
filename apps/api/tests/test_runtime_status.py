import json
from pathlib import Path

from app.services import runtime_status as runtime


def test_runtime_reports_limits_and_liveness_without_secrets_or_pids(monkeypatch, tmp_path):
    monkeypatch.setenv("HOSTLESS_COMBINED_RUNTIME", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://private:secret@private.example/database")
    for name, content in {"memory.max": "805306368", "memory.current": "300000000", "cpu.max": "30000 100000", "memory.events": "oom_kill 0\n"}.items():
        (tmp_path / name).write_text(content)
    process_file = tmp_path / "processes.json"
    process_file.write_text(json.dumps({name: 101 + index for index, name in enumerate(runtime.COMPONENTS)}))
    monkeypatch.setattr(runtime.os, "kill", lambda pid, signal: None)
    report = runtime.runtime_status(tmp_path, process_file)
    assert report["status"] == "observed" and all(report["components"].values())
    assert report["cpu_limit_cores"] == 0.3 and report["memory_limit_bytes"] == 768 * 1024 ** 2
    assert "private" not in json.dumps(report) and "pids" not in report
    assert set(report["components"].values()) == {True}
    assert not report["whole_stack_capacity_verified"]


def test_missing_cgroup_or_separate_services_does_not_claim_zero_usage(monkeypatch, tmp_path):
    monkeypatch.delenv("HOSTLESS_COMBINED_RUNTIME", raising=False)
    assert runtime.runtime_status(tmp_path)["status"] == "not_collected"
    monkeypatch.setenv("HOSTLESS_COMBINED_RUNTIME", "true")
    report = runtime.runtime_status(tmp_path)
    assert report["status"] == "unavailable" and "memory_used_bytes" not in report


def test_runtime_separates_reclaimable_cache_and_reports_component_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOSTLESS_COMBINED_RUNTIME", "true")
    files = {"memory.max": "805306368", "memory.current": "700000000", "cpu.max": "30000 100000",
             "memory.events": "oom_kill 0\n", "memory.stat": "anon 400000000\nfile 300000000\ninactive_file 200000000\n"}
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    process_file = tmp_path / "processes.json"
    process_file.write_text(json.dumps({name: 101 + index for index, name in enumerate(runtime.COMPONENTS)}))
    monkeypatch.setattr(runtime.os, "kill", lambda pid, signal: None)
    report = runtime.runtime_status(tmp_path, process_file)
    assert report["memory_anon_bytes"] == 400000000 and report["memory_file_bytes"] == 300000000
    assert report["memory_working_set_bytes"] == 500000000
    assert set(report["component_rss_bytes"]) == set(runtime.COMPONENTS)


def test_tree_rss_counts_a_process_and_its_children(tmp_path):
    for pid, kb in ((10, 1000), (11, 500)):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "status").write_text("Name:\tx\nVmRSS:\t%d kB\n" % kb)
    (tmp_path / "10" / "task" / "10").mkdir(parents=True)
    (tmp_path / "10" / "task" / "10" / "children").write_text("11")
    assert runtime._tree_rss_bytes(10, tmp_path) == 1500 * 1024
