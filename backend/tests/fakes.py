import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.services.media_prober import ProbedMediaInfo
from app.services.s3_client import S3ObjectInfo, S3ObjectNotFoundError


@dataclass
class FakeS3Client:
    """In-memory S3Client double for tests (ticket #18) — see
    app/services/s3_client.py's `S3Client` protocol and issue #1's testing
    decisions. Seed `objects` directly rather than via a constructor arg, so
    a test can mutate it (e.g. add a Cut mid-test) the same way it would
    with any other plain dataclass field.
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    # Every object reports this same last_modified — nothing in this fake's
    # seed shape (plain key -> bytes) carries a per-object timestamp, and no
    # test in this ticket needs one.
    last_modified: datetime = field(default_factory=lambda: datetime.now(UTC))

    def list_folders(self, prefix: str = "") -> Iterator[str]:
        seen: set[str] = set()
        for key in self.objects:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if "/" not in rest:
                continue
            folder = prefix + rest.split("/", 1)[0] + "/"
            if folder not in seen:
                seen.add(folder)
                yield folder

    def list_objects_info(
        self, prefix: str = "", *, recursive: bool = True
    ) -> Iterator[S3ObjectInfo]:
        for key, data in self.objects.items():
            if key.endswith("/") or not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if not recursive and "/" in rest:
                continue
            yield S3ObjectInfo(key=key, size=len(data), last_modified=self.last_modified)

    def head_object(self, key: str) -> S3ObjectInfo:
        if key not in self.objects:
            raise S3ObjectNotFoundError(key)
        data = self.objects[key]
        return S3ObjectInfo(
            key=key,
            size=len(data),
            last_modified=self.last_modified,
            # Content-derived, like a real (non-multipart) S3 ETag, so a
            # test can invalidate the cache simply by changing the bytes
            # at the same key (ticket #22) rather than needing a separate
            # seam just to control the ETag.
            etag=hashlib.md5(data).hexdigest(),
        )

    def read_range(self, key: str, start: int, end: int) -> bytes:
        return self.objects[key][start : end + 1]

    def download_file(self, key: str, local_path: Path) -> Path:
        if key not in self.objects:
            raise S3ObjectNotFoundError(key)
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.objects[key])
        return local_path

    def upload_file(self, local_path: Path, key: str) -> None:
        self.objects[key] = Path(local_path).read_bytes()


@dataclass
class FakeMediaProber:
    """Returns a canned `ProbedMediaInfo` instantly, instead of running the
    real `ffprobe` binary — ticket #22's acceptance criterion that tests
    exercise probing "against a fake prober that returns canned results
    instantly — no real probing binary or real video bytes required."

    `calls` records every probed path, in order — tests use its length to
    prove a cache hit skipped a second probe entirely (see
    app/services/cut_media_info.py's caching decision).
    """

    result: ProbedMediaInfo = field(
        default_factory=lambda: ProbedMediaInfo(
            duration_seconds=12.5, width=1920, height=1080, codec="h264"
        )
    )
    calls: list[Path] = field(default_factory=list)

    def probe(self, path: Path) -> ProbedMediaInfo:
        self.calls.append(Path(path))
        return self.result
