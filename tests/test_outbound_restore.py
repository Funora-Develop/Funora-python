"""Повреждение журнала не стирает квоту и не включает долговечность."""

import inspect
from copy import deepcopy

import pytest

from funora import AsyncClient, Budget, Client, Router, StateFile
from funora._outbound import OutboundGovernor
from funora.errors import StateSchemaIncompatibleError

WALL = 1_700_000_000_000
VALID = {"sent": [{"chat_id": "123", "at_ms": WALL, "cold": True}], "incoming": {"123": WALL}}
BAD = [
    None,
    [],
    False,
    0,
    "",
    {"sent": None},
    {"sent": {}},
    {"sent": ""},
    {"sent": [None]},
    {"sent": [{}]},
    {"sent": [{"chat_id": "123"}]},
    {"sent": [{"at_ms": WALL}]},
    {"sent": [{"chat_id": 123, "at_ms": WALL}]},
    {"sent": [{"chat_id": "", "at_ms": WALL}]},
    {"sent": [{"chat_id": "123", "at_ms": str(WALL)}]},
    {"sent": [{"chat_id": "123", "at_ms": True}]},
    {"sent": [{"chat_id": "123", "at_ms": WALL, "cold": 0}]},
    {"sent": [{"chat_id": "123", "at_ms": WALL, "cold": "false"}]},
    {"sent": [{"chat_id": "123", "at_ms": WALL, "cold": None}]},
    {"sent": [VALID["sent"][0], None]},
    {"incoming": None},
    {"incoming": []},
    {"incoming": ""},
    {"incoming": {"123": str(WALL)}},
    {"incoming": {"123": True}},
    {"incoming": {"": WALL}},
    {"sent": [], "incoming": {"123": None}},
]


class Offline:
    def fetch(self, *args, **kwargs):
        pytest.fail("восстановление не должно обращаться к площадке")

    submit = fetch
    upload = fetch

    def close(self):
        pass


class AsyncOffline(Offline):
    async def close(self):
        pass


async def complete(value):
    if inspect.isawaitable(value):
        return await value
    return value


@pytest.mark.parametrize("payload", BAD)
@pytest.mark.parametrize("merge", [False, True])
def test_invalid_restore_preserves_all_live_records(payload, merge):
    governor = OutboundGovernor()
    governor.record("prior", now_ms=WALL, now_s=10)
    governor.note_incoming("prior", at_ms=WALL)
    before = deepcopy(governor.snapshot())
    sent = list(governor._sent)
    with pytest.raises(StateSchemaIncompatibleError):
        if merge:
            governor.restore(payload, merge=True)
        else:
            governor.restore(payload)
    assert governor.snapshot() == before
    assert governor._sent == sent


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("payload", BAD)
async def test_invalid_journal_refuses_construction_and_adoption(tmp_path, payload, asynchronous):
    state = StateFile(tmp_path / "state.json")
    state.save({"outbound": payload})
    before = state.path.read_bytes()
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    with pytest.raises(StateSchemaIncompatibleError):
        kind(transport=transport, budget=Budget(names=()), state_path=state.path)

    client = kind(transport=transport, budget=Budget(names=()))
    governor = client.engine._state.outbound
    governor.record("prior", now_ms=WALL, now_s=10)
    governor.note_incoming("prior", at_ms=WALL)
    stored = deepcopy(governor.snapshot())
    try:
        with pytest.raises(StateSchemaIncompatibleError):
            await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        assert client.engine._ledger is None
        assert client.engine._state.outbound is governor
        assert governor.durable is False
        assert governor.snapshot() == stored
        assert state.path.read_bytes() == before

        # Отказ освобождает lease: исправленный файл можно подключить тем же клиентом.
        state.save({"outbound": VALID})
        await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        restored = client.engine._state.outbound
        assert restored.durable is True
        assert {one["chat_id"] for one in restored.snapshot()["sent"]} == {"prior", "123"}
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("disk_is_newer", [False, True])
async def test_adoption_keeps_monotonic_quota_and_latest_incoming(
    tmp_path, asynchronous, disk_is_newer
):
    state = StateFile(tmp_path / "state.json")
    disk_at, memory_at = (WALL + 1000, WALL) if disk_is_newer else (WALL, WALL + 1000)
    state.save({"outbound": {"incoming": {"123": disk_at}}})
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    client = kind(transport=transport, budget=Budget(names=()), unsafe_sends_without_ledger=True)
    governor = client.engine._state.outbound
    governor.record("123", now_ms=WALL, now_s=10)
    governor.note_incoming("123", at_ms=memory_at)
    try:
        await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        restored = client.engine._state.outbound
        assert restored.snapshot()["incoming"] == {"123": WALL + 1000}
        # Стенные часы прыгнули вперёд на два часа, внутри процесса прошла секунда.
        refusal = restored.check("123", now_ms=WALL + 7_200_000, now_s=11, declared_cold=True)
        assert refusal is not None
        assert refusal.limit == "min_interval_per_chat"
    finally:
        await complete(client.close())


@pytest.mark.parametrize("payload", [{}, {"sent": []}, {"incoming": {}}, VALID])
def test_valid_legacy_sections_roundtrip(payload):
    governor = OutboundGovernor()
    governor.restore(payload)
    assert governor.snapshot() == {
        "sent": payload.get("sent", []),
        "incoming": payload.get("incoming", {}),
    }
    assert all(one.monotonic_s is None for one in governor._sent)


def test_legacy_missing_cold_is_counted_conservatively():
    governor = OutboundGovernor()
    governor.restore({"sent": [{"chat_id": "123", "at_ms": WALL}]})
    assert governor.snapshot()["sent"][0]["cold"] is True


@pytest.mark.parametrize("stamp", [float(WALL), float("nan"), float("inf")])
def test_float_stamps_are_not_reinterpreted_as_integers(stamp):
    with pytest.raises(StateSchemaIncompatibleError):
        OutboundGovernor().restore({"sent": [{"chat_id": "123", "at_ms": stamp}]})
    with pytest.raises(StateSchemaIncompatibleError):
        OutboundGovernor().restore({"incoming": {"123": stamp}})


def test_restore_copies_caller_containers():
    payload = deepcopy(VALID)
    governor = OutboundGovernor()
    governor.restore(payload)
    payload["incoming"].clear()
    payload["sent"][0]["cold"] = False
    assert governor.snapshot() == VALID
