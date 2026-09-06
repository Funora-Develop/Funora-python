"""Публикация запрещена при рассогласовании версий."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_release", ROOT / "tools/check_release.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_matching_release_versions_pass() -> None:
    from funora import __version__

    assert MODULE.check_release(ROOT, f"v{__version__}") == __version__


@pytest.mark.parametrize("tag", ["v1.0.0", "main", "", "0.0.1.dev0"])
def test_mismatched_release_tag_is_rejected(tag: str) -> None:
    with pytest.raises(ValueError, match="версии расходятся"):
        MODULE.check_release(ROOT, tag)


def test_public_api_version_must_match_metadata(tmp_path: Path) -> None:
    (tmp_path / "src/funora").mkdir(parents=True)
    (tmp_path / "src/funora/__init__.py").write_text('__version__ = "0.1.0"')
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"')
    with pytest.raises(ValueError):
        MODULE.check_release(tmp_path, "v0.2.0")
