"""Интервалы рынка учитывают чтение, бюджет и подтверждение партии."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from test_client import _observation
from test_market_money import html
from test_monitoring import WATCH, history, snapshot
from test_monitoring import virtual_time as virtual_time  # noqa: F401

from funora import AsyncClient, Budget, Client, EventType, MarketWatch, Router
from funora._monitoring import initial_cursor, observe_market
from funora._state import StateFile
from funora._watch_state import PendingBatch
from funora.errors import StateSchemaIncompatibleError, ValidationError


def run_watch(asynchronous, transport, router, watches=(WATCH,), **kwargs):
    budget = kwargs.pop("budget", Budget(names=()))
    if asynchronous:

        class AsyncTransport:
            async def fetch(self, path):
                return transport.fetch(path)

            async def close(self):
                transport.close()

        async def run():
            async with AsyncClient(
                public_only=True, public_transport=AsyncTransport(), budget=budget
            ) as client:
                await client.monitoring.watch(router, *watches, **kwargs)

        asyncio.run(run())
    else:
        with Client(public_only=True, public_transport=transport, budget=budget) as client:
            client.monitoring.watch(router, *watches, **kwargs)


class TimedTransport:
    def __init__(self, clock, duration=30):
        self.clock = clock
        self.duration = duration
        self.starts = []
        self.paths = []

    def fetch(self, path):
        self.starts.append(self.clock[0])
        self.paths.append(path)
        self.clock[0] += self.duration
        return _observation(html(str(len(self.starts))))

    def close(self):
        pass


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("limited", [False, True])
def test_read_and_handler_time_do_not_accumulate(asynchronous, limited, virtual_time):
    now, _ = virtual_time
    transport = TimedTransport(now)
    router = Router()
    seen = []

    @router.on()
    def handle(event):
        seen.append(event)
        now[0] += 20

    run_watch(
        asynchronous,
        transport,
        router,
        budget=Budget() if limited else Budget(names=()),
        max_iterations=4,
    )
    assert transport.starts == pytest.approx([1000, 1120, 1240, 1360], abs=0.005)
    assert EventType.WATCH_DEGRADED not in {event.type for event in seen}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_global_budget_gap_is_measured_from_read_start(asynchronous, virtual_time):
    now, _ = virtual_time
    transport = TimedTransport(now, duration=5)
    watches = tuple(MarketWatch(f"w{i}", str(i + 1)) for i in range(9))
    run_watch(asynchronous, transport, Router(), watches, budget=Budget(), max_iterations=10)
    assert transport.starts == pytest.approx([1000 + i * (40 / 3) for i in range(10)], abs=0.01)
    assert transport.paths[0] == transport.paths[9]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_slow_reads_report_each_missed_interval_without_catch_up(asynchronous, virtual_time):
    now, sleeps = virtual_time
    transport = TimedTransport(now, duration=300)
    seen = []
    router = Router()
    router.on()(seen.append)
    run_watch(asynchronous, transport, router, max_iterations=3)
    assert transport.starts == [1000, 1300, 1600]
    assert sleeps == []
    assert [event.type for event in seen] == [
        EventType.WATCH_PRIMED,
        EventType.WATCH_DEGRADED,
        EventType.MARKET_PRICE_CHANGED,
        EventType.WATCH_DEGRADED,
        EventType.MARKET_PRICE_CHANGED,
    ]
    assert (
        seen[1].payload
        == seen[3].payload
        == {
            "watch_id": "prices",
            "requested_interval_ms": 120000,
            "effective_interval_ms": 300000,
            "reason_code": "schedule_overrun",
        }
    )
    assert seen[1].id != seen[3].id
    assert seen[1].ordering_key == seen[3].ordering_key == "watch:prices"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_overdue_watches_keep_global_gap_and_take_one_snapshot_each(asynchronous, virtual_time):
    now, _ = virtual_time
    transport = TimedTransport(now, duration=0)
    router = Router()

    @router.on(EventType.WATCH_PRIMED)
    def stall_once(event):
        if event.payload["watch_id"] == "a":
            now[0] += 500

    run_watch(
        asynchronous,
        transport,
        router,
        (MarketWatch("a", "1"), MarketWatch("b", "2")),
        budget=Budget(),
        max_iterations=5,
    )
    assert transport.paths == ["/lots/1/", "/lots/2/", "/lots/1/", "/lots/2/", "/lots/1/"]
    assert transport.starts == pytest.approx([1000, 1500, 1513.334, 1620, 1633.334], abs=0.004)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_degradation_replays_before_prices_and_http_after_restart(
    asynchronous, virtual_time, tmp_path
):
    now, _ = virtual_time
    transport = TimedTransport(now, duration=300)
    path = tmp_path / "market.json"
    router = Router()
    seen = []

    @router.on(EventType.WATCH_DEGRADED)
    def fail(event):
        seen.append(event)
        event.payload["effective_interval_ms"] = 0
        raise ValueError("повтор")

    @router.on(EventType.MARKET_PRICE_CHANGED)
    def price(_event):
        pytest.fail("цена не должна обгонять управляющее событие той же партии")

    run_watch(asynchronous, transport, router, state_path=path, max_iterations=3)
    assert len(transport.starts) == 2
    assert [event.delivery.attempt for event in seen] == [1, 2]
    state = StateFile(path).load()
    assert state["cursor"]["market"]["prices"]["sequence"] == 1
    assert state["watch_pending"] is not None

    now[0] += 86400
    replayed = []
    router = Router()
    router.on()(replayed.append)
    run_watch(asynchronous, transport, router, state_path=path, max_iterations=2)
    assert len(transport.starts) == 3
    assert [event.type for event in replayed] == [
        EventType.WATCH_DEGRADED,
        EventType.MARKET_PRICE_CHANGED,
        EventType.MARKET_PRICE_CHANGED,
    ]
    assert replayed[0].id == seen[0].id == seen[1].id
    assert replayed[0].delivery.attempt == 3
    assert replayed[0].payload["effective_interval_ms"] == 300000
    state = StateFile(path).load()
    assert state["watch_pending"] is None
    assert state["cursor"]["market"]["prices"]["sequence"] == 3


@pytest.mark.parametrize("elapsed", [None, 0, 120001, 239999, 240000, 240001])
def test_degradation_requires_a_whole_missed_interval(elapsed):
    cursor, _ = history(snapshot())
    _, events = observe_market(
        cursor, WATCH, snapshot(), account_id="self", read_interval_ms=elapsed
    )
    degraded = [event for event in events if event.type is EventType.WATCH_DEGRADED]
    assert bool(degraded) == (elapsed is not None and elapsed >= 240000)


def test_degradation_revision_uses_sequence_instead_of_time():
    cursor, _ = history(snapshot())
    page = snapshot()
    _, events = observe_market(cursor, WATCH, page, account_id="self", read_interval_ms=300000)
    material = 'self\x1fwatch.degraded\x1fprices\x1f["prices",2]'
    assert events[0].id == hashlib.blake2s(material.encode(), digest_size=16).hexdigest()
    _, later = observe_market(
        cursor,
        WATCH,
        replace(page, taken_at=page.taken_at.replace(year=2027)),
        account_id="self",
        read_interval_ms=400000,
    )
    assert events[0].id == later[0].id


@pytest.mark.parametrize("elapsed", [True, -1, 1.5, "240000"])
def test_invalid_measured_interval_is_rejected(elapsed):
    with pytest.raises(ValidationError):
        observe_market(
            initial_cursor((WATCH,)), WATCH, snapshot(), account_id="self", read_interval_ms=elapsed
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("watch_id", "another"),
        ("requested_interval_ms", True),
        ("requested_interval_ms", 0),
        ("requested_interval_ms", 120001),
        ("effective_interval_ms", 239999),
        ("effective_interval_ms", True),
        ("effective_interval_ms", "300000"),
        ("reason_code", "network_error"),
        ("extra", "unknown"),
    ],
)
def test_corrupt_degradation_cannot_be_replayed(field, value):
    cursor, _ = history(snapshot())
    target, events = observe_market(
        cursor, WATCH, snapshot(), account_id="self", read_interval_ms=300000
    )
    raw = json.loads(PendingBatch(events, target, True).encode())
    raw["events"][0]["payload"][field] = value
    with pytest.raises(StateSchemaIncompatibleError):
        PendingBatch.decode(json.dumps(raw), account_id="self")
