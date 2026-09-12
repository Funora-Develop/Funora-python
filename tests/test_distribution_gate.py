"""Состав дистрибутива не зависит от Git и результатов предыдущих проверок."""

import importlib.util
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_distribution", ROOT / "tools/check_distribution.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def contents(path: Path) -> dict[str, bytes]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    with tarfile.open(path) as archive:
        return {
            member.name.split("/", 1)[1]: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        }


def build(project: Path, output: Path) -> dict[str, dict[str, bytes]]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hatchling",
            "build",
            "-t",
            "sdist",
            "-t",
            "wheel",
            "-d",
            str(output),
        ],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {path.name: contents(path) for path in output.iterdir()}


def test_build_without_git_excludes_local_files(tmp_path: Path) -> None:
    # Имя tmp входит в .gitignore. Hatchling в таком корне отключает правила
    # VCS: регрессия воспроизводится и на macOS с pytest в /var/folders.
    project = tmp_path / "tmp" / "project"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "README.en.md", "LICENSE", "DISCLAIMER.md"):
        (project / name).write_bytes((ROOT / name).read_bytes())
    files = {
        "src/funora/__init__.py": b'__version__ = "0.0.1.dev2"\n',
        "src/funora/py.typed": b"",
        "tests/test_example.py": b"def test_example(): pass\n",
        "tests/fixtures/pages/example.skeleton.txt": b"redacted\n",
        "tests/fixtures/pages/example.provenance.json": b"{}\n",
        "tests/fixtures/pages/README.md": b"fixtures\n",
        "tools/example.py": b"print('example')\n",
        "tools/example.js": b"export default {};\n",
        "docs/guide/example.md": b"# Example\n",
        ".github/workflows/ci.yml": b"name: test\n",
        ".gitattributes": b"* text=auto\n",
        ".gitignore": (ROOT / ".gitignore").read_bytes(),
        "mkdocs.yml": b"site_name: test\n",
    }
    for name, data in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    clean = build(project, tmp_path / "clean")
    for name in (
        ".coverage",
        "coverage.xml",
        "site/index.html",
        "dist/old.whl",
        ".env",
        "golden_key.txt",
        "observations/page.raw.json",
        ".notes/private.md",
        "src/funora/__pycache__/example.cpython-311.pyc",
        "src/funora/.env",
        "tests/__pycache__/example.cpython-311.pyc",
        "tests/.cache/local.py",
        "docs/.cache/local.md",
        "tools/.cache/local.py",
    ):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic local data, never ship\n")
    dirty = build(project, tmp_path / "dirty")
    assert clean == dirty
    sdist = next(data for name, data in dirty.items() if name.endswith(".tar.gz"))
    assert all(sdist[name] == data for name, data in files.items())


@pytest.mark.parametrize("extension", [".whl", ".tar.gz"])
def test_both_distributions_are_required(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"funora-0.0.1{extension}").touch()
    with pytest.raises(ValueError, match="ровно один"):
        MODULE.check_archives(tmp_path)


def archives(
    directory: Path,
    *,
    wheel_files: dict[str, bytes] | None = None,
    source_files: dict[str, bytes] | None = None,
) -> tuple[Path, Path]:
    package = {"funora/__init__.py": b"# SDK\n", "funora/py.typed": b""}
    wheel = directory / "funora-0.1.0-py3-none-any.whl"
    source = directory / "funora-0.1.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in (package if wheel_files is None else wheel_files).items():
            # Конструктор ZipInfo нормализует разделители на Windows.
            # Негодный вход нужно сохранить в архиве буквально.
            member = zipfile.ZipInfo()
            member.filename = name
            archive.writestr(member, data)
    if source_files is None:
        source_files = {f"funora-0.1.0/src/{name}": data for name, data in package.items()}
    with tarfile.open(source, "w:gz") as archive:
        for name, data in source_files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return wheel, source


def test_valid_archives_install_both_formats(tmp_path: Path, monkeypatch) -> None:
    expected = archives(tmp_path)
    installed = []
    monkeypatch.setattr(MODULE, "_check_install", installed.append)
    MODULE.check_distribution(tmp_path)
    assert installed == list(expected)


@pytest.mark.parametrize("index", [0, 1])
def test_ambiguous_distributions_are_rejected(tmp_path: Path, index: int) -> None:
    path = archives(tmp_path)[index]
    path.with_name("another-" + path.name).write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="ровно один"):
        MODULE.check_archives(tmp_path)


