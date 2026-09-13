"""Search-quality evaluation (architecture doc §27).

Runs the golden set through one or more funnel stages and prints ranking metrics, so
a change can be shown to help rather than assumed to.

    python scripts/evaluate_search.py                     # cheap stages, no model calls
    python scripts/evaluate_search.py --stage full        # includes LLM evaluation
    python scripts/evaluate_search.py --detail            # per-job rankings
    python scripts/evaluate_search.py --json out.json     # machine-readable, for diffing

Run it before and after a change and compare. The three cheap stages cost nothing and
answer "did retrieval get better"; `full` costs a model call per candidate per job.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.database import session_scope  # noqa: E402
from app.evaluation.harness import (  # noqa: E402
    STAGES,
    GoldenSetError,
    load_golden,
    run_stage,
)

logger = get_logger("evaluate")

#: Printed in this order so the columns line up across stages.
COLUMNS = ("p@3", "r@3", "ndcg@3", "p@5", "r@5", "ndcg@5", "mrr")


def print_stage(result, detail: bool) -> None:
    print(f"\n─── {result.stage} " + "─" * (58 - len(result.stage)))

    if detail:
        for job in result.per_job:
            relevant = sum(1 for g in job.relevance.values() if g >= 1)
            print(f"\n  {job.job_title}   ({relevant} relevant in the golden set)")
            if job.note:
                print(f"    ! {job.note}")
            for position, (cid, name) in enumerate(
                zip(job.ranked[:5], job.ranked_names[:5], strict=False), start=1
            ):
                grade = job.relevance.get(cid, 0)
                mark = {2: "++", 1: " +", 0: " ."}[grade]
                print(f"    {position}. {mark}  {name}")
            print(
                "       "
                + "  ".join(f"{key}={job.scores.get(key, 0):.2f}" for key in COLUMNS)
            )
        print()

    print("  " + "  ".join(f"{key:>7}" for key in COLUMNS))
    print("  " + "  ".join(f"{result.overall.get(key, 0):7.3f}" for key in COLUMNS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        action="append",
        choices=STAGES,
        help="Stage to evaluate; repeatable. Defaults to the three cheap stages.",
    )
    parser.add_argument("--golden", type=Path, help="Path to the golden set.")
    parser.add_argument("--detail", action="store_true", help="Show per-job rankings.")
    parser.add_argument("--json", type=Path, help="Write results here for diffing.")
    args = parser.parse_args()

    configure_logging()
    stages = args.stage or ["vector", "hybrid", "rerank"]

    try:
        golden = load_golden(args.golden)
    except GoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    recruiter = golden.get("recruiter")
    if not recruiter:
        print("error: the golden set needs a top-level `recruiter:` email.", file=sys.stderr)
        return 1

    jobs = golden.get("jobs", [])
    judgements = sum(len(entry.get("candidates", [])) for entry in jobs)
    print(f"golden set: {len(jobs)} jobs, {judgements} judgements, recruiter {recruiter}")

    session = session_scope()
    payload: dict[str, dict] = {}
    try:
        for stage in stages:
            try:
                result = run_stage(
                    session, stage=stage, golden=golden, recruiter_email=recruiter
                )
            except GoldenSetError as exc:
                print(f"\nerror in stage {stage}: {exc}", file=sys.stderr)
                return 1
            print_stage(result, args.detail)
            payload[stage] = {
                "overall": result.overall,
                "jobs": {
                    job.job_title: {"scores": job.scores, "ranked": job.ranked_names}
                    for job in result.per_job
                },
            }
    finally:
        session.close()

    if args.json:
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    print(
        "\nReading these: p@k is how much of the top k is useful, r@k how much of the "
        "\nuseful was found, ndcg@k whether strong matches outrank partial ones, and "
        "\nmrr how far a recruiter scrolls before the first good candidate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
