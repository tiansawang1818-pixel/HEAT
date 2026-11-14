from app.core import oom
from app.core.gunicorn_worker import ApiWorker


def test_prefer_as_oom_victim_writes_score_and_tolerates_missing_proc(tmp_path):
    target = tmp_path / "oom_score_adj"
    assert oom.prefer_as_oom_victim(700, target) is True
    assert target.read_text() == "700"
    assert oom.prefer_as_oom_victim(700, tmp_path / "missing" / "oom_score_adj") is False


def test_api_worker_raises_its_score_before_serving(monkeypatch):
    import app.core.gunicorn_worker as module
    calls = []
    monkeypatch.setattr(module, "prefer_as_oom_victim", lambda score: calls.append(score))
    monkeypatch.setattr(module.UvicornWorker, "init_process", lambda self: calls.append("serve"))
    ApiWorker.init_process(object.__new__(ApiWorker))
    assert calls == [oom.API_WORKER_OOM_SCORE_ADJ, "serve"]


def test_only_prefork_children_raise_their_score(monkeypatch):
    import app.worker as worker
    from celery.signals import worker_process_init
    calls = []
    monkeypatch.setattr(worker, "prefer_as_oom_victim", lambda score: calls.append(score))
    worker_process_init.send(sender=None)
    assert calls == [oom.INGESTION_CHILD_OOM_SCORE_ADJ]
    # Top-level processes, whose exit restarts the container, keep the default.
    assert oom.API_WORKER_OOM_SCORE_ADJ == oom.INGESTION_CHILD_OOM_SCORE_ADJ == 1000
