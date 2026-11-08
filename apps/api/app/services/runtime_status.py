"""Read runtime limits without exposing environment variables or process IDs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


COMPONENTS = ("api", "web", "ingestion", "dispatcher", "beat")


def runtime_status(cgroup=Path("/sys/fs/cgroup"), process_file=Path("/tmp/heatshift/processes.json")):
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "scope": "runtime_resource_observation",
              "mode": "combined" if os.environ.get("HOSTLESS_COMBINED_RUNTIME") == "true" else "separate_services",
              "status": "not_collected", "whole_stack_capacity_verified": False}
    if report["mode"] != "combined":
        return report
    try:
        maximum = (cgroup / "memory.max").read_text().strip()
        quota, period = (cgroup / "cpu.max").read_text().split()
        events = dict(line.split() for line in (cgroup / "memory.events").read_text().splitlines())
        pids = json.loads(process_file.read_text())
        components = {}
        for name in COMPONENTS:
            pid = pids.get(name)
            try:
                if type(pid) is not int or pid <= 1:
                    raise ValueError("Invalid component PID")
                os.kill(pid, 0)
                components[name] = True
            except (OSError, ValueError):
                components[name] = False
        report.update(
            status="observed", components=components,
            memory_limit_bytes=None if maximum == "max" else int(maximum),
            memory_used_bytes=int((cgroup / "memory.current").read_text()),
            cpu_limit_cores=None if quota == "max" else round(int(quota) / int(period), 6),
            oom_kill_count=int(events["oom_kill"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        report["status"] = "unavailable"
    return report
