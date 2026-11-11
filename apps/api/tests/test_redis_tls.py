import ssl

from celery import Celery
import pytest

from app.core.redis_tls import celery_redis_tls_settings


def test_private_compose_redis_retains_its_plaintext_transport():
    assert celery_redis_tls_settings("redis://redis:6379/0") == {}


def test_managed_redis_configures_broker_and_backend_without_connecting():
    url = "rediss://default:isolated-test@db.example.org:6380/0"
    with Celery("isolated-tls-test", broker=url, backend=url) as app:
        app.conf.update(**celery_redis_tls_settings(url))
        with app.connection_for_write() as connection:
            assert connection.ssl["ssl_cert_reqs"] == ssl.CERT_REQUIRED
            assert connection.ssl["ssl_check_hostname"] is True
        assert app.backend.connparams["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        assert app.backend.connparams["ssl_check_hostname"] is True
        assert app.backend.connparams["host"] == "db.example.org"


@pytest.mark.parametrize("query", [
    "ssl_cert_reqs=none", "ssl_cert_reqs=optional", "ssl_check_hostname=false",
    "ssl_check_hostname=0", "ssl_cert_reqs=required&ssl_cert_reqs=none", "ssl_cert_reqs=",
])
def test_managed_redis_rejects_url_tls_downgrades(query):
    with pytest.raises(ValueError):
        celery_redis_tls_settings("rediss://db.example.org/0?" + query)


def test_celery_reopens_connections_the_managed_redis_closed():
    from app.celery_config import celery_settings
    from app.services.locks import MONITOR_HARD_TIME_LIMIT_SECONDS
    options = celery_settings()
    transport = options["broker_transport_options"]
    assert transport["health_check_interval"] > 0 and transport["socket_keepalive"] is True
    assert transport["visibility_timeout"] > MONITOR_HARD_TIME_LIMIT_SECONDS
    assert options["redis_backend_health_check_interval"] > 0
