"""Проверки границ записи, гонок процессов и восстановления очереди."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import funora._fileio as fileio
from funora._state import StateFile
from funora.bot import SendCommand, Spool
from funora.errors import StateSchemaIncompatibleError, ValidationError


@pytest.mark.parametrize("payload", [None, [], "", 0])
def test_invalid_payload_never_resets_a_ledger(tmp_path: Path, payload: object) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"delivery": {"done": []}})
    raw = json.loads(state.path.read_text())
    raw["payload"] = payload
    state.path.write_text(json.dumps(raw))
    with pytest.raises(StateSchemaIncompatibleError):
        state.load()


def test_failed_replace_preserves_previous_state_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"before": True})

    def fail(*args: object) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(fileio.os, "replace", fail)
    with pytest.raises(OSError):
        state.update({"after": True})
    assert state.load() == {"before": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_state_updates_from_processes_do_not_lose_sections(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    script = """
import sys
from pathlib import Path
from funora._state import StateFile
state = StateFile(Path(sys.argv[1]))
for i in range(12):
    state.update({sys.argv[2] + str(i): i})
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(path), f"p{n}-"]) for n in range(4)
    ]
    for process in processes:
        assert process.wait(timeout=20) == 0
    assert len(StateFile(path).load()) == 48


def test_spool_different_keys_with_a_shared_suffix_are_distinct(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    assert spool.submit(SendCommand("1", "first", "prefix-key"))
    assert spool.submit(SendCommand("1", "second", "key"))
    assert [one.command.idempotency_key for one in spool.take(2)] == ["prefix-key", "key"]


@pytest.mark.parametrize("key", ["key\n", "../../secret", "", "a/b"])
def test_spool_keys_cannot_escape_the_result_directory(tmp_path: Path, key: str) -> None:
    spool = Spool(tmp_path)
    with pytest.raises(ValidationError):
        spool.submit(SendCommand("1", "text", key))
    with pytest.raises(ValidationError):
        spool.outcome(key)


def test_spool_rejects_payload_identity_different_from_filename(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    spool.submit(SendCommand("1", "text", "original"))
    path = next((tmp_path / "ready").iterdir())
    raw = json.loads(path.read_text())
    raw["idempotency_key"] = "different"
    path.write_text(json.dumps(raw))
    assert spool.take(1) == []
    assert spool.stuck == ("original",)


def test_recovery_preserves_a_recorded_outcome_after_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = Spool(tmp_path)
    spool.submit(SendCommand("1", "text", "sent"))
    entry = spool.take(1)[0]
    original = Path.unlink

    def fail(path: Path, *args: object, **kwargs: object) -> None:
        if path == entry.path:
            raise OSError("cleanup failed")
        original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError):
            spool.settle(entry, state="sent", detail="confirmed")
    assert Spool(tmp_path).recover() == ()
    assert spool.outcome("sent").state == "sent"
    assert spool.take(1) == []


def test_spool_publish_failure_does_not_expose_a_partial_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = Spool(tmp_path)

    def fail(*args: object) -> None:
        raise OSError("full disk")

    monkeypatch.setattr(fileio.os, "fsync", fail)
    with pytest.raises(OSError):
        spool.submit(SendCommand("1", "text", "failed"))
    assert spool.pending == 0
    assert spool.take(1) == []


def test_concurrent_spool_producers_preserve_all_keys_once(tmp_path: Path) -> None:
    script = """
import sys
from funora.bot import Spool, SendCommand
spool = Spool(sys.argv[1])
for i in range(15):
    spool.submit(SendCommand('1', 'same', 'key-' + str(i)))
"""
    Spool(tmp_path)
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)]) for _ in range(4)]
    for process in processes:
        assert process.wait(timeout=20) == 0
    entries = Spool(tmp_path).take(100)
    assert len(entries) == 15
    assert len({one.command.idempotency_key for one in entries}) == 15
    if os.name != "nt":
        assert all(one.path.stat().st_mode & 0o077 == 0 for one in entries)
