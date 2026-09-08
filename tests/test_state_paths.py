"""Только отсутствующий обычный путь означает первый запуск."""

import os
from pathlib import Path

import pytest
from test_outbound_restore import AsyncOffline, Offline, complete

from funora import AsyncClient, Budget, Client, Router, StateFile
from funora.errors import StateSchemaIncompatibleError


def invalid_path(root, problem):
    path = root / "state.json"
    if problem == "directory":
        path.mkdir()
    elif problem == "parent-file":
        path.write_text("preserve")
        path = path / "child.json"
    elif problem == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO недоступен в этой ОС")
        os.mkfifo(path)
    else:
        target = path if problem == "loop" else root / "missing"
        try:
            path.symlink_to(target, target_is_directory=problem == "dangling-parent")
        except OSError:
            pytest.skip("символические ссылки недоступны в этом окружении")
        if problem == "dangling-parent":
            path = path / "child.json"
    return path


PROBLEMS = ["directory", "parent-file", "fifo", "dangling", "dangling-parent", "loop"]


@pytest.mark.parametrize("problem", PROBLEMS)
def test_invalid_paths_are_not_empty_state(tmp_path, problem):
    path = invalid_path(tmp_path, problem)
    with pytest.raises(StateSchemaIncompatibleError):
        StateFile(path).load()


@pytest.mark.parametrize("problem", ["directory", "fifo", "dangling"])
@pytest.mark.parametrize("operation", ["load", "save", "update"])
def test_path_replaced_after_construction_is_not_read_or_overwritten(tmp_path, problem, operation):
    state = StateFile(tmp_path / "state.json")
    path = invalid_path(tmp_path, problem)
    before = path.lstat()
    with pytest.raises(StateSchemaIncompatibleError):
        if operation == "load":
            state.load()
        else:
            getattr(state, operation)({"new": 1})
    assert path.lstat().st_ino == before.st_ino
    assert path.lstat().st_mode == before.st_mode


@pytest.mark.parametrize("problem", PROBLEMS)
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_invalid_path_refuses_client_and_watch_before_io(tmp_path, problem, asynchronous):
    path = invalid_path(tmp_path, problem)
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    with pytest.raises(StateSchemaIncompatibleError):
        kind(transport=transport, state_path=path, budget=Budget(names=()))
    client = kind(transport=transport, budget=Budget(names=()))
    try:
        with pytest.raises(StateSchemaIncompatibleError):
            await complete(client.watch(Router(), state_path=path, max_iterations=0))
        assert client.engine._ledger is None
        assert not client.engine.price_audit.durable
        assert not client.engine._state.outbound.durable
    finally:
        await complete(client.close())


@pytest.mark.parametrize("failure", [PermissionError, OSError, FileNotFoundError])
def test_read_failure_is_typed_and_does_not_replace_existing_file(tmp_path, monkeypatch, failure):
    state = StateFile(tmp_path / "state.json")
    state.save({"preserve": 1})
    before = state.path.read_bytes()

    def fail(*args, **kwargs):
        raise failure("synthetic read failure")

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(StateSchemaIncompatibleError):
        state.update({"new": 2})
    assert state.path.read_bytes() == before


def test_new_nested_path_and_valid_alias_write_the_same_file(tmp_path):
    state = StateFile(tmp_path / "new" / "nested" / "state.json")
    assert state.load() == {}
    state.save({"first": 1})
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(state.path)
    except OSError:
        pytest.skip("символические ссылки недоступны в этом окружении")
    StateFile(alias).update({"second": 2})
    assert alias.is_symlink()
    assert state.load() == {"first": 1, "second": 2}
