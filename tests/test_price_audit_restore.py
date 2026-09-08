"""Исходные цены не теряются при повреждении и подключении журнала."""

import json
from copy import deepcopy

import pytest
from test_outbound_restore import AsyncOffline, Offline, complete
from test_price_audit import _change, _flat
from test_update_price import NODE, OFFER, _drive, _revision

from funora import AsyncClient, Budget, Client, Router, StateFile
from funora._delivered import Delivery
from funora._price_audit import PriceAudit, PriceChange
from funora.errors import StateSchemaIncompatibleError

BAD = [
    None,
    [],
    False,
    0,
    "",
    *[{section: value} for section in ("journal", "first") for value in (None, {}, "", [None])],
    *[
        {"journal": [_flat("a"), dict(_flat("b"), **{field: value})]}
        for field, value in (
            ("offer_id", " "),
            ("node_id", None),
            ("price_before", 50),
            ("price_after", False),
            ("revision_before", []),
            ("at_ms", True),
            ("at_ms", "1"),
        )
    ],
    *[{"first": [{k: v for k, v in _flat("a").items() if k != field}]} for field in _flat("a")],
    {"first": [_flat("a"), _flat("a")]},
    {"first": [], "journal": [_flat("a")]},
    *[{"dropped": value} for value in (True, -1, None, "5", 1.5)],
]


@pytest.mark.parametrize("payload", BAD)
@pytest.mark.parametrize("merge", [False, True])
def test_invalid_restore_preserves_original_and_history(payload, merge):
    audit = PriceAudit(limit=1)
    audit.record(_change("old"))
    audit.record(_change("new"))
    before = deepcopy(audit.snapshot())
    with pytest.raises(StateSchemaIncompatibleError):
        audit.restore(payload, merge=merge)
    assert audit.snapshot() == before


@pytest.mark.parametrize("payload", BAD)
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_invalid_audit_refuses_construction_and_adoption(tmp_path, payload, asynchronous):
    state = StateFile(tmp_path / "state.json")
    state.save({})
    envelope = json.loads(state.path.read_text())
    envelope["payload"]["price_audit"] = payload
    state.path.write_text(json.dumps(envelope))
    before = state.path.read_bytes()
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    with pytest.raises(StateSchemaIncompatibleError):
        kind(transport=transport, budget=Budget(names=()), state_path=state.path)
    client = kind(transport=transport, budget=Budget(names=()))
    audit = client.engine.price_audit
    audit.record(_change("memory"))
    original = audit.snapshot()
    delivery = client.engine.delivered
    delivery.record(Delivery("order", "lot", 1, "queued"))
    outbound = client.engine._state.outbound
    try:
        with pytest.raises(StateSchemaIncompatibleError):
            await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        assert client.engine._ledger is None
        assert client.engine.price_audit is audit
        assert audit.snapshot() == original
        assert not audit.durable
        assert client.engine.delivered is delivery and delivery.seen("order")
        assert client.engine._state.outbound is outbound and not outbound.durable
        assert state.path.read_bytes() == before
        state.save({"price_audit": {"journal": [_flat("disk")]}})
        await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        assert client.engine.price_audit is audit
        assert audit.durable
        assert [one.offer_id for one in audit.history()] == ["disk", "memory"]
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("unsafe", [False, True])
async def test_watch_adoption_keeps_original_before_the_next_price_write(
    tmp_path, asynchronous, unsafe
):
    state = StateFile(tmp_path / "state.json")
    saved = PriceAudit(limit=1)
    saved.record(_change(OFFER, at_ms=100))
    saved.record(_change("evict", at_ms=200))
    state.save({"price_audit": saved.snapshot(), "unrelated": "keep"})
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    client = kind(
        transport=transport, budget=Budget(names=()), unsafe_price_changes_without_audit=unsafe
    )
    audit = client.engine.price_audit
    if unsafe:
        # Стенные часы могли уйти назад: порядок записей не сортируется по времени.
        audit.record(PriceChange(OFFER, NODE, "999", "777", "revision", -1))
    try:
        await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        assert client.engine.price_audit is audit and audit.durable
        assert audit.original(OFFER) == saved.original(OFFER)
        assert audit.dropped == 1
        _drive(client.engine.update_price(NODE, OFFER, "12345", expected_revision=_revision()))
        restored = kind(transport=transport, budget=Budget(names=()), state_path=state.path)
        try:
            assert restored.engine.price_audit.original(OFFER) == saved.original(OFFER)
            assert restored.engine.price_audit.history()[-1].price_after == "12345"
            assert StateFile(state.path).load()["unrelated"] == "keep"
            if unsafe:
                assert "unsafe_price_changes_without_audit" in client.engine._unsafe
                assert audit.history()[-2].at_ms == -1
        finally:
            await complete(restored.close())
    finally:
        await complete(client.close())


def test_merge_retains_evicted_first_prices_and_counts_each_eviction():
    disk = PriceAudit(limit=2)
    memory = PriceAudit(limit=2)
    for step in range(5):
        disk.record(_change("disk", at_ms=step))
        memory.record(_change("memory", at_ms=step))
    memory.restore(disk.snapshot(), merge=True)
    assert memory.original("disk") == disk.original("disk")
    assert memory.original("memory") == _change("memory", at_ms=0)
    assert len(memory) == 2
    assert memory.dropped == 8


def test_empty_legacy_state_and_exact_text_remain_supported():
    audit = PriceAudit()
    audit.restore({})
    assert audit.snapshot() == {"journal": [], "first": [], "dropped": 0}
    row = dict(
        _flat("a"), price_before="", price_after=" 1,00 ", revision_before="old-revision", at_ms=-1
    )
    audit.restore({"journal": [row]})
    assert audit.snapshot()["first"] == [row]
