#!/usr/bin/env python3
"""Dependency-free smoke checks for a running local HEAT//SHIFT stack."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
WEB_URL = os.getenv("WEB_URL", "http://localhost:3000").rstrip("/")


def fetch(url: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "heatshift-smoke/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read(), response.headers.get("content-type", "")


def main() -> int:
    checks = [
        ("API health", f"{API_URL}/api/health", True),
        ("Dataset status", f"{API_URL}/api/datasets/status", True),
        ("Web", WEB_URL, False),
    ]
    failed = False
    for label, url, expect_json in checks:
        try:
            status, body, content_type = fetch(url)
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            if expect_json:
                json.loads(body)
            print(f"PASS  {label}: {url} ({content_type})")
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            failed = True
            print(f"FAIL  {label}: {url}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
