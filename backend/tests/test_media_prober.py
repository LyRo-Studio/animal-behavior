import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.media_prober import FfprobeMediaProber, MediaProbeError, ProbedMediaInfo

# FfprobeMediaProber is deliberately never exercised through the app's own
# HTTP seam (app/api/deps.py-style DI always overrides get_media_prober to
# FakeMediaProber in tests — see conftest.py) since that would mean running
# the real ffprobe binary against real video bytes. It's still worth a
# narrow unit test of its own contract, mocking only the third-party
# subprocess it wraps — same pattern as test_mail.py's SmtpMailTransport
# tests and test_s3_client.py's BotoS3Client tests.

_FFPROBE_JSON = json.dumps(
    {
        "format": {"duration": "12.345000"},
        "streams": [{"width": 1920, "height": 1080, "codec_name": "h264"}],
    }
)


def test_probe_parses_duration_resolution_and_codec_from_ffprobe_json():
    with patch("app.services.media_prober.subprocess.run") as run:
        run.return_value = MagicMock(stdout=_FFPROBE_JSON)

        result = FfprobeMediaProber().probe(Path("/tmp/some-cut.mp4"))

        assert result == ProbedMediaInfo(
            duration_seconds=12.345, width=1920, height=1080, codec="h264"
        )
        [call] = run.call_args_list
        command = call.args[0]
        assert command[0] == "ffprobe"
        assert str(Path("/tmp/some-cut.mp4")) in command


def test_probe_raises_media_probe_error_when_ffprobe_exits_nonzero():
    with patch("app.services.media_prober.subprocess.run") as run:
        run.side_effect = subprocess.CalledProcessError(1, ["ffprobe"])

        with pytest.raises(MediaProbeError):
            FfprobeMediaProber().probe(Path("/tmp/broken.mp4"))


def test_probe_raises_media_probe_error_on_unparseable_output():
    with patch("app.services.media_prober.subprocess.run") as run:
        run.return_value = MagicMock(stdout="not json")

        with pytest.raises(MediaProbeError):
            FfprobeMediaProber().probe(Path("/tmp/some-cut.mp4"))


def test_probe_raises_media_probe_error_when_the_binary_is_missing():
    with patch("app.services.media_prober.subprocess.run") as run:
        run.side_effect = FileNotFoundError("no such file")

        with pytest.raises(MediaProbeError):
            FfprobeMediaProber().probe(Path("/tmp/some-cut.mp4"))
