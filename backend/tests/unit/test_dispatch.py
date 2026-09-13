"""Queue dispatch and its fallback.

The dangerous failure is not a broker outage — that raises, and the existing
try/except catches it. It is a healthy broker with nothing consuming: ``.delay()``
succeeds, the task sits in Redis, and the resume stays "queued" while the UI polls
it forever. Nothing raises, so these tests exist to prove something still notices.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from app.workers import dispatch


class _FakeTask:
    """Stands in for a Celery task, recording whether it was ever queued."""

    def __init__(self, task_id: str = "task-1") -> None:
        self.task_id = task_id
        self.calls: list[str] = []

    def delay(self, argument: str):
        self.calls.append(argument)
        return type("Result", (), {"id": self.task_id})()


class _Recorder:
    def __init__(self) -> None:
        self.runs = 0

    def __call__(self) -> None:
        self.runs += 1


@pytest.fixture
def queued(monkeypatch):
    """Not eager, and the probe cache cleared either side."""
    monkeypatch.setattr(settings, "task_always_eager", False)
    dispatch.reset_worker_probe()
    yield
    dispatch.reset_worker_probe()


def _patch_ping(monkeypatch, result, *, counter: list[int] | None = None):
    def ping(*_args, **_kwargs):
        if counter is not None:
            counter.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.control, "ping", ping)


class TestWorkerProbe:
    def test_eager_mode_never_probes(self, monkeypatch):
        """In eager mode .delay() runs the task in the caller, so a broker round trip
        would be latency for an answer that is already known."""
        monkeypatch.setattr(settings, "task_always_eager", True)
        dispatch.reset_worker_probe()
        pings: list[int] = []
        _patch_ping(monkeypatch, [{"celery@host": {"ok": "pong"}}], counter=pings)

        assert dispatch._workers_available() is True
        assert pings == [], "eager mode should not touch the broker"

    def test_a_reply_means_available(self, monkeypatch, queued):
        _patch_ping(monkeypatch, [{"celery@host": {"ok": "pong"}}])
        assert dispatch._workers_available() is True

    def test_no_reply_means_unavailable(self, monkeypatch, queued):
        # A live broker with nothing consuming answers an empty list, not an error.
        _patch_ping(monkeypatch, [])
        assert dispatch._workers_available() is False

    def test_an_unreachable_broker_counts_as_no_workers(self, monkeypatch, queued):
        _patch_ping(monkeypatch, OSError("connection refused"))
        assert dispatch._workers_available() is False

    def test_the_probe_is_cached_across_a_bulk_upload(self, monkeypatch, queued):
        """Dispatch happens once per file. Twenty resumes must not mean twenty pings."""
        pings: list[int] = []
        _patch_ping(monkeypatch, [{"celery@host": {"ok": "pong"}}], counter=pings)

        for _ in range(20):
            assert dispatch._workers_available() is True

        assert len(pings) == 1, f"probed {len(pings)} times for one batch"


class TestDispatch:
    def test_work_is_queued_when_a_worker_is_listening(self, monkeypatch, queued):
        _patch_ping(monkeypatch, [{"celery@host": {"ok": "pong"}}])
        task, inline = _FakeTask(), _Recorder()

        route = dispatch._dispatch(task, uuid.uuid4(), inline, "resume ingestion")

        assert route == "task-1"
        assert len(task.calls) == 1
        assert inline.runs == 0, "queued work must not also run in the request"

    def test_work_runs_inline_when_nothing_is_listening(self, monkeypatch, queued):
        """The whole point: without this the task is published and never consumed."""
        _patch_ping(monkeypatch, [])
        task, inline = _FakeTask(), _Recorder()

        route = dispatch._dispatch(task, uuid.uuid4(), inline, "resume ingestion")

        assert route == "inline"
        assert inline.runs == 1
        assert task.calls == [], "must not publish a task no worker will take"

    def test_a_broker_that_raises_on_publish_still_falls_back(self, monkeypatch, queued):
        _patch_ping(monkeypatch, [{"celery@host": {"ok": "pong"}}])
        inline = _Recorder()

        class _Exploding(_FakeTask):
            def delay(self, argument: str):
                raise OSError("broker went away between the probe and the publish")

        route = dispatch._dispatch(_Exploding(), uuid.uuid4(), inline, "screening")

        assert route == "inline"
        assert inline.runs == 1
