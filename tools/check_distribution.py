"""Проверяет собранный wheel вне исходного дерева и текущего окружения."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


def check_distribution(directory: Path) -> None:
    wheels = sorted(directory.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("для проверки нужен ровно один wheel")
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        assert "funora/py.typed" in names, "в wheel отсутствует py.typed"
        assert not any("golden_key" in name or "/.notes/" in name for name in names)
    with tempfile.TemporaryDirectory(prefix="funora-wheel-") as temporary:
        root = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        interpreter = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [str(interpreter), "-m", "pip", "install", str(wheels[0])], check=True, cwd=root
        )
        subprocess.run(
            [
                str(interpreter),
                "-I",
                "-c",
                """
from importlib.metadata import version
import funora
assert funora.__version__ == version('funora')
assert all(hasattr(funora, name) for name in funora.__all__)
from funora._client import CatalogService
from funora._aclient import AsyncCatalogService
assert callable(CatalogService.field_schema)
assert callable(AsyncCatalogService.field_schema)
from funora import Client, AsyncClient
from funora.errors import ConfigurationError
with Client(public_only=True, account_id='package-check') as client:
    try:
        client.orders.list()
    except ConfigurationError:
        pass
    else:
        raise AssertionError('anonymous client accepted a private operation')
import asyncio
async def check_async():
    async with AsyncClient(public_only=True):
        pass
asyncio.run(check_async())
print('wheel imports and public API: OK')
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


if __name__ == "__main__":
    check_distribution(Path(sys.argv[1]))
