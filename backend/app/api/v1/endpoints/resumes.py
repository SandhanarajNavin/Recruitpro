"""Resume upload and ingestion status (architecture doc §16)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import Candidate, Resume, User
from app.schemas import RejectedUpload, ResumeOut, ResumeStatusOut, UploadAccepted
from app.services import audit_service
from app.services.resume_service import UploadRejected, accept_upload
from app.storage import get_storage
from app.workers.dispatch import enqueue_resume

router = APIRouter(prefix="/resumes", tags=["resumes"])

MAX_FILES_PER_REQUEST = 50


@router.post("/upload", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED)
def upload_resumes(
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> UploadAccepted:
    """Accept one or many resumes and return immediately.

    A rejected file does not fail the batch: a recruiter dragging in thirty CVs
    should not lose twenty-nine of them because one was a scanned image.
    """
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No files were uploaded.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Upload at most {MAX_FILES_PER_REQUEST} files per request.",
        )

    accepted: list[ResumeOut] = []
    rejected: list[RejectedUpload] = []

    for upload in files:
        filename = upload.filename or "resume"
        try:
            data = upload.file.read()
            resume = accept_upload(
                session,
                owner_id=user.id,
                filename=filename,
                content_type=upload.content_type or "",
                data=data,
            )
            enqueue_resume(resume.id)
            session.refresh(resume)
            accepted.append(ResumeOut.model_validate(resume))
        except UploadRejected as exc:
            session.rollback()
            rejected.append(RejectedUpload(filename=filename, reason=str(exc)))
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            rejected.append(
                RejectedUpload(filename=filename, reason=f"Could not store the file: {exc}")
            )
        finally:
            upload.file.close()

    if not accepted and rejected:
        # Nothing was stored, so 202 would be a lie.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "message": "No file could be accepted.",
                "rejected": [r.model_dump() for r in rejected],
            },
        )

    return UploadAccepted(accepted=accepted, rejected=rejected)


@router.get("/{resume_id}/status", response_model=ResumeStatusOut)
def resume_status(
    resume_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ResumeStatusOut:
    resume = session.execute(
        select(Resume).join(Candidate).where(
            Resume.id == resume_id, Candidate.owner_id == user.id
        )
    ).scalars().first()
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")
    return ResumeStatusOut.model_validate(resume)


@router.get("/{resume_id}/download")
def download_resume(
    resume_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """Stream the original file after an ownership check.

    The bucket has no public read path — this endpoint is the only way to the bytes,
    which is what keeps candidate documents off the open internet (doc §19).
    """
    resume = session.execute(
        select(Resume).join(Candidate).where(
            Resume.id == resume_id, Candidate.owner_id == user.id
        )
    ).scalars().first()
    if resume is None:
        # Either it does not exist or it belongs to someone else. Both are recorded
        # as a denied access; the response does not distinguish them (doc §6).
        audit_service.record_denied(
            session, recruiter_id=user.id, resource_type="resume", resource_id=resume_id
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")

    try:
        data = get_storage().get(resume.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status.HTTP_410_GONE, "The stored file is no longer available."
        ) from exc

    audit_service.record(
        session,
        "resume.downloaded",
        recruiter_id=user.id,
        resource_type="resume",
        resource_id=resume.id,
        candidate_id=resume.candidate_id,
        size_bytes=resume.size_bytes,
    )
    return Response(
        content=data,
        media_type=resume.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{resume.original_filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/{resume_id}/reprocess", response_model=ResumeStatusOut)
def reprocess_resume(
    resume_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ResumeStatusOut:
    """Re-run ingestion for one resume.

    The reason resume files are retained rather than overwritten: when the parsing or
    embedding model changes, the original document is still there to re-read.
    """
    resume = session.execute(
        select(Resume).join(Candidate).where(
            Resume.id == resume_id, Candidate.owner_id == user.id
        )
    ).scalars().first()
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found.")

    resume.status = "queued"
    resume.error = None
    session.commit()
    enqueue_resume(resume.id)
    session.refresh(resume)
    return ResumeStatusOut.model_validate(resume)
