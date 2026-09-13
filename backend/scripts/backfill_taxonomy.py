"""Backfill role classification and canonical skill/industry edges.

Migration 0002 adds the taxonomy tables and the role columns, but a schema migration
cannot populate them: the values are derived, and deriving them means running the
classifier and the normaliser over every profile that already exists. Without this,
rows ingested before 0002 have no skill edges and no primary role, and every query
built on the new tables quietly under-reports.

Idempotent — the sync helpers replace a candidate's edges wholesale, so running this
twice is the same as running it once.

    python scripts/backfill_taxonomy.py            # report what would change
    python scripts/backfill_taxonomy.py --apply    # write it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.ai import classifier, skill_normalizer  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.database import session_scope  # noqa: E402
from app.db.models import CandidateProfile  # noqa: E402

logger = get_logger("backfill")


def latest_profiles(session):
    """One profile per candidate — the current version, which is what search reads."""
    rows = session.execute(
        select(CandidateProfile).order_by(
            CandidateProfile.candidate_id, CandidateProfile.version.desc()
        )
    ).scalars().all()
    seen: set = set()
    latest = []
    for profile in rows:
        if profile.candidate_id in seen:
            continue
        seen.add(profile.candidate_id)
        latest.append(profile)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    configure_logging()
    session = session_scope()
    try:
        profiles = latest_profiles(session)
        logger.info("%s candidate profile(s) to process.", len(profiles))

        classified = skills_total = industries_total = 0
        for profile in profiles:
            experience_titles = [
                str(item.get("title") or "") for item in (profile.experience or [])
            ]
            result = classifier.classify(
                current_title=profile.current_title,
                skills=list(profile.skills or []),
                experience_titles=experience_titles,
            )

            if args.apply:
                profile.primary_role = result.primary_role
                profile.secondary_roles = result.secondary_roles
                profile.role_confidence = result.confidence
                profile.classifier_model = result.engine
                skills_total += skill_normalizer.sync_candidate_skills(
                    session, profile.candidate_id, list(profile.skills or [])
                )
                industries_total += skill_normalizer.sync_candidate_industries(
                    session, profile.candidate_id, list(profile.domains or [])
                )

            if result.primary_role:
                classified += 1
            logger.info(
                "  %-42s -> %-20s %s (conf %.2f)",
                (profile.current_title or "(no title)")[:42],
                result.primary_role or "UNCLASSIFIED",
                result.secondary_roles or "",
                result.confidence,
            )

        if args.apply:
            session.commit()
            logger.info(
                "Applied. %s/%s classified, %s skill edges, %s industry edges.",
                classified, len(profiles), skills_total, industries_total,
            )
        else:
            logger.info(
                "Dry run. %s/%s would be classified. Re-run with --apply to write.",
                classified, len(profiles),
            )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
