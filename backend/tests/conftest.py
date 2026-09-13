from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    """Force the deterministic engine for every test.

    Without this the suite silently depends on whoever is running it. A developer
    with GOOGLE_CLOUD_PROJECT set makes every parse, evaluation and explanation
    attempt a real Vertex call: the run costs money, needs network, and is not
    reproducible. With the project set but credentials missing — the common
    half-configured state — each call still burns several seconds failing before it
    falls back, which took this suite from 17 seconds to over ten minutes.

    Tests that want to exercise the model path should patch ``llm_available`` back
    on themselves, so the intent is visible at the test rather than ambient.
    """
    from app.ai.embeddings import embedding_service
    from app.core.config import settings
    from app.workers import dispatch

    monkeypatch.setattr(type(settings), "llm_available", property(lambda _self: False))

    # Embedding is a separate switch. `embedding_provider` is read straight from the
    # environment, so a developer with EMBEDDING_PROVIDER=gemini in .env would have
    # the whole suite embedding against Vertex — thousands of calls for the resume
    # fixtures alone, which is exactly what the note above exists to prevent.
    # Forced offline, and the cached embedder reset either side so a real one built
    # by earlier work in the same process cannot leak in.
    monkeypatch.setattr(settings, "embedding_provider", "offline")
    monkeypatch.setattr(settings, "embedding_model", "offline-hashing-v1")

    # Third switch, same reasoning. `.env` now ships TASK_ALWAYS_EAGER=false so bulk
    # uploads are queued rather than run in the request. A developer with a worker
    # running would otherwise have the suite hand ingestion to *that* worker: it has
    # its own session and its own settings, so the test's transaction never sees the
    # result, and the work lands against real Vertex in a container. Tasks run in the
    # test process, which is what every assertion here already assumes.
    monkeypatch.setattr(settings, "task_always_eager", True)
    dispatch.reset_worker_probe()

    embedding_service.reset_embedder()
    yield
    embedding_service.reset_embedder()
    dispatch.reset_worker_probe()
