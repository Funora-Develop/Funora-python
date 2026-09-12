"""Повреждение истории не разрешает повторную выдачу или ложные события."""

import copy
from dataclasses import replace

import pytest
from test_auto_delivery import LOTS, _order
from test_client import _page
from test_dispatch_concurrent import BATCH
from test_monitoring import WATCH, history, snapshot
from test_parser_failures import WHEN

from funora import _diff, _retry
from funora._chats import parse_chats_page
from funora._delivered import DeliveryLedger
from funora._listen import seed_tags
from funora._monitoring import (
    observe_market,
    validate_market_cursor,
    validate_market_transition,
)
from funora._observed import Observed
from funora._outbound import OutboundGovernor
from funora._result import Completeness
from funora._runner import parse_runner_context
from funora._state import StateFile
from funora._thread import parse_thread
from funora._watch_state import PendingBatch
from funora.bot._delivery import AutoDelivery, DeliveryPlan
from funora.errors import ConfigurationError, StateSchemaIncompatibleError, TimeoutError
from funora.events import EventType
from funora.operations import Safety
from funora.retry import RetryDecision


@pytest.mark.parametrize(
    "value", [None, {}, {"market": {}}, {"market": []}, {"market": {"prices": []}}]
)
def test_market_cursor_shape_cannot_be_replaced_by_cold_start(value):
    with pytest.raises(ValueError):
        validate_market_cursor(value)


@pytest.mark.parametrize("problem", ["config", "offer-id", "offer-shape"])
def test_market_cursor_invalid_record_is_refused_atomically(problem):
    cursor, _ = history(snapshot())
    record = cursor["market"][WATCH.watch_id]
    if problem == "config":
        record["config"]["interval_ms"] = -1
    elif problem == "offer-id":
        record["offers"][""] = record["offers"].pop("123")
    else:
        record["offers"]["123"] = {}
    before = copy.deepcopy(cursor)
    with pytest.raises(ValueError):
        validate_market_cursor(cursor)
    assert cursor == before


def test_market_transition_requires_one_snapshot_and_matching_event():
    cursor, _ = history(snapshot())
    target, events = observe_market(cursor, WATCH, snapshot("2"), account_id="self")
    with pytest.raises(ValueError, match="один снимок"):
        validate_market_transition(cursor, cursor, ())
    bad = replace(events[0], payload={**events[0].payload, "watch_id": "other"})
    with pytest.raises(ValueError, match="разным наблюдениям"):
        validate_market_transition(cursor, target, (bad,))


@pytest.mark.parametrize("kind", ["offer", "event-type", "watch-id"])
def test_market_pending_events_cannot_change_their_entity(kind):
    cursor, events = history(snapshot(), snapshot("2"))
    event = events[-1]
    if kind == "offer":
        event = replace(event, entity_id="456")
    elif kind == "event-type":
        event = replace(event, type=EventType.ORDER_CREATED)
    else:
        event = replace(events[0], entity_id="other")
    with pytest.raises(StateSchemaIncompatibleError):
        PendingBatch.decode(PendingBatch((event,), cursor, True).encode(), account_id="self")


def test_incomplete_observations_do_not_advance_event_cursors():
    chats = parse_chats_page(_page("chat.logged.ru"), observed_at=WHEN)
    chat = replace(chats._entries[0], last_message_position=Observed.missing("absent"))
    chats = replace(chats, _entries=(chat,))
    assert _diff.chats_cursor(chats) == {}
    assert _diff.diff_chats({}, chats, account_id="self") == ()
    thread = parse_thread(_page("chat-thread.logged.ru"), observed_at=WHEN)
    message = replace(thread._messages[0], message_id=Observed.missing("absent"))
    thread = replace(thread, _messages=(message,))
    assert _diff.diff_thread(frozenset(), thread, account_id="self", chat_id="123") == ()
    assert _diff.ordering_keys(BATCH) == {"chat:a", "chat:b", "chat:c"}


def test_unknown_hash_and_missing_retry_rule_fail_closed(monkeypatch):
    monkeypatch.setattr(_diff, "FINGERPRINT_HASH", "unsupported-hash")
    with pytest.raises(ConfigurationError, match="алгоритмом"):
        _diff._fingerprint(
            account_id="self", event_type=EventType.ORDER_CREATED, entity_id="a", revision="1"
        )
    monkeypatch.setattr(_retry, "DECISION_MATRIX", ())
    with pytest.raises(ConfigurationError, match="матрица"):
        _retry.decide(TimeoutError("synthetic"), Safety.SAFE)
    monkeypatch.setattr(_retry, "DECISION_MATRIX", ((True, None, None, RetryDecision.NEVER),))
    attempt = _retry.plan_attempt(TimeoutError("synthetic"), attempt=1)
    assert not attempt.retry and attempt.delay_ms == 0


def test_no_identity_means_no_channel_tags_and_empty_governor_has_no_wait():
    context = parse_runner_context("<body></body>")
    assert seed_tags(context) == {}
    assert OutboundGovernor()._frees_in([], window_ms=100, now_ms=0, now_s=0) == 0


def test_attempts_without_pending_batch_cannot_be_loaded(tmp_path):
    state = StateFile(tmp_path / "state.json")
    state.save({"attempts": {"event": 1}, "watch_pending": None})
    before = state.path.read_bytes()
    with pytest.raises(StateSchemaIncompatibleError, match="без непринятой партии"):
        state.load()
    assert state.path.read_bytes() == before


def test_queue_failure_persists_the_final_delivery_state(tmp_path):
    state = StateFile(tmp_path / "state.json")
    ledger = DeliveryLedger()
    persisted = []

    def persist():
        state.update({"delivery": ledger.snapshot()})
        persisted.append(state.load()["delivery"])

    def send(*args):
        assert state.load()["delivery"]["done"][0]["outcome"] == "queued"
        raise OSError("queue unavailable")

    delivery = AutoDelivery(
        DeliveryPlan(goods={"L2": "товар"}, chat_of=lambda _: "chat"), ledger, send, persist=persist
    )
    with pytest.raises(OSError, match="queue unavailable"):
        delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)
    assert len(persisted) == 2
    assert persisted[-1]["done"][0]["outcome"] == "queue_failed"
    restored = DeliveryLedger()
    restored.restore(state.load()["delivery"])
    assert restored.snapshot() == ledger.snapshot()
