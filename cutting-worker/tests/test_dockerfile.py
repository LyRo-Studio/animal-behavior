"""The cutting-worker image copies only a hand-picked slice of backend/app
(see its Dockerfile), while tests here run against the whole backend. So a
new `from app...` import anywhere the worker reaches passes every other test
and only fails once the real image starts. That happened with #96/#99's
`media_browser`/`audit_log` imports in app/services/cutting_jobs.py: the
container crash-looped on `ModuleNotFoundError` and jobs sat `queued`.
"""

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"
_DOCKERFILE = _REPO_ROOT / "cutting-worker" / "Dockerfile"
_WORKER_PACKAGE = _REPO_ROOT / "cutting-worker" / "cutting_worker"


def _app_imports(path: Path) -> set[str]:
    imports = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        imports |= {name for name in names if name == "app" or name.startswith("app.")}
    return imports


def _module_file(module: str) -> Path:
    base = _BACKEND / module.replace(".", "/")
    return base.with_suffix(".py") if base.with_suffix(".py").exists() else base / "__init__.py"


def _backend_files_the_worker_imports() -> set[Path]:
    """Every backend/app file reachable by import from cutting_worker/,
    with each package's `__init__.py` along the way."""
    pending = set().union(*(_app_imports(path) for path in _WORKER_PACKAGE.glob("*.py")))
    seen: set[str] = set()
    files: set[Path] = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        parts = module.split(".")
        for depth in range(1, len(parts) + 1):
            package_init = _BACKEND.joinpath(*parts[:depth], "__init__.py")
            if package_init.exists():
                files.add(package_init)
        path = _module_file(module)
        assert path.exists(), f"{module} imported by the cutting-worker doesn't exist"
        files.add(path)
        pending |= _app_imports(path)
    return files


def _backend_paths_the_dockerfile_copies() -> list[Path]:
    """Each `COPY backend/... ./...` source, file or whole directory."""
    return [
        _REPO_ROOT / source
        for source in re.findall(r"^COPY (backend/\S+) ", _DOCKERFILE.read_text(), re.MULTILINE)
    ]


def test_the_image_copies_every_backend_module_the_worker_imports():
    copied = _backend_paths_the_dockerfile_copies()

    missing = sorted(
        str(path.relative_to(_REPO_ROOT))
        for path in _backend_files_the_worker_imports()
        if not any(path == source or source in path.parents for source in copied)
    )

    assert missing == [], f"add these to cutting-worker/Dockerfile's COPY list: {missing}"
