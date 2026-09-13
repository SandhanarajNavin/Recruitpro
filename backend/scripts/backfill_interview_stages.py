"""Reconcile application stages with interviews that were already booked.

Scheduling an interview did not originally advance the application, so any
interview booked before that changed left its candidate sitting at an earlier
stage. The pipeline chart counts applications by stage, so those bookings were
invisible on the dashboard.

This walks each affected application forward to the interview stage, recording an
event for every step — the same path a new booking now takes. Applications already
at or past interview are left alone; nobody is moved backwards.

    python -m scripts.backfill_interview_stages [--apply]

Without ``--apply`` it only reports what it would do.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Application, ApplicationStage, Candidate, Interview, Job
from app.db.models.application import PIPELINE_ORDER, InterviewOutcome
from app.services.interview_service import advance_to_interview

#: A cancelled interview is not evidence the candidate reached the stage.
COUNTS_AS_REACHED = (
    InterviewOutcome.SCHEDULED.value,
    InterviewOutcome.COMPLETED.value,
    InterviewOutcome.NO_SHOW.value,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    args = parser.parse_args()

    order = list(PIPELINE_ORDER)
    target = order.index(ApplicationStage.INTERVIEW)
    session = SessionLocal()
    moved = skipped = 0

    rows = session.execute(
        select(Application, Candidate, Job, Interview)
        .join(Interview, Interview.application_id == Application.id)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Job, Job.id == Application.job_id)
        .where(Interview.outcome.in_(COUNTS_AS_REACHED))
    ).all()

    seen: set = set()
    for application, candidate, job, _interview in rows:
        if application.id in seen:
            continue
        seen.add(application.id)

        try:
            current = order.index(ApplicationStage(application.stage))
        except ValueError:
            # Rejected, or otherwise off the main line.
            print(f"  skip  {candidate.full_name} — stage {application.stage!r}")
            skipped += 1
            continue

        if current >= target:
            skipped += 1
            continue

        label = f"{candidate.full_name} / {job.title[:30]}"
        if not args.apply:
            print(f"  would move  {label}: {application.stage} -> interview")
            moved += 1
            continue

        if advance_to_interview(session, job.owner_id, application):
            print(f"  moved  {label}: -> interview")
            moved += 1
        else:
            print(f"  stuck  {label}: still {application.stage}")
            skipped += 1

    verb = "moved" if args.apply else "would move"
    print(f"\n{verb} {moved}, skipped {skipped}")
    if not args.apply and moved:
        print("Re-run with --apply to write these changes.")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
