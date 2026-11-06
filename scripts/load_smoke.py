#!/usr/bin/env python3
"""Bounded read-only latency smoke test, not a production capacity claim."""
import argparse
import json
import math
import ssl
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPSHandler, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca-file")
    parser.add_argument("--requests", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-p95-ms", type=float, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.path not in ("", "/"):
        parser.error("A credential-free HTTPS origin is required")
    if not 10 <= args.requests <= 2000 or not 1 <= args.concurrency <= 16:
        parser.error("Use 10–2000 requests and 1–16 workers")
    context = ssl.create_default_context(cafile=args.ca_file)
    paths = ["/api/health/live", "/api/map/layers", "/api/map/features?layer=priority&limit=10&bbox=100.4,13.6,100.7,13.9"]

    def sample(index):
        start = time.perf_counter()
        path = paths[index % len(paths)]
        try:
            with build_opener(HTTPSHandler(context=context)).open(args.url.rstrip("/") + path, timeout=10) as response:
                payload = json.loads(response.read(2_000_000))
                assert response.status == 200
            return {"elapsed_ms": (time.perf_counter() - start) * 1000, "ok": True, "feature_count": len(payload.get("features", [])) if isinstance(payload, dict) else 0}
        except (OSError, ValueError, AssertionError):
            return {"elapsed_ms": (time.perf_counter() - start) * 1000, "ok": False, "feature_count": 0}

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        samples = list(executor.map(sample, range(args.requests)))
    times = sorted(row["elapsed_ms"] for row in samples)
    p95 = times[math.ceil(len(times) * .95) - 1]
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "requests": args.requests, "concurrency": args.concurrency,
              "failures": sum(not row["ok"] for row in samples), "mean_ms": round(statistics.mean(times), 2), "p95_ms": round(p95, 2),
              "max_features_in_response": max(row["feature_count"] for row in samples),
              "scope": "bounded_read_only_latency_smoke", "limitations_th": "ไม่ใช่การรับรองความจุ production ต้องทดสอบอีกครั้งด้วยข้อมูลจริงและจำนวนผู้ใช้ที่คาดการณ์"}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["failures"] == 0 and p95 <= args.max_p95_ms else 2


if __name__ == "__main__":
    sys.exit(main())
