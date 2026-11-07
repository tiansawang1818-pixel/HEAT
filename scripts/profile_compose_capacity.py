#!/usr/bin/env python3
"""Measure the full runtime in isolated GitHub CI, never on an operator's PC.

This collects cgroup v2 memory/CPU while serving bounded read-only HTTP. It is
an idle/warm baseline, not an ingestion/optimizer stress test or a hosting quote.
It neither starts services nor reads container environment/secrets.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import ssl
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, build_opener


SERVICES = ("postgres", "redis", "api", "web", "worker", "dispatcher", "beat")
MIB = 1024 ** 2


def parse_cgroup(text):
    lines = text.splitlines()
    current = int(lines[0])
    fields = {}
    for line in lines[1:]:
        key, value = line.split()
        fields[key] = int(value)
    for key in ("anon", "file", "usage_usec", "oom_kill"):
        if key not in fields or fields[key] < 0:
            raise ValueError("Missing cgroup v2 evidence")
    if current <= 0:
        raise ValueError("Memory measurements must be positive")
    return {"current_bytes": current, "anonymous_bytes": fields["anon"],
            "file_bytes": fields["file"], "cpu_usage_usec": fields["usage_usec"],
            "oom_kill_count": fields["oom_kill"], "sampled_monotonic": time.monotonic()}


def assess_capacity(samples, ram_allowance_bytes=1024 * MIB):
    if len(samples) < 2 or any(set(sample) != set(SERVICES) for sample in samples):
        raise ValueError("Every sample must include all seven runtime services")
    total_current = max(sum(row["current_bytes"] for row in sample.values()) for sample in samples)
    total_anonymous = max(sum(row["anonymous_bytes"] for row in sample.values()) for sample in samples)
    if total_anonymous > ram_allowance_bytes:
        status = "baseline_anonymous_memory_exceeds_allowance"
    elif total_current > ram_allowance_bytes:
        status = "baseline_memory_pressure_exceeds_allowance"
    else:
        status = "production_fit_not_established"
    return {"status": status, "ram_allowance_bytes": ram_allowance_bytes,
            "peak_sampled_total_bytes": total_current, "peak_sampled_anonymous_bytes": total_anonymous,
            "ingestion_and_optimization_peak_verified": False, "provider_resource_admission_verified": False,
            "complete_hosting_fit_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--url", default="https://localhost:18443")
    parser.add_argument("--ca-file", required=True)
    parser.add_argument("--samples", type=int, default=10, choices=range(2, 31))
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CI") != "true" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Refusing container profiling outside isolated GitHub CI")
    if urlsplit(args.url).scheme != "https" or urlsplit(args.url).hostname not in ("localhost", "127.0.0.1"):
        parser.error("Only the isolated CI HTTPS endpoint may be profiled")
    if not 0.5 <= args.interval <= 5:
        parser.error("Interval must be bounded between 0.5 and 5 seconds")
    compose = ["docker", "compose", "--env-file", args.env_file,
               "-f", "docker-compose.yml", "-f", "docker-compose.production.yml",
               "-f", "infra/ci/docker-compose.smoke.yml"]
    containers = {}
    for service in SERVICES:
        value = subprocess.run(compose + ["ps", "--quiet", service], capture_output=True, text=True, check=True).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{12,64}", value):
            raise ValueError("Expected exactly one running container per runtime service")
        containers[service] = value
    if len(set(containers.values())) != len(SERVICES):
        raise ValueError("Runtime services must have separate cgroups")

    def collect(service):
        result = subprocess.run(["docker", "exec", containers[service], "cat",
                                 "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat",
                                 "/sys/fs/cgroup/memory.events", "/sys/fs/cgroup/cpu.stat"],
                                capture_output=True, text=True, timeout=10, check=True)
        return service, parse_cgroup(result.stdout)

    opener = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=args.ca_file)))
    paths = ("/", "/observatory", "/optimizer", "/api/map/layers", "/api/areas", "/api/health/ready")
    # Warm the actual Next/FastAPI processes, without a second client process
    # inside their containers distorting the measured memory.
    for path in paths:
        with opener.open(args.url + path, timeout=15) as response:
            if response.status != 200:
                raise ValueError("Read-only CI warmup failed")
            response.read()
    samples = []
    with ThreadPoolExecutor(max_workers=len(SERVICES)) as pool:
        for index in range(args.samples):
            with opener.open(args.url + paths[index % len(paths)], timeout=15) as response:
                response.read()
            samples.append(dict(pool.map(collect, SERVICES)))
            if index + 1 < args.samples:
                time.sleep(args.interval)
    by_service = {}
    for service in SERVICES:
        rows = [sample[service] for sample in samples]
        elapsed = rows[-1]["sampled_monotonic"] - rows[0]["sampled_monotonic"]
        by_service[service] = {
            "peak_sampled_memory_bytes": max(row["current_bytes"] for row in rows),
            "peak_sampled_anonymous_bytes": max(row["anonymous_bytes"] for row in rows),
            "mean_cpu_cores": round((rows[-1]["cpu_usage_usec"] - rows[0]["cpu_usage_usec"]) / (elapsed * 1_000_000), 6),
            "oom_kill_count": max(row["oom_kill_count"] for row in rows),
        }
    report = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "isolated_github_ci_idle_and_read_only_http_profile",
              "architecture": platform.machine(), "functional_code_sha": os.environ.get("GITHUB_SHA"),
              "samples": args.samples, "services": by_service, "assessment": assess_capacity(samples),
              "limitations": ["Measured on GitHub CI, not on Hostless. Provider allocation defaults and quotas are not proven.",
                              "Real environmental ingestion, large rasters, OR-Tools peak load and forecast training were not exercised.",
                              "No external data checks were enabled. Caddy/platform edge memory is excluded from totals.",
                              "Docker exec sampling overhead is included; reclaimable file cache is reported separately from anonymous memory.",
                              "Neither successful baseline sampling nor below-pool memory establishes zero cost or production fit."]}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
