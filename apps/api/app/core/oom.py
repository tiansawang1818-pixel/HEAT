"""Steer the kernel OOM killer towards processes a parent replaces in seconds.

When the 768 MiB container reaches its limit the kernel kills the process
with the highest badness score, roughly its share of memory. Right after the
API worker recycles, the Celery dispatcher or the ingestion main process can
be the largest, and losing either ends the supervisor and restarts the whole
container (one to two minutes of the host's 404 page). Gunicorn replaces its
worker and Celery its prefork child within seconds, so those processes raise
their own score. Raising it needs no privilege; lowering it would.
"""
from pathlib import Path

API_WORKER_OOM_SCORE_ADJ = 1000
INGESTION_CHILD_OOM_SCORE_ADJ = 500


def prefer_as_oom_victim(score, path=Path("/proc/self/oom_score_adj")):
    """Raise this process's OOM score; a no-op where /proc is unavailable."""
    try:
        path.write_text(str(score))
        return True
    except OSError:
        return False
