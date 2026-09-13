"""Reading a job description out of an uploaded file.

The endpoint extracts and returns text; it does not create the job. These cover the
three formats a recruiter actually has a JD in, the rejections that must not reach
the parser, and the round trip that proves the extracted text is usable as-is.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import User
from app.main import app


def _database_available() -> bool:
    try:
        engine = create_engine(
            settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2}
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason="Postgres is not reachable — run `docker compose up -d`",
)

JD_LINES = [
    "Senior Platform Engineer",
    "Location: Remote (EU)",
    "",
    "Requirements",
    "- 5+ years of experience building backend services in Python or Go",
    "- Strong PostgreSQL and Kubernetes background, and CI/CD ownership",
    "- Experience with Terraform and AWS is required for this role",
    "",
    "Nice to have",
    "- Kafka, gRPC, or event-driven architecture exposure",
]

JD_TEXT = "\n".join(JD_LINES)


def _pdf_bytes(lines: list[str]) -> bytes:
    """A one-page PDF containing `lines`, assembled by hand.

    Nothing in requirements.txt can write a PDF, and PDF extraction is the main
    thing worth testing here, so the fixture is built from the format itself:
    objects, a byte-offset xref table, and a trailer pointing at it. Verified to
    round-trip through the pypdf reader the extractor uses.
    """

    def escape(value: str) -> str:
        return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    show = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for index, line in enumerate(lines):
        show.append(f"({escape(line)}) Tj" if index == 0 else f"T* ({escape(line)}) Tj")
    show.append("ET")
    stream = "\n".join(show).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n".encode()
    )
    out.write(b"%%EOF\n")
    return out.getvalue()


def _docx_bytes(lines: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def session():
    from app.db.database import session_scope

    db = session_scope()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def recruiter(session):
    user = User(
        name="JD Uploader",
        email=f"jd-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password("test-password"),
    )
    session.add(user)
    session.commit()
    yield user
    session.delete(user)
    session.commit()


@pytest.fixture
def client(recruiter):
    api = TestClient(app)
    token = api.post(
        "/api/v1/auth/login",
        json={"email": recruiter.email, "password": "test-password"},
    ).json()["access_token"]
    api.headers.update({"Authorization": f"Bearer {token}"})
    return api


def _extract(client, filename: str, data: bytes, content_type: str):
    return client.post(
        "/api/v1/jobs/extract",
        files={"file": (filename, data, content_type)},
    )


class TestFormats:
    def test_a_pdf_job_description(self, client):
        response = _extract(client, "jd.pdf", _pdf_bytes(JD_LINES), "application/pdf")

        assert response.status_code == 200, response.text
        body = response.json()
        assert "Senior Platform Engineer" in body["text"]
        assert "Terraform and AWS" in body["text"]
        assert body["filename"] == "jd.pdf"
        assert body["characters"] == len(body["text"])

    def test_a_docx_job_description(self, client):
        response = _extract(
            client,
            "jd.docx",
            _docx_bytes(JD_LINES),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        assert response.status_code == 200, response.text
        assert "Kubernetes" in response.json()["text"]

    def test_a_plain_text_job_description(self, client):
        response = _extract(client, "jd.txt", JD_TEXT.encode("utf-8"), "text/plain")

        assert response.status_code == 200, response.text
        assert "Nice to have" in response.json()["text"]

    def test_no_job_is_created_by_extracting(self, client):
        """Extraction is a read. The recruiter reviews the text and creates the job
        themselves, so a file that was only inspected must leave nothing behind."""
        before = client.get("/api/v1/jobs").json()

        _extract(client, "jd.pdf", _pdf_bytes(JD_LINES), "application/pdf")

        assert client.get("/api/v1/jobs").json() == before


class TestRejections:
    def test_an_unsupported_type_is_refused(self, client):
        response = _extract(client, "jd.exe", b"MZ\x90\x00binary", "application/octet-stream")

        assert response.status_code == 400
        assert "unsupported" in response.text.lower()

    def test_a_file_lying_about_being_a_pdf_is_refused(self, client):
        """The content type is caller-supplied, so the magic bytes are what decide."""
        response = _extract(client, "jd.pdf", b"not really a pdf at all", "application/pdf")

        assert response.status_code == 400
        assert "%PDF" in response.text

    def test_an_empty_file_is_refused(self, client):
        response = _extract(client, "jd.txt", b"", "text/plain")

        assert response.status_code == 400

    def test_too_little_text_is_refused_with_the_filename(self, client):
        """A scanned JD extracts to almost nothing. Failing here can name the file;
        failing later at create would name a field the recruiter never typed in."""
        response = _extract(client, "scanned-jd.txt", b"Engineer", "text/plain")

        assert response.status_code == 422
        assert "scanned-jd.txt" in response.text

    def test_extraction_requires_a_signed_in_recruiter(self):
        anonymous = TestClient(app)

        response = anonymous.post(
            "/api/v1/jobs/extract",
            files={"file": ("jd.txt", JD_TEXT.encode("utf-8"), "text/plain")},
        )

        assert response.status_code in (401, 403)


class TestRoundTrip:
    def test_extracted_text_creates_a_job_with_parsed_requirements(self, client):
        """The point of the endpoint: what comes out of a PDF goes into the normal
        create path unchanged and parses into real requirements."""
        extracted = _extract(client, "jd.pdf", _pdf_bytes(JD_LINES), "application/pdf")
        assert extracted.status_code == 200, extracted.text

        created = client.post(
            "/api/v1/jobs",
            json={"description": extracted.json()["text"], "location": "Remote (EU)"},
        )

        assert created.status_code == 201, created.text
        body = created.json()
        assert body["job"]["description"] == extracted.json()["text"]
        requirement = body["requirement"]
        assert requirement is not None
        skills = {entry["skill"].lower() for entry in requirement["required_skills"]}
        assert skills, "the JD text parsed into no required skills at all"
