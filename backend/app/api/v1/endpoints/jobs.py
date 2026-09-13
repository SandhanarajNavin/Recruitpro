"""Job endpoints (architecture doc §16)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.db.database import get_session
from app.db.models import Application, Screening, ScreeningResult, User
from app.parsers import ExtractionError, extract_text
from app.schemas import (
    ExtractedDescription,
    JobCreateRequest,
    JobDetailResponse,
    JobListItem,
    JobOut,
    JobStatsOut,
    JobUpdateRequest,
    RequirementOut,
    SkillCoverageOut,
)
from app.services import analytics_service, job_service
from app.services.scoring import BAND_STRONG_HIRE as STRONG_MATCH_SCORE
from app.utils.file_utils import UploadRejected, validate_upload

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Mirrors JobCreateRequest.description's min_length. An upload that cannot clear the
#: create endpoint's bar should be refused while the filename is still in hand.
MIN_DESCRIPTION_CHARS = 40


@router.post("", response_model=JobDetailResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobDetailResponse:
    """Create a recruitment from a job description.

    Requirements are parsed and returned synchronously so the recruiter can see —
    and correct — what the parser extracted before running a screening.
    """
    job = job_service.create_job(
        session,
        owner_id=user.id,
        title=body.title,
        description=body.description,
        location=body.location,
    )
    return JobDetailResponse(
        job=JobOut.model_validate(job),
        requirement=RequirementOut.model_validate(job.requirement) if job.requirement else None,
    )


@router.post("/extract", response_model=ExtractedDescription)
def extract_description(
    file: UploadFile = File(...),
    _user: User = Depends(current_user),
) -> ExtractedDescription:
    """Read a job description out of a PDF, DOCX or text file.

    Extraction only — no job is created and the file is not stored. The text goes
    back to the recruiter to read and edit, and they create the job from it as if
    they had pasted it. That ordering is the point: PDF extraction interleaves
    two-column layouts and flattens tables, and the resulting requirements and JD
    vector are only as good as the text they came from.

    Unlike a resume, a JD has no candidate to belong to and nothing later re-reads
    the original, so there is nothing to retain.
    """
    filename = file.filename or "job-description"
    try:
        data = file.file.read()
        validate_upload(filename, file.content_type or "", data)
        text = extract_text(data, file.content_type or "", filename)
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{filename}: {exc}") from exc
    except ExtractionError as exc:
        # Named, because the recruiter chose a file rather than filling in a field —
        # and a JD often arrives as one of several attachments on the same mail.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"{filename}: {exc}"
        ) from exc
    finally:
        file.file.close()

    # The create endpoint requires 40 characters. Anything shorter is refused here,
    # while the filename is still in hand, rather than at create against a field the
    # recruiter never typed in. The extractor's own "almost no text" guard catches
    # the emptiest files first; this covers a short but genuine extraction.
    if len(text.strip()) < MIN_DESCRIPTION_CHARS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{filename}: yielded only {len(text.strip())} characters of text — "
            f"a job description needs at least {MIN_DESCRIPTION_CHARS}.",
        )

    return ExtractedDescription(text=text, filename=filename, characters=len(text))


@router.get("", response_model=list[JobListItem])
def list_jobs(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[JobListItem]:
    jobs = job_service.list_jobs(session, user.id, limit=200)
    if not jobs:
        return []

    ids = [job.id for job in jobs]

    # Three grouped queries for the whole page rather than three per row.
    matched = dict(
        session.execute(
            select(
                Screening.job_id,
                func.count(func.distinct(ScreeningResult.candidate_id)),
            )
            .join(ScreeningResult, ScreeningResult.screening_id == Screening.id)
            .where(Screening.job_id.in_(ids))
            .group_by(Screening.job_id)
        ).all()
    )
    strong = dict(
        session.execute(
            select(
                Screening.job_id,
                func.count(func.distinct(ScreeningResult.candidate_id)),
            )
            .join(ScreeningResult, ScreeningResult.screening_id == Screening.id)
            .where(
                Screening.job_id.in_(ids),
                # The band, not the recommendation string: an override reflects a
                # recruiter's judgement, and this column reports what the pipeline
                # found.
                ScreeningResult.composite_score >= STRONG_MATCH_SCORE,
            )
            .group_by(Screening.job_id)
        ).all()
    )
    pipeline = dict(
        session.execute(
            select(Application.job_id, func.count(Application.id))
            .where(
                Application.job_id.in_(ids),
                Application.stage.notin_(["rejected"]),
            )
            .group_by(Application.job_id)
        ).all()
    )

    items = []
    for job in jobs:
        item = JobListItem.model_validate(job)
        item.candidate_count = int(matched.get(job.id, 0))
        item.strong_match_count = int(strong.get(job.id, 0))
        item.in_pipeline = int(pipeline.get(job.id, 0))
        items.append(item)
    return items


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobDetailResponse:
    job = job_service.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    return JobDetailResponse(
        job=JobOut.model_validate(job),
        requirement=RequirementOut.model_validate(job.requirement) if job.requirement else None,
        stats=_job_stats(session, job),
        skill_coverage=[
            SkillCoverageOut(**vars(entry))
            for entry in analytics_service.skill_coverage(session, job.id, user.id)
        ],
    )


def _job_stats(session: Session, job) -> JobStatsOut:
    """Header counts for one job. Five scalars, five small queries."""
    matched = session.execute(
        select(func.count(func.distinct(ScreeningResult.candidate_id)))
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .where(Screening.job_id == job.id)
    ).scalar() or 0

    strong = session.execute(
        select(func.count(func.distinct(ScreeningResult.candidate_id)))
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .where(
            Screening.job_id == job.id,
            ScreeningResult.composite_score >= STRONG_MATCH_SCORE,
        )
    ).scalar() or 0

    pipeline = session.execute(
        select(func.count(Application.id)).where(
            Application.job_id == job.id, Application.stage.notin_(["rejected"])
        )
    ).scalar() or 0

    week_ago = datetime.now(UTC) - timedelta(days=7)
    new_this_week = session.execute(
        select(func.count(func.distinct(ScreeningResult.candidate_id)))
        .join(Screening, Screening.id == ScreeningResult.screening_id)
        .where(Screening.job_id == job.id, ScreeningResult.created_at >= week_ago)
    ).scalar() or 0

    latest = session.execute(
        select(Screening.id)
        .where(Screening.job_id == job.id, Screening.status == "completed")
        .order_by(Screening.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    opened = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
    return JobStatsOut(
        candidate_count=int(matched),
        strong_match_count=int(strong),
        in_pipeline=int(pipeline),
        days_open=max((datetime.now(UTC) - opened).days, 0),
        new_this_week=int(new_this_week),
        latest_screening_id=latest,
    )


@router.patch("/{job_id}", response_model=JobDetailResponse)
def update_job(
    job_id: uuid.UUID,
    body: JobUpdateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobDetailResponse:
    """Edit a job, or move it between open / on hold / closed."""
    try:
        job = job_service.update_job(
            session,
            job_id,
            user.id,
            **body.model_dump(exclude_unset=True),
        )
    except job_service.JobError as error:
        # "Not found" and "not yours" are the same answer on purpose.
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(code, str(error)) from error

    return JobDetailResponse(
        job=JobOut.model_validate(job),
        requirement=RequirementOut.model_validate(job.requirement) if job.requirement else None,
        stats=_job_stats(session, job),
        skill_coverage=[
            SkillCoverageOut(**vars(entry))
            for entry in analytics_service.skill_coverage(session, job.id, user.id)
        ],
    )
