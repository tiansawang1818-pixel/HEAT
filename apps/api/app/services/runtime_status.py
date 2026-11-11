"""Read runtime limits without exposing environment variables or process IDs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


COMPONENTS = ("api", "web", "ingestion", "dispatcher")


def _tree_rss_bytes(pid, proc=Path("/proc")):
    """Resident memory of a process and its direct children (e.g. a Celery
    prefork child). Shared pages are counted once per process."""
    total = 0
    pids = [pid]
    try:
        pids += [int(child) for child in (proc / str(pid) / "task" / str(pid) / "children").read_text().split()]
    except (OSError, ValueError):
        pass
    for item in pids:
        try:
            for line in (proc / str(item) / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            continue
    return total


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
        component_rss = {}
        for name in COMPONENTS:
            pid = pids.get(name)
            try:
                if type(pid) is not int or pid <= 1:
                    raise ValueError("Invalid component PID")
                os.kill(pid, 0)
                components[name] = True
                component_rss[name] = _tree_rss_bytes(pid)
            except (OSError, ValueError):
                components[name] = False
        report.update(
            status="observed", components=components,
            memory_limit_bytes=None if maximum == "max" else int(maximum),
            memory_used_bytes=int((cgroup / "memory.current").read_text()),
            cpu_limit_cores=None if quota == "max" else round(int(quota) / int(period), 6),
            oom_kill_count=int(events["oom_kill"]),
            component_rss_bytes=component_rss,
        )
        try:
            # memory.current includes page cache the kernel reclaims under
            # pressure; anon memory is what cannot be given back.
            stat = dict(line.split() for line in (cgroup / "memory.stat").read_text().splitlines())
            report.update(memory_anon_bytes=int(stat["anon"]), memory_file_bytes=int(stat["file"]),
                          memory_working_set_bytes=report["memory_used_bytes"] - int(stat.get("inactive_file", 0)))
        except (OSError, ValueError, KeyError):
            pass
    except (OSError, ValueError, KeyError, TypeError):
        report["status"] = "unavailable"
    return report
