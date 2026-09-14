"""Env parsing for the list-valued settings.

These exist because of a production failure, not a hypothetical. Setting
CORS_ORIGINS to the value its own documentation shows —
`CORS_ORIGINS=https://app.example.com` — raised SettingsError during import and the
container exited before binding a port. Cloud Run reported only "failed to start and
listen on the port", which points at the server rather than at a settings field.

pydantic-settings JSON-decodes env values for complex types *before* field
validators run, so `_split_csv` never saw the string. Both fields are annotated
NoDecode to hand it over raw.
"""

from __future__ import annotations

from app.core.config import Settings


class TestCorsOrigins:
    def test_a_single_origin(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
        assert Settings().cors_origins == ["https://app.example.com"]

    def test_a_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,https://b.example.com")
        assert Settings().cors_origins == ["https://a.example.com", "https://b.example.com"]

    def test_surrounding_whitespace_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", " https://a.example.com , https://b.example.com ")
        assert Settings().cors_origins == ["https://a.example.com", "https://b.example.com"]

    def test_empty_entries_are_dropped(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,,")
        assert Settings().cors_origins == ["https://a.example.com"]

    def test_the_default_still_applies_when_unset(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        assert "http://localhost:3000" in Settings().cors_origins


class TestAllowedUploadTypes:
    """Shares _split_csv, so it would have failed the same way."""

    def test_a_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_UPLOAD_TYPES", "application/pdf,text/plain")
        assert Settings().allowed_upload_types == ["application/pdf", "text/plain"]

    def test_a_single_type(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_UPLOAD_TYPES", "application/pdf")
        assert Settings().allowed_upload_types == ["application/pdf"]


class TestImportSurvivesRealisticDeploymentEnv:
    def test_a_cloud_run_style_environment_constructs(self, monkeypatch):
        """The exact shape the deploy sets. A failure here is a container that never
        starts, which is far more expensive to diagnose than this assertion."""
        for key, value in {
            "ENVIRONMENT": "production",
            "API_RELOAD": "false",
            "CORS_ALLOW_LOOPBACK": "false",
            "CORS_ORIGINS": "https://recruitpro-web-abc123-uc.a.run.app",
            "STORAGE_BACKEND": "gcs",
            "GCS_BUCKET": "example-resumes",
            "TASK_ALWAYS_EAGER": "true",
        }.items():
            monkeypatch.setenv(key, value)

        settings = Settings()

        assert settings.environment == "production"
        assert settings.api_reload is False
        assert settings.storage_backend == "gcs"
        assert settings.cors_origins == ["https://recruitpro-web-abc123-uc.a.run.app"]
        # Loopback must be off outside development or the allowlist means nothing.
        assert settings.cors_origin_regex is None
