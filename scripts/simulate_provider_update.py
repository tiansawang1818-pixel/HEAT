#!/usr/bin/env python3
"""Submit the explicitly synthetic provider fixture to a local authorized API.

The API must independently enforce ALLOW_FIXTURE_INGESTION and restrict fixture
paths to FIXTURE_ROOT. This script never changes a real provider checkpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
FIXTURE_PATH = os.getenv(
    "FIXTURE_PATH", "/workspace/data/fixtures/provider_update.json"
)
LOCAL_FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/provider_update.json"


def request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "heatshift-fixture/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if ADMIN_API_KEY:
        headers["X-Admin-Key"] = ADMIN_API_KEY
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def read_status(job_id: str) -> dict:
    query = urllib.parse.urlencode({"job_id": job_id})
    try:
        status = request_json(f"{API_URL}/api/ingestion/status?{query}")
        if str(status.get("id", "")) == job_id or "status" in status:
            return status
        if isinstance(status.get("job"), dict) and str(status["job"].get("id", "")) == job_id:
            return status["job"]
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    jobs = request_json(f"{API_URL}/api/ingestion/jobs")
    values = jobs.get("items", jobs if isinstance(jobs, list) else [])
    return next((item for item in values if str(item.get("id")) == job_id), {})


def main() -> int:
    if os.getenv("ALLOW_FIXTURE_INGESTION", "").lower() not in {"1", "true", "yes"}:
        print("Refusing: set ALLOW_FIXTURE_INGESTION=true explicitly.", file=sys.stderr)
        return 2
    if not ADMIN_API_KEY:
        print("Refusing: ADMIN_API_KEY is required.", file=sys.stderr)
        return 2
    fixture = json.loads(LOCAL_FIXTURE.read_text())
    if fixture.get("fixture_kind") != "synthetic" or fixture.get("publication_allowed") is not False:
        print("Refusing: fixture safety labels are missing.", file=sys.stderr)
        return 2

    print(f"Submitting synthetic test fixture {fixture['source_asset_id']} ...")
    result = request_json(
        f"{API_URL}/api/ingestion/run",
        method="POST",
        payload={"source": "fixture", "fixture_path": FIXTURE_PATH},
    )
    job_id = str(result.get("job_id") or result.get("id") or "")
    if not job_id:
        print(f"API did not return a job ID: {result}", file=sys.stderr)
        return 1
    print(f"Queued job {job_id}")

    terminal = {"published", "completed", "success", "failed", "rejected", "error"}
    for _ in range(60):
        status = read_status(job_id)
        state = str(status.get("status") or status.get("processing_status") or "unknown").lower()
        print(f"  {state}")
        if state in terminal:
            if state in {"failed", "rejected", "error"}:
                print(json.dumps(status, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
            print("Synthetic update pipeline completed; verify SSE/layer version in the UI.")
            return 0
        time.sleep(1)
    print("Timed out waiting for the fixture job; it may still be running.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        print(f"HTTP {error.code}: {body}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as error:
        print(f"Could not reach API: {error}", file=sys.stderr)
        raise SystemExit(1)
