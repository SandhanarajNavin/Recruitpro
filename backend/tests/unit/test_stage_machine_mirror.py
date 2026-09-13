"""The frontend's copy of the stage machine must match this one.

`lib/types.ts` mirrors ``application_service.ALLOWED`` so a stage picker can offer
only moves that will succeed instead of letting the recruiter choose one and get a
409. A mirror that silently drifts is worse than no mirror: the picker would offer a
move the server refuses, or hide one it would have accepted. This reads the TypeScript
and compares, so the duplication cannot rot unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.db.models import ApplicationStage
from app.services.application_service import ALLOWED

TYPES_TS = Path(__file__).resolve().parents[3] / "frontend" / "lib" / "types.ts"


def _parse_stage_moves(source: str) -> dict[str, set[str]]:
    """Pull STAGE_MOVES out of the TypeScript.

    A small regex rather than a JS parser: the constant is a flat literal of
    ``key: ["a", "b"],`` lines, and anything more elaborate in there should fail
    this test loudly rather than be quietly accepted.
    """
    block = re.search(
        r"export const STAGE_MOVES: Record<Stage, Stage\[\]> = \{(.*?)\n\};",
        source,
        re.DOTALL,
    )
    assert block, "STAGE_MOVES not found in types.ts — was it renamed?"

    moves: dict[str, set[str]] = {}
    for key, values in re.findall(r"^\s*(\w+):\s*\[([^\]]*)\],", block.group(1), re.MULTILINE):
        moves[key] = set(re.findall(r'"([^"]+)"', values))
    return moves


@pytest.fixture(scope="module")
def mirrored() -> dict[str, set[str]]:
    if not TYPES_TS.exists():
        pytest.skip(f"frontend types not present at {TYPES_TS}")
    return _parse_stage_moves(TYPES_TS.read_text(encoding="utf-8"))


def test_every_stage_is_mirrored(mirrored):
    assert set(mirrored) == {stage.value for stage in ApplicationStage}


def test_the_moves_match_the_server(mirrored):
    server = {
        stage.value: {target.value for target in targets} for stage, targets in ALLOWED.items()
    }
    assert mirrored == server


def test_hired_is_terminal_in_both(mirrored):
    # The one that matters most: a hire also removes the candidate from every
    # ranking, so offering a move out of it would misrepresent the model.
    assert mirrored["hired"] == set()
    assert ALLOWED[ApplicationStage.HIRED] == set()
