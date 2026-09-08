"""Сверяет SDK в двух архивах и устанавливает каждый в отдельное окружение."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


def _parts(name: str) -> list[str]:
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts):
        raise ValueError(f"недопустимый путь в архиве: {name!r}")
    if any("golden_key" in part or part == ".notes" for part in parts):
        raise ValueError(f"служебный файл в архиве: {name!r}")
    return parts


def check_archives(directory: Path) -> tuple[Path, Path]:
    wheels = sorted(directory.resolve().glob("*.whl"))
    sources = sorted(directory.resolve().glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("для проверки нужны ровно один wheel и ровно один sdist")
    wheel, source = wheels[0], sources[0]
    wheel_files: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as archive:
        names: set[str] = set()
        for member in archive.infolist():
            parts = _parts(member.filename)
            name = "/".join(parts)
            if name in names:
                raise ValueError(f"повтор пути в wheel: {name}")
            names.add(name)
            if parts[0] == "funora" and not member.is_dir():
                wheel_files[name] = archive.read(member)
    source_files: dict[str, bytes] = {}
    with tarfile.open(source) as archive:
        names = set()
        for member in archive.getmembers():
            parts = _parts(member.name)
            name = "/".join(parts)
            if parts[0] != source.name.removesuffix(".tar.gz"):
                raise ValueError(f"посторонний корень sdist: {name}")
            if name in names:
                raise ValueError(f"повтор пути в sdist: {name}")
            names.add(name)
            if member.isdir():
                continue
            if not member.isfile() or len(parts) < 2:
                raise ValueError(f"в sdist ожидался обычный файл: {name}")
            if parts[1:3] == ["src", "funora"]:
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"не удалось прочитать файл sdist: {name}")
                with stream:
                    source_files["/".join(parts[2:])] = stream.read()
    if not {"funora/py.typed", "funora/__init__.py"} <= wheel_files.keys():
        raise ValueError("в wheel отсутствует py.typed или __init__.py")
    if wheel_files != source_files:
        changed = sorted(
            name
            for name in wheel_files.keys() | source_files.keys()
            if wheel_files.get(name) != source_files.get(name)
        )
        raise ValueError(f"SDK в wheel и sdist расходится: {changed}")
    print(f"wheel и sdist: совпадают {len(wheel_files)} файлов SDK")
    return wheel, source


def check_distribution(directory: Path) -> None:
    for distribution in check_archives(directory):
        _check_install(distribution)


def _check_install(distribution: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="funora-package-") as temporary:
        root = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        interpreter = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [str(interpreter), "-m", "pip", "install", str(distribution)], check=True, cwd=root
        )
        subprocess.run([str(interpreter), "-m", "pip", "check"], check=True, cwd=root)
        subprocess.run(
            [
                str(interpreter),
                "-I",
                "-c",
                """
from importlib.metadata import version
import funora
assert funora.__version__ == version('funora')
from funora._money import parse_display_price
from funora.errors import ValidationError
assert parse_display_price("0.000012", "€") == funora.Money(12, "EUR", 6)
try:
    funora.Money(2**63, "RUB")
except ValidationError:
    pass
else:
    raise AssertionError("Money accepted int64 overflow")
assert all(hasattr(funora, name) for name in funora.__all__)
funora.Router().on(funora.EventType.WATCH_DEGRADED)(lambda event: None)
from funora._lot_form import _revision_of
assert _revision_of({"a": "x\\nb=y", "b": "z"}, frozenset(), {}) != _revision_of(
    {"a": "x", "b": "y\\nb=z"}, frozenset(), {}
)
from funora._runner import classify_send_response
from funora.send_outcome import SEND_REASONS, SendOutcome
receipt = classify_send_response('{"response": {}, "objects": []}', sent_to="test")
assert receipt.outcome is SendOutcome.UNCONFIRMED
assert receipt.reason == "response_error_missing" and receipt.reason in SEND_REASONS
position = funora.ReviewsCursor("123", "opaque")
assert funora.ReviewsCursor.from_token(position.to_token()) == position
from funora._client import CatalogService
from funora._aclient import AsyncCatalogService
assert callable(CatalogService.search)
assert callable(AsyncCatalogService.search)
assert callable(CatalogService.field_schema)
assert callable(AsyncCatalogService.field_schema)
from funora import Client, AsyncClient
from funora import Budget
from funora.budget import BUCKETS
from funora.errors import ConfigurationError
assert BUCKETS['write'].unit == 'actions_per_hour'
assert Budget().reserve(0, action=True).granted
budget = Budget(names=("account",))
assert all(budget.reserve(0.001).granted for _ in range(5))
assert budget.reserve(0.001).wait_ms == 201
assert budget.reserve(0.202).granted
with Client(public_only=True, account_id='package-check') as client:
    watch = funora.MarketWatch('package-market', '922')
    assert client.monitoring.plan(watch).admitted
    client.monitoring.watch(funora.Router(), watch, max_iterations=0, history_limit=1)
    try:
        client.monitoring.watch(funora.Router(), watch, max_iterations=0, history_limit=0)
    except ConfigurationError:
        pass
    else:
        raise AssertionError('monitoring accepted a zero history limit')
    try:
        client.orders.list()
    except ConfigurationError:
        pass
    else:
        raise AssertionError('anonymous client accepted a private operation')
import asyncio
async def check_async():
    async with AsyncClient(public_only=True) as client:
        watch = funora.MarketWatch("package-market-async", "922")
        assert client.monitoring.plan(watch).admitted
        await client.monitoring.watch(funora.Router(), watch, max_iterations=0, history_limit=1)
asyncio.run(check_async())
print('installed package imports and public API: OK')
""",
            ],
            check=True,
            cwd=root,
        )
        subprocess.run(
            [str(interpreter), "-I", "-m", "funora.observe", "--help"],
            check=True,
            cwd=root,
            stdout=subprocess.DEVNULL,
        )
        print(f"установка и публичный API: {distribution.name}: OK")


if __name__ == "__main__":
    check_distribution(Path(sys.argv[1]))
