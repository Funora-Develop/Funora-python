"""Рынок: наблюдаемые изменения, подтверждение отсутствия и общий допуск."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fractions import Fraction
from threading import Barrier

import pytest
from test_catalog import WHEN
from test_client import _FakeFetcher, _observation
from test_market_money import html

from funora import AsyncClient, Budget, Client, EventType, MarketWatch, Router
from funora._market import parse_market
from funora._monitoring import initial_cursor, observe_market, validate_market_cursor
from funora._snapshot import snapshot_of
from funora._state import StateFile
from funora._watch_state import PendingBatch
from funora.budget import RequestClass
from funora.errors import (
    BudgetExhaustedError,
    ConfigurationError,
    CursorIncompatibleError,
    StateSchemaIncompatibleError,
    ValidationError,
)

WATCH = MarketWatch("prices", "922")


def snapshot(price="1.16", *, complete=True, absent=False, offer="123", node="922"):
    page = parse_market(html(price).replace("id=123", f"id={offer}"), observed_at=WHEN)
    result = snapshot_of(page, node_id=node)
    if absent:
        result = replace(result, offers={}, rows_total=0, rows_accepted=0)
    if not complete:
        result = replace(
            result, completeness=type(result.completeness).UNKNOWN, reason="integrity_unverified"
        )
    return result


def history(*pages, watch=WATCH, account_id="self"):
    cursor = initial_cursor((watch,))
    events = []
    for page in pages:
        cursor, batch = observe_market(cursor, watch, page, account_id=account_id)
        validate_market_cursor(cursor)
        events.extend(batch)
    return cursor, events


def test_cold_start_and_recurrence_have_durable_revisions():
    cursor, events = history(snapshot(), snapshot("2"), snapshot(), snapshot("2"))
    assert [one.type for one in events] == [EventType.WATCH_PRIMED] + [
        EventType.MARKET_PRICE_CHANGED
    ] * 3
    assert len({one.id for one in events}) == 4
    assert all(one.ordering_key == "watch:prices" for one in events)
    assert cursor["market"]["prices"]["sequence"] == 4
    assert all(one.observed_at == WHEN for one in events), "новизна не зависит от часов"


def test_disappearance_needs_consecutive_complete_snapshots():
    cursor, events = history(
        snapshot(),
        snapshot(absent=True),
        snapshot(absent=True, complete=False),
        snapshot(absent=True),
    )
    assert EventType.MARKET_OFFER_DISAPPEARED not in {one.type for one in events}
    assert cursor["market"]["prices"]["absences"] == {"123": 1}
    cursor, events = observe_market(cursor, WATCH, snapshot(absent=True), account_id="self")
    assert len(events) == 1 and events[0].type is EventType.MARKET_OFFER_DISAPPEARED
    assert events[0].payload["consecutive_absences"] == 2
    assert events[0].payload["filters_affect_visibility"] is False
    assert cursor["market"]["prices"]["offers"] == {}
    _, returned = observe_market(cursor, WATCH, snapshot(), account_id="self")
    assert returned[0].type is EventType.MARKET_OFFER_APPEARED
    assert returned[0].payload["seller_id"] == "456"


def test_unknown_initial_snapshot_does_not_invent_appearances():
    _, events = history(
        snapshot(complete=False),
        snapshot(offer="124", complete=False),
        snapshot(offer="125"),
        snapshot(offer="126"),
    )
    appeared = [one for one in events if one.type is EventType.MARKET_OFFER_APPEARED]
    assert [one.entity_id for one in appeared] == ["126"]


def test_positive_partial_observations_preserve_prices_and_reset_absences():
    cursor, events = history(
        snapshot(),
        snapshot(absent=True),
        snapshot("?", complete=False),
        snapshot("2", complete=False),
    )
    prices = [one for one in events if one.type is EventType.MARKET_PRICE_CHANGED]
    assert len(prices) == 1
    assert prices[0].payload["before"]["amount_minor"] == 1160000
    assert prices[0].payload["latest"]["amount_minor"] == 2000000
    assert cursor["market"]["prices"]["absences"] == {}


def test_same_offer_in_two_watches_gets_different_identity():
    ids = []
    for watch in (WATCH, replace(WATCH, watch_id="other")):
        _, events = history(snapshot(), snapshot("2"), watch=watch)
        ids.append(events[-1].id)
    assert len(set(ids)) == 2


def test_reading_another_query_is_rejected():
    with pytest.raises(ValidationError):
        history(snapshot(node="999"))


@pytest.mark.parametrize(
    "change",
    [
        {"watch_id": ""},
        {"watch_id": "русский"},
        {"watch_id": "x\x1fy"},
        {"node_id": "../"},
        {"node_id": "１"},
        {"interval_ms": 0},
        {"interval_ms": -1},
        {"interval_ms": True},
        {"interval_ms": float("inf")},
    ],
)
def test_watch_validation(change):
    with pytest.raises(ValidationError):
        replace(WATCH, **change)


def test_forecast_is_exact_and_does_not_consume_tokens():
    budget = Budget()
    one = replace(WATCH, interval_ms=20000)
    before = [bucket.tokens for bucket in budget._buckets]
    plan = budget.monitoring_plan((one,), 0)
    assert plan.admitted and plan.requests_per_second == Fraction(1, 20)
    assert [(limit.bucket, limit.available_per_second) for limit in plan.limits] == [
        ("host", Fraction(3, 20)),
        ("account", Fraction(3, 40)),
    ]
    assert before == [bucket.tokens for bucket in budget._buckets]
    assert not budget.monitoring_plan((replace(one, interval_ms=10000),), 0).admitted


def test_registrations_share_host_but_have_separate_account_limits():
    root = Budget()
    a, b = root.for_account("a"), root.for_account("b")
    first = replace(WATCH, interval_ms=20000)
    second = replace(first, watch_id="second")
    with a.admit_monitoring((first,), 0):
        assert not a.monitoring_plan((second,), 0).admitted
        assert b.monitoring_plan((second,), 0).admitted
        with b.admit_monitoring((second,), 0):
            third = root.for_account("third")
            assert not third.monitoring_plan(
                (replace(second, watch_id="third", interval_ms=13334),), 0
            ).admitted
    assert a.monitoring_plan((first,), 0).admitted


def test_suspend_scale_and_exception_release_registration():
    budget = Budget()
    watch = replace(WATCH, interval_ms=20000)
    budget.scale(Fraction(1, 2))
    assert not budget.monitoring_plan((watch,), 0).admitted
    budget.suspend((RequestClass.MONITORING,), until=10)
    assert budget.monitoring_plan((WATCH,), 0).reason == "monitoring_suspended"
    with pytest.raises(RuntimeError), budget.admit_monitoring((WATCH,), 10):
        raise RuntimeError("выход")
    assert budget.monitoring_plan((WATCH,), 10).admitted


def test_parallel_admission_is_atomic():
    budget = Budget()
    barrier = Barrier(2)

    def register(index):
        watch = replace(WATCH, watch_id=str(index), interval_ms=20000)
        barrier.wait()
        try:
            with budget.admit_monitoring((watch,), 0):
                barrier.wait()
                return True
        except BudgetExhaustedError:
            barrier.wait()
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(register, (1, 2))) == [False, True]
    assert not budget._monitoring


@pytest.fixture
def virtual_time(monkeypatch):
    now = [1000.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    async def asleep(seconds):
        sleep(seconds)

    for name in ("_engine", "_client", "_aclient"):
        monkeypatch.setattr(f"funora.{name}.monotonic", lambda: now[0])
    monkeypatch.setattr("funora._client.sleep", sleep)
    monkeypatch.setattr("funora._aclient.asyncio.sleep", asleep)
    return now, sleeps


def test_real_sync_driver_persists_prices_and_retries_without_http(tmp_path, virtual_time):
    path = tmp_path / "market.json"
    transport = _FakeFetcher([_observation(html()), _observation(html("2"))])
    seen = []
    router = Router()

    @router.on(EventType.MARKET_PRICE_CHANGED)
    def handler(event):
        seen.append(event)
        if len(seen) == 1:
            event.payload["latest"]["amount_minor"] = 999
            raise ValueError("повтор")

    with Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client:
        client.monitoring.watch(router, WATCH, state_path=path, max_iterations=3)
        assert client.monitoring.plan(WATCH).admitted
    assert transport.calls == 2 and len(seen) == 2
    assert seen[0].id == seen[1].id and seen[1].delivery.attempt == 2
    assert seen[1].payload["latest"]["amount_minor"] == 2000000
    state = StateFile(path).load()
    assert state["watch_pending"] is None
    assert state["cursor"]["market"]["prices"]["sequence"] == 2
    assert "outbound" not in state


def test_restart_replays_before_reading_and_continues_sequence(tmp_path, virtual_time):
    path = tmp_path / "market.json"
    seen = []
    router = Router()

    @router.on(EventType.MARKET_PRICE_CHANGED)
    def interrupt(event):
        seen.append(event)
        raise KeyboardInterrupt

    with (
        Client(
            public_only=True,
            public_transport=_FakeFetcher([_observation(html()), _observation(html("2"))]),
            budget=Budget(names=()),
        ) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        client.monitoring.watch(router, WATCH, state_path=path, max_iterations=2)
    router = Router()
    router.on(EventType.MARKET_PRICE_CHANGED)(seen.append)
    transport = _FakeFetcher([_observation(html())])
    with Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client:
        client.monitoring.watch(router, WATCH, state_path=path, max_iterations=2)
    assert transport.calls == 1 and len(seen) == 3
    assert seen[0].id == seen[1].id != seen[2].id
    assert [one.delivery.attempt for one in seen] == [1, 2, 1]


def test_async_cancellation_releases_file_and_admission(tmp_path, virtual_time):
    path = tmp_path / "market.json"
    budget = Budget(names=())
    seen = []

    class Transport:
        def __init__(self):
            self.pages = iter([_observation(html()), _observation(html("2"))])

        async def fetch(self, _path):
            return next(self.pages)

        async def close(self):
            pass

    async def run():
        task = asyncio.current_task()
        router = Router()

        @router.on(EventType.MARKET_PRICE_CHANGED)
        async def cancel(event):
            seen.append(event)
            task.cancel()
            await asyncio.Future()

        async with AsyncClient(
            public_only=True, public_transport=Transport(), budget=budget
        ) as client:
            with pytest.raises(asyncio.CancelledError):
                await client.monitoring.watch(router, WATCH, state_path=path, max_iterations=2)
            assert client.monitoring.plan(WATCH).admitted
            router = Router()
            router.on(EventType.MARKET_PRICE_CHANGED)(seen.append)
            await client.monitoring.watch(router, WATCH, state_path=path, max_iterations=1)

    asyncio.run(run())
    assert seen[0].id == seen[1].id and seen[1].delivery.attempt == 2


def test_scheduling_multiple_watches_has_no_catch_up_burst(virtual_time):
    transport = _FakeFetcher([_observation(html())] * 5)
    router = Router()
    seen = []
    router.on()(seen.append)
    with Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client:
        client.monitoring.watch(
            router, MarketWatch("a", "1", 20000), MarketWatch("b", "2", 50000), max_iterations=5
        )
    assert transport.calls == 5
    assert {one.payload["watch_id"] for one in seen} == {"a", "b"}
    assert virtual_time[0][0] >= 1050


@pytest.mark.parametrize("change", [{"watch_id": "other"}, {"node_id": "1"}, {"interval_ms": 1000}])
def test_foreign_config_rejected_before_http(tmp_path, virtual_time, change):
    path = tmp_path / "market.json"
    transport = _FakeFetcher([_observation(html())])
    with Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client:
        client.monitoring.watch(Router(), WATCH, state_path=path, max_iterations=1)
        with pytest.raises(CursorIncompatibleError):
            client.monitoring.watch(
                Router(), replace(WATCH, **change), state_path=path, max_iterations=1
            )
    assert transport.calls == 1


def test_budget_rejection_happens_before_http_and_file_write(tmp_path):
    path = tmp_path / "market.json"
    transport = _FakeFetcher([])
    with (
        Client(public_only=True, public_transport=transport, budget=Budget()) as client,
        pytest.raises(BudgetExhaustedError, match="prices"),
    ):
        client.monitoring.watch(
            Router(), replace(WATCH, interval_ms=1), state_path=path, max_iterations=1
        )
    assert transport.calls == 0 and not path.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("sequence", -1),
        ("sequence", True),
        ("baseline", 1),
        ("offers", []),
        ("absences", {"other": 1}),
        ("config", {}),
    ],
)
def test_corrupt_history_is_not_silently_replaced(field, value):
    cursor, _ = history(snapshot())
    cursor["market"]["prices"][field] = value
    with pytest.raises(ValueError):
        validate_market_cursor(cursor)


def test_pending_market_events_cannot_change_watch_owner():
    cursor, events = history(snapshot(), snapshot("2"))
    pending = PendingBatch(tuple(events[-1:]), cursor, True).encode()
    record = json.loads(pending)
    record["events"][0]["payload"]["watch_id"] = "other"
    with pytest.raises(StateSchemaIncompatibleError):
        PendingBatch.decode(json.dumps(record), account_id="self")


def test_new_watch_validation_has_no_io():
    with pytest.raises(ValidationError):
        Budget().monitoring_plan((), 0)
    with pytest.raises(ValidationError):
        Budget().monitoring_plan((WATCH, WATCH), 0)


def test_price_formatting_is_not_a_change():
    _, events = history(snapshot("2.00"), snapshot("2.000000"))
    assert [event.type for event in events] == [EventType.WATCH_PRIMED]


def test_market_fingerprint_uses_declared_sequence_and_watch():
    import hashlib

    _, events = history(snapshot(), snapshot("2"))
    material = 'self\x1fmarket.price_changed\x1f123\x1f["prices",2]'
    assert events[-1].id == hashlib.blake2s(material.encode(), digest_size=16).hexdigest()
    _, events_later = history(snapshot(), replace(snapshot("2"), taken_at=WHEN.replace(year=2027)))
    assert events[-1].id == events_later[-1].id


@pytest.mark.parametrize(
    "path,value",
    [
        ("sequence", 100),
        ("sequence", 0),
        ("config.node_id", "999"),
        ("offers.123.price.amount_minor", True),
        ("offers.123.price.currency", "rub"),
        ("offers.123.price", {}),
        ("offers.123.seller_id", "../"),
        ("absences.123", 2),
    ],
)
def test_corrupt_pending_snapshot_is_rejected_before_replay(tmp_path, virtual_time, path, value):
    state_path = tmp_path / "market.json"
    base, _ = history(snapshot())
    target, events = observe_market(base, WATCH, snapshot("2"), account_id="self")
    raw = json.loads(PendingBatch(events, target, True).encode())
    field = raw["cursor"]["market"]["prices"]
    parts = path.split(".")
    for key in parts[:-1]:
        field = field[key]
    field[parts[-1]] = value
    StateFile(state_path).update(
        {
            "watch_owner": "self",
            "watch_greeted": True,
            "cursor": base,
            "watch_pending": json.dumps(raw),
        }
    )
    transport = _FakeFetcher([])
    seen = []
    router = Router()
    router.on()(seen.append)
    with (
        Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client,
        pytest.raises((CursorIncompatibleError, StateSchemaIncompatibleError)),
    ):
        client.monitoring.watch(router, WATCH, state_path=state_path, max_iterations=1)
    assert not seen and transport.calls == 0


@pytest.mark.parametrize("iterations", [-1, True, 1.5])
def test_invalid_iteration_limit_is_rejected(iterations):
    from funora._engine import Engine
    from funora._transport import TransportSettings

    with pytest.raises(ConfigurationError):
        next(
            Engine(TransportSettings(), Budget(names=())).monitor_market(
                (WATCH,), account_id="self", max_iterations=iterations
            )
        )


def test_runtime_interval_rejects_float_overflow():
    with pytest.raises(ValidationError):
        replace(WATCH, interval_ms=10**10000)


def test_active_watch_id_cannot_be_registered_twice():
    budget = Budget(names=())
    with budget.admit_monitoring((WATCH,), 0):
        assert budget.monitoring_plan((WATCH,), 0).reason == "watch_id_in_use"


def test_empty_run_releases_lease_without_writing_snapshot(tmp_path):
    state = tmp_path / "state.json"
    with Client(
        public_only=True, public_transport=_FakeFetcher([]), budget=Budget(names=())
    ) as client:
        client.monitoring.watch(Router(), WATCH, state_path=state, max_iterations=0)
        assert client.monitoring.plan(WATCH).admitted
    assert not state.exists()


def test_monitoring_httpx_lane_never_receives_private_cookie(monkeypatch, virtual_time):
    import httpx
    from test_public_transport import _response

    from funora import Secret

    original = httpx.Client
    requests = []

    def handle(request):
        requests.append(request)
        return _response(html("2" if len(requests) == 2 else "1.16"))

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original(**kwargs)

    monkeypatch.setattr("funora._transport.httpx.Client", factory)
    seen = []
    router = Router()
    router.on(EventType.MARKET_PRICE_CHANGED)(seen.append)
    with Client(Secret("private-test-key"), budget=Budget(names=())) as client:
        client.monitoring.watch(router, WATCH, max_iterations=2)
    assert len(seen) == 1 and len(requests) == 2
    assert all(
        request.url.path == "/lots/922/" and "golden_key" not in request.headers.get("cookie", "")
        for request in requests
    )


def test_personal_watch_rejects_market_file(tmp_path, virtual_time):
    from test_client import _page

    path = tmp_path / "state.json"
    with Client(
        public_only=True,
        public_transport=_FakeFetcher([_observation(html())]),
        budget=Budget(names=()),
    ) as client:
        client.monitoring.watch(Router(), WATCH, state_path=path, max_iterations=1)
    transport = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    with (
        Client(transport=transport, budget=Budget(names=())) as client,
        pytest.raises(CursorIncompatibleError, match="monitoring"),
    ):
        client.watch(Router(), state_path=path, max_iterations=1, use_channel=False)
    assert transport.calls == 0


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("price", "latest", None),
        ("price", "latest", {"amount_minor": True, "currency": "RUB", "scale": 6}),
        ("price", "latest", {"amount_minor": 1, "currency": "RUB", "scale": 7}),
        ("price", "aggregate", {}),
        ("price", "extra", True),
        ("appeared", "seller_id", "../"),
        ("appeared", "query_fingerprint", "other"),
        ("disappeared", "consecutive_absences", True),
        ("disappeared", "filters_affect_visibility", True),
    ],
)
def test_pending_payload_validation(kind, field, value):
    if kind == "price":
        cursor, events = history(snapshot(), snapshot("2"))
    elif kind == "appeared":
        cursor, events = history(snapshot(), snapshot(offer="124"))
    else:
        cursor, events = history(snapshot(), snapshot(absent=True), snapshot(absent=True))
    event = events[-1]
    event.payload[field] = value
    with pytest.raises(StateSchemaIncompatibleError):
        PendingBatch.decode(PendingBatch((event,), cursor, True).encode(), account_id="self")


def test_market_history_without_owner_cannot_be_adopted(tmp_path):
    path = tmp_path / "state.json"
    cursor, _ = history(snapshot())
    StateFile(path).update({"cursor": cursor})
    transport = _FakeFetcher([])
    with (
        Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client,
        pytest.raises(CursorIncompatibleError),
    ):
        client.monitoring.watch(Router(), WATCH, state_path=path, max_iterations=1)
    assert transport.calls == 0


def test_reserved_control_identity_is_rejected():
    with (
        Client(
            public_only=True, public_transport=_FakeFetcher([]), budget=Budget(names=())
        ) as client,
        pytest.raises(ConfigurationError, match="account_id"),
    ):
        client.monitoring.watch(Router(), replace(WATCH, watch_id="self"), max_iterations=0)


def test_admitted_default_set_does_not_exhaust_budget_at_cold_start(virtual_time):
    watches = tuple(MarketWatch(f"watch-{index}", str(index + 1)) for index in range(9))
    transport = _FakeFetcher([_observation(html())] * len(watches))
    with Client(public_only=True, public_transport=transport, budget=Budget()) as client:
        assert client.monitoring.plan(*watches).admitted
        client.monitoring.watch(Router(), *watches, max_iterations=len(watches))
    assert transport.calls == 9
