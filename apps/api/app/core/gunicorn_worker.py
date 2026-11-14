from uvicorn.workers import UvicornWorker

from app.core.oom import API_WORKER_OOM_SCORE_ADJ, prefer_as_oom_victim


class ApiWorker(UvicornWorker):
    # Gunicorn starts the replacement only after the old worker exits. Open
    # event streams never finish on their own, so without a bound the single
    # worker held shutdown for the full graceful timeout and every request in
    # those 30 s failed at the proxy. Uvicorn cancels whatever is still open
    # after this many seconds; browsers reconnect their event streams.
    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, "timeout_graceful_shutdown": 3}

    def init_process(self):
        # The Gunicorn master keeps the socket and forks a new worker within
        # seconds, so the worker, not a top-level process, should absorb an
        # OOM kill.
        prefer_as_oom_victim(API_WORKER_OOM_SCORE_ADJ)
        super().init_process()
