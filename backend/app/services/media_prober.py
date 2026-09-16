"""Probing a Cut's media info (duration, resolution, codec), behind a small
injectable seam — ticket #22, part of #17's media browser.

Mirrors `app/services/s3_client.py`'s and `app/services/mail.py`'s
Protocol-plus-fake-transport shape: later code calls through `MediaProber`
(never `subprocess`/`ffprobe` directly), so tests inject `FakeMediaProber`
(see `tests/fakes.py`) instead of running the real binary against real
video bytes (CONTEXT.md's "Media browser — Cut media-info caching"
decision).
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbedMediaInfo:
    duration_seconds: float
    width: int
    height: int
    codec: str


class MediaProbeError(Exception):
    """Probing the file at a given path failed — `ffprobe` errored, timed
    out, or produced output this module can't make sense of."""


class MediaProber(Protocol):
    def probe(self, path: Path) -> ProbedMediaInfo:
        """The duration/resolution/codec of the local video file at
        `path`. Raises MediaProbeError if it can't be determined."""
        ...


class FfprobeMediaProber:
    """Real MediaProber, shelling out to the `ffprobe` binary against a
    local file (CONTEXT.md: "`ffprobe` runs against a temporary local
    download of the Cut... not a presigned URL")."""

    def probe(self, path: Path) -> ProbedMediaInfo:
        try:
            result = subprocess.run(  # noqa: S603
                [
                    settings.ffprobe_path,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    "-select_streams",
                    "v:0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=settings.ffprobe_timeout_seconds,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MediaProbeError(f"ffprobe failed for {path}: {exc}") from exc

        try:
            data = json.loads(result.stdout)
            stream = data["streams"][0]
            return ProbedMediaInfo(
                duration_seconds=float(data["format"]["duration"]),
                width=int(stream["width"]),
                height=int(stream["height"]),
                codec=str(stream["codec_name"]),
            )
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise MediaProbeError(f"unparseable ffprobe output for {path}: {exc}") from exc


_media_prober: MediaProber | None = None


def get_media_prober() -> MediaProber:
    """The process-lifetime MediaProber, built once and reused — same
    "shared, stateless client" reasoning as `get_s3_client`, since
    `FfprobeMediaProber` holds no per-request state."""
    global _media_prober
    if _media_prober is None:
        _media_prober = FfprobeMediaProber()
    return _media_prober
