#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Check HEAT//SHIFT production readiness without reading secrets.")
    parser.add_argument(
        "--url",
        default=os.environ.get("HEATSHIFT_API_URL", "http://localhost:8000"),
        help="Public API base URL (default: HEATSHIFT_API_URL or http://localhost:8000)",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    endpoint = urljoin(args.url.rstrip("/") + "/", "api/system/readiness?probe_dependencies=true")
    try:
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "heatshift-readiness/1"})
        with urlopen(request, timeout=args.timeout) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ตรวจ Production readiness ไม่สำเร็จ: {exc}", file=sys.stderr)
        return 3

    if not isinstance(payload, dict) or payload.get("scope") != "configuration_and_dependencies" or not isinstance(payload.get("configuration_ready"), bool):
        print("API ตอบผลตรวจในรูปแบบที่ไม่รองรับ", file=sys.stderr)
        return 3

    summary = payload.get("summary", {})
    status = "ผ่านการตรวจเบื้องต้น" if payload.get("configuration_ready") else "ยังมีรายการต้องแก้ไข"
    print(
        f"HEAT//SHIFT Production readiness: {status} "
        f"(พร้อม {summary.get('ready', 0)} / เตือน {summary.get('warning', 0)} / ติดขัด {summary.get('blocked', 0)})"
    )
    for check in payload.get("checks", []):
        if check.get("status") == "ready":
            continue
        marker = "!" if check.get("status") == "warning" else "x"
        print(f"[{marker}] {check.get('label_th')}: {check.get('detail_th')}")
        if check.get("action_th"):
            print(f"    แก้ไข: {check['action_th']}")
    print(payload.get("limitations_th", ""))
    return 0 if payload.get("configuration_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
