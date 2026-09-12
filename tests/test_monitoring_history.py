"""Предел истории не теряет курсор, цены или непринятую партию."""

import copy
from dataclasses import replace

import pytest
from test_client import _FakeFetcher, _observation
from test_market_money import html
from test_monitoring import WATCH, history, snapshot
from test_monitoring import virtual_time as virtual_time  # noqa: F401
from test_monitoring_timing import run_watch

from funora import Budget, EventType, Router
from funora._monitoring import initial_cursor, observe_market
from funora._state import StateFile
from funora._watch_state import PendingBatch
from funora.budget import MARKET_HISTORY_LIMIT
from funora.errors import ConfigurationError, CursorIncompatibleError


def test_partial_history_stops_at_capacity_without_mutating_previous_cursor():
    cursor = initial_cursor((WATCH,))
    for offer in ("1", "2"):
        cursor, _ = observe_market(
            cursor,
            WATCH,
            snapshot(offer=offer, complete=False),
            account_id="self",
            history_limit=2,
        )
    before = copy.deepcopy(cursor)
    with pytest.raises(ConfigurationError, match="history_limit=2"):
        observe_market(
            cursor,
            WATCH,
            snapshot(offer="3", complete=False),
            account_id="self",
            history_limit=2,
        )
    assert cursor == before
    assert len(cursor["market"]["prices"]["offers"]) == 2


def test_default_capacity_refuses_before_copying_history(monkeypatch):
    cursor, _ = history(snapshot())
    record = cursor["market"]["prices"]
    entry = record["offers"]["123"]
    record["offers"] = {str(i): entry for i in range(MARKET_HISTORY_LIMIT)}
    page = snapshot(offer=str(MARKET_HISTORY_LIMIT))

    def must_not_copy(*args, **kwargs):
        pytest.fail("overflow copied the whole history")

    monkeypatch.setattr("funora._monitoring.json.dumps", must_not_copy)
    with pytest.raises(ConfigurationError, match=f"history_limit={MARKET_HISTORY_LIMIT}"):
        observe_market(cursor, WATCH, page, account_id="self")
    assert len(record["offers"]) == MARKET_HISTORY_LIMIT


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("limit", [0, -1, True, False, 1.0, float("inf"), None, "10"])
def test_invalid_limit_rejected_before_io(asynchronous, limit, tmp_path, virtual_time):
    transport = _FakeFetcher([])
    path = tmp_path / "new" / "state.json"
    with pytest.raises(ConfigurationError, match="положительным целым"):
        run_watch(
            asynchronous,
            transport,
            Router(),
            state_path=path,
            history_limit=limit,
            max_iterations=0,
        )
    assert transport.calls == 0
    assert not path.parent.exists()


def test_same_offer_in_two_watches_counts_twice():
    second = replace(WATCH, watch_id="second")
    cursor = initial_cursor((WATCH, second))
    cursor, _ = observe_market(cursor, WATCH, snapshot(), account_id="self", history_limit=1)
    before = copy.deepcopy(cursor)
    with pytest.raises(ConfigurationError, match="2 записей"):
        observe_market(cursor, second, snapshot(), account_id="self", history_limit=1)
    assert cursor == before
    cursor, _ = observe_market(cursor, second, snapshot(), account_id="self", history_limit=2)
    assert all(len(record["offers"]) == 1 for record in cursor["market"].values())


def test_price_change_at_exact_capacity_preserves_events():
    cursor, _ = history(snapshot())
    target, events = observe_market(
        cursor,
        WATCH,
        snapshot("2", complete=False),
        account_id="self",
        history_limit=1,
    )
    changed = [event for event in events if event.type is EventType.MARKET_PRICE_CHANGED]
    assert len(changed) == 1
    assert changed[0].payload["before"]["amount_minor"] == 1160000
    assert changed[0].payload["latest"]["amount_minor"] == 2000000
    assert target["market"]["prices"]["offers"]["123"]["price"]["amount_minor"] == 2000000
    assert cursor["market"]["prices"]["offers"]["123"]["price"]["amount_minor"] == 1160000


@pytest.mark.parametrize("complete", [False, True])
def test_first_absence_does_not_free_capacity(complete):
    cursor, _ = history(snapshot())
    with pytest.raises(ConfigurationError):
        observe_market(
            cursor,
            WATCH,
            snapshot(offer="456", complete=complete),
            account_id="self",
            history_limit=1,
        )
    assert cursor["market"]["prices"]["absences"] == {}


def test_only_confirmed_disappearance_frees_capacity():
    cursor, _ = history(snapshot(), snapshot(absent=True))
    before = copy.deepcopy(cursor)
    with pytest.raises(ConfigurationError):
        observe_market(
            cursor,
            WATCH,
            snapshot(offer="456", complete=False),
            account_id="self",
            history_limit=1,
        )
    assert cursor == before
    target, events = observe_market(
        cursor,
        WATCH,
        snapshot(offer="456"),
        account_id="self",
        history_limit=1,
    )
    assert set(target["market"]["prices"]["offers"]) == {"456"}
    assert {event.type for event in events} == {
        EventType.MARKET_OFFER_APPEARED,
        EventType.MARKET_OFFER_DISAPPEARED,
    }
    assert cursor == before


