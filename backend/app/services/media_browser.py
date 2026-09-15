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

# Every Dataset lives under this prefix (see CONTEXT.md's "Dataset" term) —
# the only prefix `list_dataset_folder` ever resolves keys under, regardless
# of what a caller supplies as `path` (see `_dataset_prefix_for_path`).
DATASET_PREFIX = "dataset/"

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


class DatasetNotFoundError(Exception):
    """No folder exists at the given path under `dataset/` — either the
    path is malformed (escapes the Datasets prefix) or it's well-formed but
    has no children in S3 (S3 has no notion of an empty folder, so a
    Dataset folder's existence *is* having at least one entry under it,
    same reasoning as `TestNotFoundError`)."""


@dataclass(frozen=True)
class Cut:
    key: str
    filename: str
    camera: str | None
    condition: str | None
    phase: str | None
    size: int
    last_modified: datetime


@dataclass(frozen=True)
class DatasetEntry:
    name: str
    key: str
    is_folder: bool
    size: int | None = None
    last_modified: datetime | None = None


def list_test_ids(s3: S3Client) -> list[str]:
    """Every well-formed Test id, for the frontend's
    fetch-once-then-filter-client-side search (CONTEXT.md's "Media browser
    — Test discovery" decision).

    Filtered through the same `_TEST_ID_RE` `list_cuts_for_test` validates
    against — a stray folder under `cuts/` that isn't shaped like a Test id
    (e.g. `cuts/T001_backup/`) must never surface as a pickable suggestion,
    since selecting it would just fail `list_cuts_for_test`'s own check.
    """
    ids = (folder.removeprefix(CUTS_PREFIX).rstrip("/") for folder in s3.list_folders(CUTS_PREFIX))
    return sorted(test_id for test_id in ids if _TEST_ID_RE.match(test_id))


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


def _dataset_prefix_for_path(path: str) -> str:
    """The S3 prefix `path` (a "/"-separated path *relative to* `dataset/`,
    e.g. "dataset_v1/train") resolves to, or raises DatasetNotFoundError if
    it doesn't stay under `DATASET_PREFIX`.

    Rejecting a "..", "." or empty segment up front — rather than building
    the prefix first and checking the result — is what keeps this function
    from ever resolving a key outside `dataset/`, regardless of what `path`
    is supplied (mirrors `list_cuts_for_test`'s Test id validation).
    """
    path = path.strip("/")
    if path == "":
        return DATASET_PREFIX

    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise DatasetNotFoundError(path)

    return DATASET_PREFIX + "/".join(segments) + "/"


def list_dataset_folder(s3: S3Client, path: str = "") -> list[DatasetEntry]:
    """The immediate children (folders and files) of the Dataset folder at
    `path` (relative to `dataset/`; "" for the top level).

    Raises DatasetNotFoundError if `path` escapes `dataset/` or resolves to
    a folder with no children.
    """
    prefix = _dataset_prefix_for_path(path)

    entries = [
        DatasetEntry(name=folder.removeprefix(prefix).rstrip("/"), key=folder, is_folder=True)
        for folder in s3.list_folders(prefix)
    ]
    entries += [
        DatasetEntry(
            name=info.key.removeprefix(prefix),
            key=info.key,
            is_folder=False,
            size=info.size,
            last_modified=info.last_modified,
        )
        for info in s3.list_objects_info(prefix, recursive=False)
    ]

    if not entries and prefix != DATASET_PREFIX:
        raise DatasetNotFoundError(path)

    return sorted(entries, key=lambda entry: (not entry.is_folder, entry.name))
