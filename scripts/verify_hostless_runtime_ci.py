#!/usr/bin/env python3
"""Check the combined image under its proposed cap, in disposable GitHub CI only."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    if os.environ.get("CI") != "true" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Refusing to start runtime services outside isolated GitHub CI")
    env_file = "/tmp/heatshift-production-ci.env"
    compose = ["docker", "compose", "--env-file", env_file, "-f", "docker-compose.yml",
               "-f", "docker-compose.production.yml", "-f", "infra/ci/docker-compose.smoke.yml"]
    name = "heatshift-hostless-ci"

    def run(args, *, capture=False, timeout=120):
        return subprocess.run(args, check=True, capture_output=capture, text=True, timeout=timeout).stdout

    run(["docker", "build", "-f", "infra/docker/hostless.Dockerfile", "-t", "heatshift-hostless:ci", "."], timeout=600)
    postgres = run(compose + ["ps", "-q", "postgres"], capture=True).strip()
    networks = json.loads(run(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", postgres], capture=True))
    network = next(key for key in networks if key.endswith("_default"))
    # The preceding baseline step disabled every provider. Replace only the
    # disposable app processes; keep the same isolated PG/Redis test state.
    run(compose + ["stop", "api", "web", "worker", "dispatcher", "beat"], timeout=90)
    run(["docker", "run", "-d", "--name", name, "--network", network,
         "--cpus=0.3", "--memory=768m", "--memory-swap=768m", "--read-only",
         "--tmpfs", "/tmp:rw,size=96m,mode=1777", "-p", "127.0.0.1:13012:8000",
         "--env-file", env_file, "-e", "CI=true", "-e", "GITHUB_ACTIONS=true",
         "-e", "HEATSHIFT_ISOLATED_CI=true", "-e", "PORT=8000", "-e", "REDIS_URL=redis://redis:6379/0",
         "-e", "AUTH_REDIS_URL=redis://redis:6379/2", "-e", "ENVIRONMENT=production",
         "-e", "ADMIN_AUTH_MODE=oidc", "-e", "DEMO_MODE=false", "-e", "ALLOW_FIXTURE_INGESTION=false",
         "heatshift-hostless:ci"], capture=True)

    base = "http://127.0.0.1:13012"

    def wait_ready():
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                with urlopen(base + "/api/health", timeout=5) as response:
                    if response.status == 200 and json.load(response)["status"] == "ok":
                        return
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(2)
        raise AssertionError("Combined runtime did not become healthy")

    def inspect_runtime():
        code = "import json,os; p=json.load(open('/tmp/heatshift/processes.json')); assert len(p)==5; [os.kill(pid,0) for pid in p.values()]; print(json.dumps(p))"
        return json.loads(run(["docker", "exec", name, "python", "-c", code], capture=True))

    try:
        wait_ready()
        processes = inspect_runtime()
        with urlopen(base + "/api/system/runtime", timeout=10) as response:
            observed = json.load(response)
        assert observed["cpu_limit_cores"] == 0.3
        assert observed["memory_limit_bytes"] == 768 * 1024 ** 2
        assert all(observed["components"].values())
        # Hostless gives its liveness probe only one second. An unavailable
        # database must not turn that probe into a dependency timeout.
        probe_times = []
        run(["docker", "pause", postgres])
        try:
            for _ in range(3):
                started = time.monotonic()
                with urlopen(base + "/api/health/live", timeout=1) as probe:
                    assert probe.status == 200 and json.load(probe)["status"] == "ok"
                probe_times.append(time.monotonic() - started)
        finally:
            run(["docker", "unpause", postgres])
        with urlopen(base + "/api/health", timeout=10) as dependency_probe:
            assert dependency_probe.status == 200
        for path in ("/", "/observatory", "/optimizer", "/api/map/layers", "/api/system/data-readiness"):
            with urlopen(base + path, timeout=20) as response:
                assert response.status == 200
                response.read()
        try:
            urlopen(Request(base + "/api/admin/ingestion", data=b'{"source":"open-meteo"}', headers={"Content-Type": "application/json"}), timeout=10)
            raise AssertionError("Admin operation unexpectedly accepted")
        except HTTPError as error:
            assert error.code == 401
        with urlopen(base + "/api/events/stream", timeout=10) as response:
            assert response.readline().strip() == b"retry: 5000"
            assert response.readline().strip() == b": connected"
            startup_paths = ["/api/health", "/api/system/runtime", "/api/datasets/status", "/api/map/layers",
                             "/api/areas", "/api/priority/rankings", "/api/ingestion/status", "/api/health"]

            def read_startup(path):
                with urlopen(base + path, timeout=20) as startup_response:
                    assert startup_response.status == 200
                    startup_response.read()

            with ThreadPoolExecutor(max_workers=len(startup_paths)) as clients:
                list(clients.map(read_startup, startup_paths))
        samples = []
        for _ in range(10):
            with urlopen(base + "/api/map/layers", timeout=15) as response:
                response.read()
            raw = run(["docker", "exec", name, "cat", "/sys/fs/cgroup/memory.current",
                       "/sys/fs/cgroup/memory.events", "/sys/fs/cgroup/cpu.stat"], capture=True)
            lines = raw.splitlines()
            fields = dict(line.split() for line in lines[1:])
            assert int(fields["oom_kill"]) == 0
            samples.append({"memory_bytes": int(lines[0]), "cpu_usage_usec": int(fields["usage_usec"])})
            time.sleep(2)
        # Kill one real idle worker. Fail-fast must stop the whole application,
        # and a provider-style restart must start all five processes again.
        run(["docker", "exec", name, "python", "-c", "import os,signal; os.kill(%d,signal.SIGKILL)" % processes["ingestion"]])
        exit_code = run(["docker", "wait", name], capture=True, timeout=45).strip()
        assert exit_code != "0"
        run(["docker", "start", name], capture=True)
        wait_ready()
        inspect_runtime()
        code = "from sqlalchemy import func,select; from app.db.session import SessionLocal; from app.models.entities import SourceCheck; d=SessionLocal(); assert d.scalar(select(func.count()).select_from(SourceCheck))==0; d.close(); print('No external provider checks')"
        run(["docker", "exec", name, "python", "-c", code], capture=True)
        report = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "scope": "isolated_github_ci_combined_runtime", "functional_code_sha": os.environ.get("GITHUB_SHA"),
            "cpu_limit": 0.3, "memory_limit_bytes": 768 * 1024 ** 2,
            "provider_assigned_web_port": 8000,
            "peak_sampled_memory_bytes": max(row["memory_bytes"] for row in samples),
            "sample_count": len(samples), "process_count": 5,
            "read_only_root_filesystem": True, "swap_enabled": False,
            "pages_api_admin_gates_sse_verified": True, "child_failure_and_restart_verified": True,
            "concurrent_startup_reads_with_sse": len(startup_paths),
            "liveness_with_database_paused_verified": True,
            "max_liveness_probe_seconds": max(probe_times),
            "external_source_checks": 0, "provider_resource_admission_verified": False,
            "real_ingestion_optimizer_peak_verified": False, "complete_hosting_fit_verified": False,
        }
        Path("/tmp/heatshift-hostless-runtime-ci.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except Exception:
        run(["docker", "logs", "--tail", "100", name], timeout=10)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


if __name__ == "__main__":
    main()
