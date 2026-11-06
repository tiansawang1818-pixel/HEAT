#!/usr/bin/env python3
"""Verify release HTTP behavior over TLS; no provider checks or real login."""
import argparse
import json
import ssl
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca-file")
    args = parser.parse_args()
    assert args.url.startswith("https://"), "TLS verification is required"
    context = ssl.create_default_context(cafile=args.ca_file)
    opener = build_opener(HTTPSHandler(context=context))
    base = args.url.rstrip("/")
    for path in ["/", "/insights", "/optimizer", "/scenarios", "/outlook", "/observatory", "/methodology", "/api/health/ready", "/api/map/layers", "/api/auth/session", "/api/system/data-readiness"]:
        with opener.open(base + path, timeout=15) as response:
            assert response.status == 200, path
            content_type, body = response.headers.get("Content-Type", ""), response.read()
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert "max-age=" in response.headers.get("Strict-Transport-Security", "")
            if path.startswith("/api/"):
                assert "application/json" in content_type and json.loads(body) is not None, path
            else:
                assert "text/html" in content_type and b"HEAT//SHIFT" in body, path
    with opener.open(base + "/api/auth/session", timeout=5) as response:
        session = json.load(response)
        assert session["mode"] == "oidc" and session["configured"] and not session["authenticated"]
    request = Request(base + "/api/admin/ingestion", data=b'{"source":"open-meteo"}', headers={"Content-Type": "application/json"})
    try:
        opener.open(request, timeout=5)
        raise AssertionError("Unauthenticated admin operation was accepted")
    except HTTPError as error:
        assert error.code == 401
    try:
        opener.open(base + "/api/auth/callback?state=invalid&code=invalid", timeout=5)
        raise AssertionError("Invalid login callback was accepted")
    except HTTPError as error:
        assert error.code == 401
        cookie = error.headers.get("Set-Cookie", "")
        assert "__Host-heatshift-login=" in cookie and "Secure" in cookie and "HttpOnly" in cookie
    with opener.open(base + "/api/events/stream", timeout=10) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert "no-transform" in response.headers.get("Cache-Control", "")
        assert response.readline().strip() == b"retry: 5000"
        assert response.readline().strip() == b": connected"
    with opener.open(base + "/api/system/data-readiness", timeout=5) as response:
        report = json.load(response)
        assert report["scope"] == "published_data_and_recorded_decision_evidence"
        assert not report["data_ready"], "CI has no real provider data and must remain blocked"
    print("All pages, admin gates, secure cookies, data readiness and SSE verified over TLS with hostname/certificate validation.")


if __name__ == "__main__":
    main()