@pytest.mark.parametrize("change", ["missing_module", "missing_marker", "changed", "extra"])
def test_source_mismatch_prevents_installation(tmp_path: Path, monkeypatch, change: str) -> None:
    source = {
        "funora-0.1.0/src/funora/__init__.py": b"# SDK\n",
        "funora-0.1.0/src/funora/py.typed": b"",
    }
    if change == "missing_module":
        del source["funora-0.1.0/src/funora/__init__.py"]
    elif change == "missing_marker":
        del source["funora-0.1.0/src/funora/py.typed"]
    elif change == "changed":
        source["funora-0.1.0/src/funora/__init__.py"] = b"# different SDK\n"
    else:
        source["funora-0.1.0/src/funora/extra.py"] = b"# missing from wheel\n"
    archives(tmp_path, source_files=source)
    monkeypatch.setattr(MODULE, "_check_install", lambda _: pytest.fail("installation started"))
    with pytest.raises(ValueError, match="расходится"):
        MODULE.check_distribution(tmp_path)


@pytest.mark.parametrize("missing", ["funora/__init__.py", "funora/py.typed"])
def test_incomplete_wheel_is_rejected(tmp_path: Path, missing: str) -> None:
    files = {"funora/__init__.py": b"# SDK\n", "funora/py.typed": b""}
    del files[missing]
    archives(tmp_path, wheel_files=files)
    with pytest.raises(ValueError, match="отсутствует"):
        MODULE.check_archives(tmp_path)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("name", ["/absolute", "../escape", "a/../b", "a\\b", "C:/file", "a//b"])
def test_unsafe_archive_paths_are_rejected(tmp_path: Path, kind: str, name: str) -> None:
    archives(tmp_path, **{f"{kind if kind == 'wheel' else 'source'}_files": {name: b"bad"}})
    with pytest.raises(ValueError, match="недопустимый путь"):
        MODULE.check_archives(tmp_path)


@pytest.mark.parametrize("name", ["a\\b", "a\x00b"])
def test_wheel_checks_original_path_on_windows(tmp_path: Path, monkeypatch, name: str) -> None:
    archives(tmp_path, wheel_files={name: b"bad"})
    # Воспроизводит нормализацию ZipInfo и при прогоне на POSIX.
    monkeypatch.setattr(zipfile.os, "sep", "\\")
    with pytest.raises(ValueError, match="недопустимый путь"):
        MODULE.check_archives(tmp_path)


def test_foreign_source_root_is_rejected(tmp_path: Path) -> None:
    archives(tmp_path, source_files={"other-0.1.0/src/funora/__init__.py": b"# SDK\n"})
    with pytest.raises(ValueError, match="посторонний корень"):
        MODULE.check_archives(tmp_path)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_source_links_and_special_files_are_rejected(tmp_path: Path, kind: bytes) -> None:
    _, source = archives(tmp_path)
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo("funora-0.1.0/src/funora/__init__.py")
        member.type = kind
        member.linkname = "../../../outside"
        archive.addfile(member)
    with pytest.raises(ValueError, match="обычный файл"):
        MODULE.check_archives(tmp_path)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_duplicate_archive_paths_are_rejected(tmp_path: Path, kind: str) -> None:
    wheel, source = archives(tmp_path)
    if kind == "wheel":
        with zipfile.ZipFile(wheel, "a") as archive, pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("funora/__init__.py", b"different SDK\n")
    else:
        with tarfile.open(source, "w:gz") as archive:
            for _ in range(2):
                archive.addfile(tarfile.TarInfo("funora-0.1.0/src/funora/__init__.py"))
    with pytest.raises(ValueError, match="повтор пути"):
        MODULE.check_archives(tmp_path)


@pytest.mark.parametrize("name", ["golden_key.txt", ".notes/private.md"])
@pytest.mark.parametrize("kind", ["wheel", "source"])
def test_private_paths_are_rejected(tmp_path: Path, name: str, kind: str) -> None:
    archives(tmp_path, **{f"{kind}_files": {f"funora-0.1.0/{name}": b"synthetic"}})
    with pytest.raises(ValueError, match="служебный файл"):
        MODULE.check_archives(tmp_path)
