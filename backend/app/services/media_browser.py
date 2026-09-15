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

# A Dataset lives at its own top-level prefix in the bucket (e.g.
# "dataset_pose_v0.1/", "dataset_v0.9/") — sibling to "cuts/" and "source/"
# at the bucket root, not nested under a shared parent prefix (see
# CONTEXT.md's "Dataset" term). A folder matching this at the bucket root is
# a Dataset; nothing else there (in particular "cuts/" and "source/") ever
# surfaces as one, regardless of what a caller supplies as `path` (see
# `_dataset_key_prefix`).
DATASET_NAME_RE = re.compile(r"^dataset[\w.-]*$")

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
    """No folder exists at the given path — either its top-level segment
    isn't a Dataset-shaped name (see `DATASET_NAME_RE`), some other segment
    is malformed, or the resolved path has no children in S3 (S3 has no
    notion of an empty folder, so a Dataset folder's existence *is* having
    at least one entry under it, same reasoning as `TestNotFoundError`)."""


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


def _dataset_key_prefix(path: str) -> str:
    """The S3 prefix `path` (a "/"-separated path from the bucket root,
    e.g. "dataset_v0.9/train") resolves to, or raises DatasetNotFoundError
    if it doesn't name a Dataset.

    Rejecting a "..", "." or empty segment, and requiring the first segment
    to match `DATASET_NAME_RE`, up front — rather than building the prefix
    first and checking the result — is what keeps this function from ever
    resolving a key outside a Dataset's own prefix (in particular never
    "cuts/" or "source/"), regardless of what `path` is supplied (mirrors
    `list_cuts_for_test`'s Test id validation). Only called for a non-root
    `path` — the root case (listing the Datasets themselves) is handled
    directly in `list_dataset_folder`.
    """
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise DatasetNotFoundError(path)
    if not DATASET_NAME_RE.match(segments[0]):
        raise DatasetNotFoundError(path)

    return "/".join(segments) + "/"


def list_dataset_folder(s3: S3Client, path: str = "") -> list[DatasetEntry]:
    """The immediate children (folders and files) of the folder at `path`
    (a "/"-separated path from the bucket root). `path=""` (the top level)
    lists every Dataset itself, rather than one Dataset's contents.

    Raises DatasetNotFoundError if `path` doesn't name a Dataset (or a
    folder inside one) or resolves to a folder with no children.
    """
    path = path.strip("/")

    if path == "":
        entries = []
        for folder in s3.list_folders(""):
            name = folder.rstrip("/")
            if DATASET_NAME_RE.match(name):
                entries.append(DatasetEntry(name=name, key=folder, is_folder=True))
        return sorted(entries, key=lambda entry: entry.name)

    prefix = _dataset_key_prefix(path)

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

    if not entries:
        raise DatasetNotFoundError(path)

    return sorted(entries, key=lambda entry: (not entry.is_folder, entry.name))