def test_reappearing_offer_does_not_free_capacity():
    cursor, _ = history(snapshot(), snapshot(absent=True))
    page = snapshot()
    page = replace(page, offers={"123": page.offers["123"], "456": page.offers["123"]})
    with pytest.raises(ConfigurationError, match="2 записей"):
        observe_market(cursor, WATCH, page, account_id="self", history_limit=1)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_overflow_after_read_preserves_file_handlers_and_registration(
    asynchronous,
    tmp_path,
    virtual_time,
):
    path = tmp_path / "market.json"
    base, _ = history(snapshot())
    StateFile(path).update(
        {"watch_owner": "self", "cursor": base, "watch_greeted": True, "watch_pending": None}
    )
    before = path.read_bytes()
    budget = Budget(names=())
    seen = []
    router = Router()
    router.on()(seen.append)
    transport = _FakeFetcher([_observation(html().replace("id=123", "id=456"))])
    with pytest.raises(ConfigurationError, match="history_limit=1"):
        run_watch(
            asynchronous,
            transport,
            router,
            state_path=path,
            history_limit=1,
            max_iterations=1,
            budget=budget,
        )
    assert transport.calls == 1 and not seen
    assert path.read_bytes() == before
    # Повышенный предел использует прежний файл; предыдущий вызов освободил lease.
    transport = _FakeFetcher([_observation(html().replace("id=123", "id=456"))])
    run_watch(
        asynchronous,
        transport,
        router,
        state_path=path,
        history_limit=2,
        max_iterations=1,
        budget=budget,
    )
    stored = StateFile(path).load()
    assert set(stored["cursor"]["market"]["prices"]["offers"]) == {"123", "456"}
    assert stored["cursor"]["market"]["prices"]["sequence"] == 2
    assert len([event for event in seen if event.type is EventType.MARKET_OFFER_APPEARED]) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("pending", [False, True])
def test_restore_checks_both_cursors_before_http_or_delivery(
    asynchronous,
    pending,
    tmp_path,
    virtual_time,
):
    base, _ = history(snapshot())
    target, events = observe_market(base, WATCH, snapshot(offer="456"), account_id="self")
    path = tmp_path / "market.json"
    patch = {"watch_owner": "self", "cursor": base if pending else target, "watch_greeted": True}
    patch["watch_pending"] = None
    if pending:
        patch["watch_pending"] = PendingBatch(events, target, True).encode()
    StateFile(path).update(patch)
    before = path.read_bytes()
    transport = _FakeFetcher([])
    seen = []
    router = Router()
    router.on()(seen.append)
    with pytest.raises(ConfigurationError, match="history_limit=1"):
        run_watch(
            asynchronous, transport, router, state_path=path, history_limit=1, max_iterations=1
        )
    assert transport.calls == 0 and seen == []
    assert path.read_bytes() == before
    run_watch(
        asynchronous,
        _FakeFetcher([]),
        router,
        state_path=path,
        history_limit=2,
        max_iterations=1 if pending else 0,
    )
    assert [event.id for event in seen] == ([event.id for event in events] if pending else [])
    stored = StateFile(path).load()
    assert stored["cursor"] == target
    assert stored.get("watch_pending") is None


def test_repeated_partial_snapshots_remain_bounded():
    cursor = initial_cursor((WATCH,))
    for index in range(50):
        page = snapshot(offer=str(index), complete=False)
        if index < 10:
            cursor, _ = observe_market(cursor, WATCH, page, account_id="self", history_limit=10)
        else:
            before = copy.deepcopy(cursor)
            with pytest.raises(ConfigurationError):
                observe_market(cursor, WATCH, page, account_id="self", history_limit=10)
            assert cursor == before
    assert len(cursor["market"]["prices"]["offers"]) == 10
    assert cursor["market"]["prices"]["sequence"] == 10


@pytest.mark.parametrize("asynchronous", [False, True])
def test_foreign_pending_cursor_is_validated_before_capacity(
    asynchronous,
    tmp_path,
    virtual_time,
):
    cursor, _ = history(snapshot())
    personal = {"orders": None, "chats": None, "threads": {}, "pending_threads": []}
    path = tmp_path / "market.json"
    StateFile(path).update(
        {
            "watch_owner": "self",
            "cursor": cursor,
            "watch_greeted": True,
            "watch_pending": PendingBatch((), personal, True).encode(),
        }
    )
    before = path.read_bytes()
    transport = _FakeFetcher([])
    with pytest.raises(CursorIncompatibleError):
        run_watch(asynchronous, transport, Router(), state_path=path, history_limit=1)
    assert transport.calls == 0
    assert path.read_bytes() == before
