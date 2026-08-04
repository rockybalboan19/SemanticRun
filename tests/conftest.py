"""Use a project-local temp root — Windows OneDrive blocks pytest's default basetemp."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / ".pytest_tmp"


@pytest.fixture
def tmp_path() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    path = _ROOT / f"t_{Path(pytest.__file__).stem}"
    # Unique per test via pytest request would be better — use mkdtemp-style.
    import uuid

    path = _ROOT / f"case_{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
