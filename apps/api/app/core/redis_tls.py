"""Celery needs explicit SSL settings for both its Redis transport and backend."""
import ssl
from urllib.parse import parse_qs, urlsplit


def celery_redis_tls_settings(url: str) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "rediss":
        return {}
    query = parse_qs(parsed.query, keep_blank_values=True)
    # URL parameters override Celery SSL dictionaries. Reject a downgrade before
    # constructing a connection, including contradictory duplicate parameters.
    if any(value.lower() not in ("required", "cert_required", "2") for value in query.get("ssl_cert_reqs", [])):
        raise ValueError("Redis TLS requires certificate verification")
    if any(value.lower() not in ("true", "1", "yes") for value in query.get("ssl_check_hostname", [])):
        raise ValueError("Redis TLS requires hostname verification")
    options = {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_check_hostname": True}
    return {"broker_use_ssl": dict(options), "redis_backend_use_ssl": dict(options)}
