"""Test search and Cut listing (ticket #19, part of #17's media browser).

No Test/Cut catalog database — Tests and Cuts are resolved live from S3
through the injected `S3Client` (see CONTEXT.md's "Media browser — no
Test/Cut catalog database (for now)" decision), kept behind these named
functions so a future catalog database can replace the live-S3
implementation without changing callers.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from app.services.s3_client import S3Client

# One prefix per Test (see CONTEXT.md's "Test" term) — the only prefix this
# module ever resolves keys under, regardless of what a caller supplies as
# a Test id (see `list_cuts_for_test`'s validation).
CUTS_PREFIX = "cuts/"

# A Test id, e.g. "T001" (CONTEXT.md's "Test" term). Anything not matching
# this is never a real Test id, so `list_cuts_for_test` rejects it up front
# rather than using it to build an S3 prefix.
_TEST_ID_RE = re.compile(r"^T\d+$")

# A Cut's filename, e.g. "T001_C1_ME_F1.mp4" (CONTEXT.md's Camera/
# Condition/Phase terms). A filename that doesn't match this still
# produces a Cut (see `_parse_cut_filename`) — it just has no parsed
# Camera/Condition/Phase.
_CUT_FILENAME_RE = re.compile(
    r"^T\d+_(?P<camera>C\d+)_(?P<condition>ME|ZE)_(?P<phase>F\d+)\.[^./]+$"
)


class TestNotFoundError(Exception):
    """No Cuts exist under the given Test id's prefix — since there's no
    catalog database, a Test's existence *is* having at least one Cut
    under `cuts/<test_id>/`."""


@dataclass(frozen=True)
class Cut:
    key: str
    filename: str
    camera: str | None
    condition: str | None
    phase: str | None
    size: int
    last_modified: datetime


def list_test_ids(s3: S3Client) -> list[str]:
    """Every Test id, for the frontend's fetch-once-then-filter-client-side
    search (CONTEXT.md's "Media browser — Test discovery" decision)."""
    return sorted(
        folder.removeprefix(CUTS_PREFIX).rstrip("/") for folder in s3.list_folders(CUTS_PREFIX)
    )


def _parse_cut_filename(filename: str) -> tuple[str | None, str | None, str | None]:
    match = _CUT_FILENAME_RE.match(filename)
    if match is None:
        return None, None, None
    return match["camera"], match["condition"], match["phase"]


def list_cuts_for_test(s3: S3Client, test_id: str) -> list[Cut]:
    """All Cuts belonging to a Test, sorted by filename.

    Raises TestNotFoundError if `test_id` isn't a well-formed Test id or no
    Cuts exist under it. Validating the format before building the S3
    prefix is what keeps this feature from ever resolving a key outside
    `cuts/`, regardless of what `test_id` is supplied — a value containing
    "/" or ".." never reaches `list_objects_info` at all.
    """
    if not _TEST_ID_RE.match(test_id):
        raise TestNotFoundError(test_id)

    prefix = f"{CUTS_PREFIX}{test_id}/"
    cuts = []
    for info in s3.list_objects_info(prefix, recursive=False):
        filename = info.key.rsplit("/", 1)[-1]
        camera, condition, phase = _parse_cut_filename(filename)
        cuts.append(
            Cut(
                key=info.key,
                filename=filename,
                camera=camera,
                condition=condition,
                phase=phase,
                size=info.size,
                last_modified=info.last_modified,
            )
        )

    if not cuts:
        raise TestNotFoundError(test_id)

    return sorted(cuts, key=lambda cut: cut.filename)
