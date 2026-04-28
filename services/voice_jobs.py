"""In-memory job queue for the voice agent.

Why this exists: Twilio's webhook timeout is ~15 seconds. The full
caller-turn pipeline (download recording → ffmpeg → Deepgram → Claude
with tool use → ElevenLabs) frequently exceeds that, especially when
Claude chains multiple tool calls (cancel → check slots → book). When
the webhook overruns, Twilio plays "an application error has occurred"
and drops the call.

Fix: process the turn in a background thread, return TwiML immediately
that redirects Twilio to a poll endpoint. The poll endpoint blocks for
up to ~10s waiting on the job; if it's not ready it redirects to itself
again. The call stays alive across as many poll rounds as needed.

Jobs live in memory only — they're tied to a single live call and have
no value across process restarts.
"""

import logging
import threading
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Cap how long a job can sit in memory before we consider it abandoned.
# Real calls never exceed ~5 min; anything older is dead state from a
# crashed call.
_MAX_JOB_AGE_SECONDS = 300

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _cleanup_old() -> None:
    """Drop jobs older than _MAX_JOB_AGE_SECONDS. Best-effort."""
    cutoff = time.time() - _MAX_JOB_AGE_SECONDS
    dead = [k for k, v in _jobs.items() if v["created"] < cutoff]
    for k in dead:
        _jobs.pop(k, None)


def submit(func: Callable[..., Any], *args, **kwargs) -> str:
    """Queue `func(*args, **kwargs)` on a background thread, return job_id.

    The result (or exception) lands in the job's `result`/`error` fields
    once the thread finishes. Use `wait` to block on it.
    """
    job_id = uuid.uuid4().hex[:16]
    event = threading.Event()
    with _lock:
        _cleanup_old()
        _jobs[job_id] = {
            "created": time.time(),
            "event": event,
            "result": None,
            "error": None,
        }

    def _run() -> None:
        try:
            result = func(*args, **kwargs)
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["result"] = result
        except Exception as e:
            logger.error("Voice job %s failed: %s", job_id, e, exc_info=True)
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["error"] = str(e)
        finally:
            event.set()

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def wait(job_id: str, timeout: float) -> tuple[bool, Any, str | None]:
    """Block up to `timeout` seconds for the job to finish.

    Returns (done, result, error). If `done` is False the caller should
    poll again. If `done` is True and `error` is set, the job raised.
    """
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return True, None, "unknown job"
    finished = job["event"].wait(timeout=timeout)
    if not finished:
        return False, None, None
    with _lock:
        job = _jobs.pop(job_id, None)
    if job is None:
        return True, None, "job evicted"
    return True, job["result"], job["error"]


def exists(job_id: str) -> bool:
    with _lock:
        return job_id in _jobs
