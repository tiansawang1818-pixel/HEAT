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
    assert "private" not in json.dumps(report) and "101" not in json.dumps(report)
    assert not report["whole_stack_capacity_verified"]


def test_missing_cgroup_or_separate_services_does_not_claim_zero_usage(monkeypatch, tmp_path):
    monkeypatch.delenv("HOSTLESS_COMBINED_RUNTIME", raising=False)
    assert runtime.runtime_status(tmp_path)["status"] == "not_collected"
    monkeypatch.setenv("HOSTLESS_COMBINED_RUNTIME", "true")
    report = runtime.runtime_status(tmp_path)
    assert report["status"] == "unavailable" and "memory_used_bytes" not in report
