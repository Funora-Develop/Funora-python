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
