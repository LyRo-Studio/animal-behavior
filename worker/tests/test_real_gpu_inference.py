"""Opt-in real-GPU/real-image integration test for the analysis worker
(ticket #54, part of #44's Docker/CI/CD wiring). Exercises `RealDogTraceRunner`
— the actual `dogtrace` package bundled in the `lynndelaere/dogtrace` image,
never a fake — against a real CUDA GPU and a real C2 Cut pulled from the real
S3 bucket. This is the closest thing to a full production run this repo can
exercise automatically; everything else (test_orchestrator.py) deliberately
stays on `FakeDogTraceRunner`/`FakeS3Client` (issue #44's testing strategy).

Opt-in only, via the `real_gpu` marker — worker/pyproject.toml's default
`addopts` excludes it, so a plain `pytest` run and CI (no GPU, no `dogtrace`
installed — see worker/Dockerfile's "no FastAPI/test deps in the production
image" boundary) never touch either. `worker/requirements.txt` deliberately
never installs `torch`/`dogtrace`/pytest/alembic itself (they only exist
inside a container built from the `lynndelaere/dogtrace` base image), so
running this file for real means, from inside a shell in such a container:

    pip install --no-cache-dir -r worker/requirements-dev.txt
    python -m pytest -m real_gpu worker/tests/test_real_gpu_inference.py

Skips (rather than fails) whenever a prerequisite this test itself checks is
missing — `dogtrace`/`torch` not importable, no CUDA-capable GPU, or S3 not
configured — so an accidental `-m real_gpu` run outside that environment gets
a clear skip reason instead of a crash, same as `test_s3_client_real_bucket.py`'s
`real_bucket` marker. This doesn't cover conftest.py's own session-scoped
`_migrated_schema` fixture failing outright (e.g. DATABASE_URL unreachable) —
same pre-existing caveat `real_bucket` already carries, since every test in
this directory shares that autouse fixture regardless of which marker it has.
"""

from pathlib import Path

import pytest
from app.core.config import settings
from app.services.analyses import _DOGTRACE_C2_FILENAME_RE
from app.services.s3_client import BotoS3Client

from worker.dogtrace_runner import RealDogTraceRunner

pytestmark = pytest.mark.real_gpu


def _require_real_gpu_and_dogtrace() -> None:
    try:
        import torch
    except ImportError:
        pytest.skip("torch not installed — not running inside the dogtrace image.")
    if not torch.cuda.is_available():
        pytest.skip("No CUDA-capable GPU available.")
    try:
        import dogtrace  # noqa: F401
    except ImportError:
        pytest.skip("dogtrace not installed — not running inside the dogtrace image.")


def _real_s3_client() -> BotoS3Client:
    if not (
        settings.s3_bucket
        and settings.s3_endpoint
        and settings.aws_access_key_id
        and settings.aws_secret_access_key
    ):
        pytest.skip(
            "S3 not configured — set S3_BUCKET/S3_ENDPOINT/AWS_ACCESS_KEY_ID/"
            "AWS_SECRET_ACCESS_KEY (see .env.example) to run the real-GPU test."
        )
    return BotoS3Client(
        bucket_name=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint,
        access_key_id=settings.aws_access_key_id,
        secret_access_key=settings.aws_secret_access_key,
        addressing_style=settings.s3_addressing_style,
    )


def _any_real_c2_cut_key(client: BotoS3Client) -> str:
    """The first real Cut whose filename matches DogTrace's own C2 pattern
    — deliberately not a hardcoded Test/Cut, since the bucket's actual
    contents aren't this test's concern and may change over time (same
    reasoning as `test_s3_client_real_bucket.py`'s `_any_real_object_key`).
    Reuses `analyses.py`'s own C2 pattern rather than a second copy of it.
    """
    for info in client.list_objects_info(prefix="cuts/"):
        filename = info.key.rsplit("/", 1)[-1]
        if _DOGTRACE_C2_FILENAME_RE.match(filename):
            return info.key
    pytest.fail("Real bucket has no C2 Cut to run real inference against.")


def test_real_dogtrace_runner_processes_a_real_c2_cut_on_a_real_gpu(tmp_path: Path):
    _require_real_gpu_and_dogtrace()
    client = _real_s3_client()
    cut_key = _any_real_c2_cut_key(client)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    local_path = input_dir / Path(cut_key).name
    client.download_file(cut_key, local_path)

    output_dir = tmp_path / "output"
    events: list[tuple[str, str]] = []

    RealDogTraceRunner().run_reporting(
        [local_path],
        output_dir=output_dir,
        progress=lambda path, event: events.append((path.name, event)),
    )

    assert (local_path.name, "started") in events
    assert (local_path.name, "succeeded") in events
    assert any(output_dir.rglob("*.xlsx")), "Expected at least one report artifact to be produced."
