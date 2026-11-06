#!/usr/bin/env python3
"""Read-only, credential-free checks against a running release candidate."""
import argparse
import json
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPSHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description="ตรวจ release candidate โดยไม่สั่ง refresh หรือเขียนข้อมูล")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ca-file", help="CA file for an explicitly isolated TLS test; omitted for public trust")
    parser.add_argument("--allow-local-http", action="store_true")
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        parser.error("URL must be a credential-free origin")
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and args.allow_local_http
    if parsed.scheme != "https" and not local_http:
        parser.error("HTTPS is required; HTTP is allowed only for an explicitly isolated localhost test")
    context = ssl.create_default_context(cafile=args.ca_file)
    opener = build_opener(HTTPSHandler(context=context))
    report = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "automated_release_candidate_checks", "trust": "custom_test_ca" if args.ca_file else "public_ca" if parsed.scheme == "https" else "local_http_test", "checks": []}
    responses = {}
    for name, path in (("api", "/api/health/ready"), ("configuration", "/api/system/readiness"), ("data", "/api/system/data-readiness"), ("sso_configuration", "/api/auth/session")):
        try:
            with opener.open(Request(args.url.rstrip("/") + path, headers={"User-Agent": "heatshift-release-audit/1", "Accept": "application/json"}), timeout=10) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("Unexpected response shape")
            responses[name] = payload
            passed = {"api": payload.get("status") == "ready", "configuration": payload.get("configuration_ready") is True,
                      "data": payload.get("data_ready") is True, "sso_configuration": payload.get("mode") == "oidc" and payload.get("configured") is True}[name]
            report["checks"].append({"code": name, "passed": passed})
        except (OSError, ValueError):
            report["checks"].append({"code": name, "passed": False, "reason": "endpoint_unavailable_or_invalid_response"})
    report["automated_checks_passed"] = all(item["passed"] for item in report["checks"])
    report["configuration_blockers"] = responses.get("configuration", {}).get("blockers", [])
    report["data_checks"] = responses.get("data", {}).get("checks", [])
    report["external_verification_required_th"] = responses.get("data", {}).get("external_verification_required_th", [])
    report["limitations_th"] = "ผลตรวจอัตโนมัติไม่แทนการทดสอบ SSO จริง การตรวจภาพดาวเทียม สิทธิ์ข้อมูล และการซ้อมระบบปฏิบัติการบนเซิร์ฟเวอร์จริง"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("ผลตรวจอัตโนมัติ: " + ("ผ่าน" if report["automated_checks_passed"] else "ยังมีรายการต้องจัดการ"))
    for item in report["checks"]:
        print(("PASS " if item["passed"] else "BLOCKED ") + item["code"])
    print(report["limitations_th"])
    return 0 if report["automated_checks_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
