"""Storage backend selection and the local filesystem implementation.

The GCS backend is not exercised against a real bucket here — that needs credentials
and a network. What is worth pinning without either is the selection logic and the
misconfiguration that would otherwise only surface in a deployed container: on Cloud
Run each instance has its own ephemeral disk, so falling back to local storage means
the worker looks for a file the API wrote on a filesystem it cannot see.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.storage import factory
from app.storage.local import LocalStorage


@pytest.fixture(autouse=True)
def _reset_storage_cache():
    """The factory memoises its backend, so a test that changes the setting has to
    clear it or it silently asserts against the previous test's choice."""
    factory._storage = None
    yield
    factory._storage = None


class TestBackendSelection:
    def test_local_is_the_default(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "local")
        assert isinstance(factory.get_storage(), LocalStorage)

    def test_an_unknown_backend_falls_back_to_local(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "azure-blob")
        assert isinstance(factory.get_storage(), LocalStorage)

    def test_gcs_without_a_bucket_fails_loudly(self, monkeypatch):
        """A deploy that sets the backend but forgets the bucket must not quietly
        write to a container filesystem that disappears with the instance."""
        monkeypatch.setattr(settings, "storage_backend", "gcs")
        monkeypatch.setattr(settings, "gcs_bucket", None)

        with pytest.raises(ValueError, match="GCS_BUCKET"):
            factory.get_storage()

    def test_s3_without_a_bucket_fails_loudly(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "s3")
        monkeypatch.setattr(settings, "s3_bucket", None)

        with pytest.raises(ValueError, match="S3_BUCKET"):
            factory.get_storage()


class TestLocalStorage:
    def test_round_trip(self, tmp_path):
        storage = LocalStorage(root=tmp_path)
        stored = storage.put("c/abc/resume.pdf", b"%PDF-1.4 hello", "application/pdf")

        assert stored.size_bytes == 14
        assert storage.get("c/abc/resume.pdf") == b"%PDF-1.4 hello"

    def test_delete_is_idempotent(self, tmp_path):
        storage = LocalStorage(root=tmp_path)
        storage.put("k/one.txt", b"x", "text/plain")
        storage.delete("k/one.txt")
        storage.delete("k/one.txt")  # already gone is the desired end state

    def test_a_key_cannot_escape_the_root(self, tmp_path):
        """Keys are attacker-influenced: a traversal would read or overwrite files
        outside the upload directory."""
        storage = LocalStorage(root=tmp_path)

        with pytest.raises(ValueError, match="outside the storage root"):
            storage.put("../../etc/passwd", b"x", "text/plain")

    def test_a_missing_key_raises_file_not_found(self, tmp_path):
        """The download endpoint turns this into a 410, and the GCS backend
        translates NotFound into the same exception so both behave alike."""
        storage = LocalStorage(root=tmp_path)

        with pytest.raises(FileNotFoundError):
            storage.get("k/never-written.pdf")


class TestGcsBackendShape:
    def test_it_implements_the_storage_interface(self):
        """Imported without credentials: construction needs a bucket and a client,
        but the class itself must satisfy the interface or the deploy fails at the
        first upload rather than at startup."""
        from app.storage.base import ObjectStorage
        from app.storage.gcs import GcsStorage

        assert issubclass(GcsStorage, ObjectStorage)
        for method in ("put", "get", "delete", "signed_url"):
            assert callable(getattr(GcsStorage, method))
        # No abstract methods left unimplemented — instantiating would raise TypeError.
        assert not getattr(GcsStorage, "__abstractmethods__", frozenset())
