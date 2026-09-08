"""Повреждённые журналы не разрешают повтор выдачи и не стирают задания."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from test_auto_delivery import LOTS, _delivery, _order
from test_outbound_restore import AsyncOffline, Offline, complete

from funora import AsyncClient, Budget, Client, Router, StateFile
from funora._delivered import Delivery, DeliveryLedger
from funora._result import Completeness
from funora.bot import SendCommand, Spool
from funora.errors import StateSchemaIncompatibleError, ValidationError

DELIVERY = {"order_id": "A1", "offer_id": "L2", "at_ms": 1, "outcome": "queued"}
BAD_DELIVERIES = [
    None,
    [],
    False,
    0,
    "",
    {"done": None},
    {"done": {}},
    {"done": ""},
    {"done": [None]},
    {"done": [{}]},
    {"done": [{"order_id": "A1"}]},
    {"done": [dict(DELIVERY, order_id=1)]},
    {"done": [dict(DELIVERY, order_id=" ")]},
    {"done": [dict(DELIVERY, at_ms=True)]},
    {"done": [dict(DELIVERY, at_ms="broken")]},
    {"done": [dict(DELIVERY, offer_id=None)]},
    {"done": [dict(DELIVERY, outcome=False)]},
    {"done": [DELIVERY, dict(DELIVERY, outcome="confirmed")]},
    {"done": [DELIVERY, None]},
]


@pytest.mark.parametrize("payload", BAD_DELIVERIES)
def test_bad_delivery_keeps_every_previous_record(payload):
    ledger = DeliveryLedger()
    ledger.record(Delivery(**DELIVERY))
    before = ledger.snapshot()
    with pytest.raises(StateSchemaIncompatibleError):
        ledger.restore(payload)
    assert ledger.snapshot() == before
    assert ledger.seen("A1")
    assert ledger.unsettled() == ("A1",)


@pytest.mark.parametrize("payload", BAD_DELIVERIES)
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_bad_delivery_refuses_start_and_adoption(tmp_path, payload, asynchronous):
    state = StateFile(tmp_path / "state.json")
    state.save({"delivery": payload})
    before = state.path.read_bytes()
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    with pytest.raises(StateSchemaIncompatibleError):
        kind(transport=transport, state_path=state.path, budget=Budget(names=()))
    client = kind(transport=transport, budget=Budget(names=()))
    ledger = client.engine.delivered
    ledger.record(Delivery(**DELIVERY))
    outbound = client.engine._state.outbound
    try:
        with pytest.raises(StateSchemaIncompatibleError):
            await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        assert client.engine._ledger is None
        assert client.engine.delivered is ledger
        assert ledger.snapshot() == {"done": [DELIVERY]}
        assert client.engine._state.outbound is outbound
        assert not outbound.durable
        assert state.path.read_bytes() == before
        state.save({"delivery": {"done": [DELIVERY]}})
        await complete(client.watch(Router(), state_path=state.path, max_iterations=0))
        assert client.engine.delivered is ledger
        assert ledger.seen("A1")
        assert client.engine._state.outbound.durable
    finally:
        await complete(client.close())


def test_corruption_never_requeues_a_delivered_order():
    delivery, queued = _delivery()
    assert delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)
    bad = deepcopy(delivery._ledger.snapshot())
    bad["done"][0]["at_ms"] = "broken"
    with pytest.raises(StateSchemaIncompatibleError):
        delivery._ledger.restore(bad)
    assert delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE) is None
    assert delivery.decisions[-1].reason == "already_delivered"
    assert len(queued) == 1


def test_legacy_delivery_remains_an_explicit_guard():
    ledger = DeliveryLedger()
    ledger.restore({"done": [{"order_id": "A1", "at_ms": 1}]})
    assert ledger.get("A1") == Delivery("A1", "", 1, "")
    ledger.restore({})
    assert len(ledger) == 0


OUTCOME = {
    "idempotency_key": "b",
    "state": "sent",
    "detail": "confirmed",
    "at": "2026-09-08T00:00:00+00:00",
}
BAD_OUTCOMES = [
    "{}",
    "[]",
    "null",
    '"sent"',
    "{",
    "[",
    *[json.dumps({k: v for k, v in OUTCOME.items() if k != missing}) for missing in OUTCOME],
    *[
        json.dumps(dict(OUTCOME, **{field: value}))
        for field, value in [
            ("idempotency_key", "another"),
            ("idempotency_key", 1),
            ("state", ""),
            ("state", "queued"),
            ("state", None),
            ("detail", []),
            ("at", ""),
            ("at", "not a date"),
            ("at", "2026-09-08"),
            ("at", "2026-09-08T00:00:00"),
            ("at", 1),
        ]
    ],
    json.dumps(OUTCOME)[:-1] + ', "state": "refused"}',
    json.dumps(OUTCOME)[:-1] + ', "extra": {"x": 1, "x": 2}}',
    *[
        json.dumps(OUTCOME)[:-1] + ', "extra": ' + number + "}"
        for number in ("NaN", "Infinity", "-Infinity", "1e9999")
    ],
    pytest.param("[" * 10_000 + "0" + "]" * 10_000, id="too-deep-json"),
]


def taken_pair(tmp_path):
    spool = Spool(tmp_path)
    for key in ("a", "b"):
        assert spool.submit(SendCommand("123", "synthetic", key))
    entries = spool.take(2)
    (tmp_path / "done/a.json").write_text(json.dumps(dict(OUTCOME, idempotency_key="a")))
    return spool, entries


@pytest.mark.parametrize("body", BAD_OUTCOMES)
def test_bad_outcome_preserves_the_whole_recovery_batch(tmp_path, body):
    spool, entries = taken_pair(tmp_path)
    result = tmp_path / "done/b.json"
    result.write_text(body, encoding="utf-8")
    before = {entry.path: entry.path.read_bytes() for entry in entries}
    with pytest.raises(StateSchemaIncompatibleError):
        spool.outcome("b")
    with pytest.raises(StateSchemaIncompatibleError):
        spool.recover()
    assert {path: path.read_bytes() for path in before} == before
    assert result.read_text(encoding="utf-8") == body
    assert spool.stuck == ()
    result.write_text(json.dumps(OUTCOME))
    assert spool.recover() == ()
    assert all(not entry.path.exists() for entry in entries)
    assert spool.take(2) == []


@pytest.mark.parametrize("problem", ["directory", "encoding", "permission"])
def test_unreadable_outcome_is_not_an_absent_outcome(tmp_path, monkeypatch, problem):
    spool, entries = taken_pair(tmp_path)
    path = tmp_path / "done/b.json"
    if problem == "directory":
        path.mkdir()
    elif problem == "encoding":
        path.write_bytes(b"\xff")
    else:
        path.write_text(json.dumps(OUTCOME))
        original = type(path).read_text

        def deny(target, *args, **kwargs):
            if target == path:
                raise PermissionError("synthetic")
            return original(target, *args, **kwargs)

        monkeypatch.setattr(type(path), "read_text", deny)
    with pytest.raises(StateSchemaIncompatibleError):
        spool.recover()
    assert all(entry.path.exists() for entry in entries)


@pytest.mark.parametrize(
    "state,detail", [("queued", "x"), ("stuck", "x"), (None, "x"), ("sent", None)]
)
def test_settle_rejects_an_invalid_result_before_writing(tmp_path, state, detail):
    spool = Spool(tmp_path)
    spool.submit(SendCommand("123", "synthetic", "b"))
    entry = spool.take(1)[0]
    with pytest.raises(ValidationError):
        spool.settle(entry, state=state, detail=detail)
    assert entry.path.exists()
    assert not (tmp_path / "done/b.json").exists()


@pytest.mark.parametrize("foreign_path", [False, True])
def test_settle_cannot_remove_another_task(tmp_path, foreign_path):
    spool = Spool(tmp_path / "first")
    other = Spool(tmp_path / "second")
    for queue in (spool, other):
        queue.submit(SendCommand("123", "synthetic", "b"))
    entry, foreign = spool.take(1)[0], other.take(1)[0]
    invalid = foreign if foreign_path else replace(entry, command=SendCommand("123", "x", "wrong"))
    with pytest.raises(ValidationError):
        spool.settle(invalid, state="sent", detail="confirmed")
    assert entry.path.exists() and foreign.path.exists()
    assert list((tmp_path / "first/done").iterdir()) == []


@pytest.mark.parametrize(
    "extra",
    [
        ',"text":"changed"',
        ',"extra":{"x":1,"x":2}',
        ',"extra":NaN',
        ',"extra":1e9999',
        ',"declared_cold":"true"',
    ],
)
def test_ambiguous_command_is_preserved_in_stuck(tmp_path, extra):
    spool = Spool(tmp_path)
    body = '{"chat_id":"123","text":"original","idempotency_key":"b"' + extra + "}"
    path = tmp_path / "ready/000000000001-b.json"
    path.write_text(body, encoding="utf-8")
    assert spool.take(1) == []
    assert spool.stuck == ("b",)
    assert (tmp_path / "stuck" / path.name).read_text(encoding="utf-8") == body
    assert spool.outcome("b").state == "stuck"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_state_rejects_nonfinite_json_before_update(tmp_path, number):
    state = StateFile(tmp_path / "state.json")
    state.save({"marker": 0})
    body = state.path.read_text().replace('"marker":0', '"marker":' + number)
    state.path.write_text(body)
    with pytest.raises(StateSchemaIncompatibleError):
        state.update({"replacement": True})
    assert state.path.read_text() == body


@pytest.mark.parametrize("state", ["sent", "refused", "stuck"])
def test_valid_receipt_survives_recovery_unchanged(tmp_path, state):
    spool = Spool(tmp_path)
    spool.submit(SendCommand("123", "synthetic", "b"))
    entry = spool.take(1)[0]
    result = tmp_path / "done/b.json"
    body = json.dumps(dict(OUTCOME, state=state, detail="original detail"))
    result.write_text(body)
    assert spool.outcome("b").state == state
    assert spool.recover() == (("b",) if state == "stuck" else ())
    assert result.read_text() == body
    assert not entry.path.exists()
    assert spool.stuck == (("b",) if state == "stuck" else ())
    assert spool.take(1) == []


def test_dangling_receipt_link_does_not_get_overwritten(tmp_path):
    spool, entries = taken_pair(tmp_path)
    path = tmp_path / "done/b.json"
    try:
        path.symlink_to(tmp_path / "missing.json")
    except OSError:
        pytest.skip("символические ссылки недоступны")
    with pytest.raises(StateSchemaIncompatibleError):
        spool.recover()
    assert path.is_symlink()
    assert all(entry.path.exists() for entry in entries)
