import asyncio

from app import main


def _run_lifespan():
    async def go():
        async with main.lifespan(main.app):
            pass
    asyncio.run(go())


def test_recycled_worker_skips_the_bootstrap_pass(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(main, "ensure_sources", lambda db: calls.append("sources"))
    monkeypatch.setattr(main, "seed_demo_data", lambda db: calls.append("seed"))
    marker = tmp_path / "api-bootstrapped"
    monkeypatch.setenv("API_BOOTSTRAP_MARKER", str(marker))

    _run_lifespan()
    assert calls == ["sources", "seed"] and marker.exists()

    _run_lifespan()
    assert calls == ["sources", "seed"]


def test_without_a_marker_every_start_bootstraps(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "ensure_sources", lambda db: calls.append("sources"))
    monkeypatch.setattr(main, "seed_demo_data", lambda db: calls.append("seed"))
    monkeypatch.delenv("API_BOOTSTRAP_MARKER", raising=False)

    _run_lifespan()
    _run_lifespan()
    assert calls == ["sources", "seed", "sources", "seed"]


def test_oversized_worker_asks_for_one_graceful_replacement(monkeypatch):
    kills = []
    monkeypatch.setattr(main, "_recycle_requested", False)
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: kills.append(sig))
    monkeypatch.setenv("API_RECYCLE_RSS_MB", "280")
    monkeypatch.setattr(main, "_resident_bytes", lambda: 200 * 1024 * 1024)
    main._recycle_if_oversized()
    assert kills == []
    monkeypatch.setattr(main, "_resident_bytes", lambda: 300 * 1024 * 1024)
    main._recycle_if_oversized()
    main._recycle_if_oversized()
    assert kills == [main.signal.SIGTERM]


def test_no_cap_means_no_recycle(monkeypatch):
    kills = []
    monkeypatch.setattr(main, "_recycle_requested", False)
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: kills.append(sig))
    monkeypatch.delenv("API_RECYCLE_RSS_MB", raising=False)
    monkeypatch.setattr(main, "_resident_bytes", lambda: 900 * 1024 * 1024)
    main._recycle_if_oversized()
    assert kills == []
