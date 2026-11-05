#!/usr/bin/env python3
"""Validate rendered production Compose JSON without printing secret values."""
import ipaddress
import json
import sys
from urllib.parse import unquote, urlparse


def inspect(config):
    failures = []
    services = config.get("services", {})

    def check(condition, message):
        if not condition:
            failures.append(message)

    for name, service in services.items():
        check(name == "caddy" or not service.get("ports"), f"{name}: unexpected published port")
    check(set(services) >= {"api", "web", "worker", "dispatcher", "beat", "postgres", "redis", "caddy", "migrate"}, "Production services are missing")
    for name in ("api", "worker", "dispatcher", "beat", "migrate"):
        service = services.get(name, {})
        env = service.get("environment", {})
        check(env.get("ENVIRONMENT") == "production", f"{name}: production mode is required")
        for setting in ("DEMO_MODE", "ALLOW_FIXTURE_INGESTION"):
            check(str(env.get(setting)).lower() == "false", f"{name}: {setting} must be false")
        check(env.get("ADMIN_AUTH_MODE") == "oidc" and not env.get("ADMIN_API_KEY"), f"{name}: OIDC is required")
        check(str(env.get("RATE_LIMIT_ENABLED")).lower() == "true", f"{name}: rate limits must be enabled")
        check("minio-init" not in service.get("depends_on", {}), f"{name}: local storage dependency remains")
        check(not service.get("volumes"), f"{name}: unexpected development data mount")
        check(bool(service.get("secrets")), f"{name}: credential secret mount is missing")
    api = services.get("api", {}).get("environment", {})
    web = services.get("web", {}).get("environment", {})
    check(web.get("ENVIRONMENT") == "production", "web: production deployment mode is required")
    check(web.get("ADMIN_AUTH_MODE") == "oidc", "web: OIDC is required")
    check(not web.get("ADMIN_API_KEY") and not web.get("DASHBOARD_ADMIN_TOKEN"), "web: local admin credentials remain")
    check(str(web.get("NEXT_PUBLIC_DEMO_MODE")).lower() == "false", "web: demo mode must be false")
    check(not web.get("NEXT_PUBLIC_API_URL"), "web: browser API must use the same HTTPS origin")
    secret = web.get("AUTH_SESSION_SECRET", "")
    check(len(secret) >= 32 and "change_me" not in secret.lower(), "web: replace the session encryption placeholder")
    password = services.get("postgres", {}).get("environment", {}).get("POSTGRES_PASSWORD", "")
    check(len(password) >= 24 and "change_me" not in password.lower(), "postgres: replace the password placeholder")
    database = urlparse(api.get("DATABASE_URL", ""))
    check(unquote(database.password or "") == password, "postgres: password differs from DATABASE_URL")
    check(database.hostname == "postgres", "postgres: expected the internal database hostname")
    for key in ("OIDC_ISSUER", "OIDC_JWKS_URL"):
        check(urlparse(api.get(key, "")).scheme == "https", f"api: {key} must use HTTPS")
    check(web.get("OIDC_ISSUER") == api.get("OIDC_ISSUER"), "SSO issuers differ")
    check(bool(web.get("OIDC_CLIENT_ID")) and "change_me" not in web.get("OIDC_CLIENT_ID", "").lower(), "web: replace the SSO client placeholder")
    origin = web.get("APP_ORIGIN", "")
    parsed_origin = urlparse(origin)
    check(parsed_origin.scheme == "https" and bool(parsed_origin.hostname) and not parsed_origin.path, "web: exact HTTPS APP_ORIGIN is required")
    try:
        check(json.loads(api.get("CORS_ORIGINS", "null")) == [origin], "api: CORS must match APP_ORIGIN")
    except (ValueError, TypeError):
        check(False, "api: invalid CORS JSON")
    map_style = services.get("web", {}).get("build", {}).get("args", {}).get("NEXT_PUBLIC_MAP_STYLE_URL", "")
    check(urlparse(map_style).scheme == "https", "web: an approved HTTPS basemap style is required")
    check(bool(api.get("GEE_PROJECT")) and "change_me" not in api.get("GEE_PROJECT", "").lower(), "api: replace the Earth Engine project placeholder")
    try:
        address = ipaddress.ip_address(api.get("FORWARDED_ALLOW_IPS", ""))
        caddy_address = services.get("caddy", {}).get("networks", {}).get("ingress", {}).get("ipv4_address")
        check(set(services.get("caddy", {}).get("networks", {})) == {"ingress"}, "caddy: use only the ingress network to preserve trusted proxy routing")
        check("ingress" in services.get("web", {}).get("networks", {}), "web: ingress network is missing")
        subnet = config["networks"]["ingress"]["ipam"]["config"][0]["subnet"]
        check(str(address) == caddy_address and address in ipaddress.ip_network(subnet), "api: trusted proxy must match the isolated Caddy address")
        allocation = ipaddress.ip_network(config["networks"]["ingress"]["ipam"]["config"][0]["ip_range"])
        check(allocation.subnet_of(ipaddress.ip_network(subnet)) and address not in allocation, "api: dynamic allocation must exclude the reserved proxy address")
    except (ValueError, KeyError, TypeError):
        check(False, "api: invalid trusted proxy network")
    return failures


def main():
    try:
        config = json.load(sys.stdin)
        failures = inspect(config)
    except (ValueError, TypeError):
        print("No valid rendered Compose configuration received", file=sys.stderr)
        return 2
    for failure in failures:
        print("BLOCKED: " + failure, file=sys.stderr)
    if failures:
        return 2
    print("Production Compose configuration checks passed (configuration only; providers and deployment are unverified).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
