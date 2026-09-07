"""Замеченное изменение переживает новый ответ площадки и прерванную доставку."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from threading import Lock

import pytest
from test_watch import _Cycle, _event, _page, _renamed_first_order
from test_watch import no_sleep as no_sleep

from funora import AsyncClient, Client, Event, EventType, Router
from funora._state import STATE_FORMAT, StateFile
from funora._watch import adispatch, dispatch, primed
from funora._watch_state import PendingBatch, watch_lease
from funora.errors import ConfigurationError, CursorIncompatibleError, StateSchemaIncompatibleError


def test_failed_event_survives_disappearance(no_sleep: list[float]) -> None:
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    transport = _Cycle([orders, chats, _renamed_first_order(orders), chats, orders, chats])
    seen: list[Event] = []
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event)
        if len(seen) == 1:
            raise ValueError("повторить доставку")

    with Client(transport=transport) as client:
        client.watch(router, max_iterations=3, use_channel=False)

    assert len(seen) == 2
    assert seen[0].id == seen[1].id
    assert seen[0].observed_at == seen[1].observed_at
    assert seen[0].payload == seen[1].payload
    assert [event.delivery.attempt for event in seen] == [1, 2]
    assert transport.calls == 4, "повтор доставки не требует нового снимка"


def test_interrupted_delivery_survives_restart(no_sleep: list[float], tmp_path: Path) -> None:
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    state = tmp_path / "state.json"
    router = Router()
    first: list[Event] = []

    @router.on(EventType.ORDER_CREATED)
    def stop(event: Event) -> None:
        first.append(event)
        raise KeyboardInterrupt

    with (
        Client(transport=_Cycle([orders, chats, _renamed_first_order(orders), chats])) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        client.watch(router, state_path=state, max_iterations=2, use_channel=False)

    seen: list[Event] = []
    resumed = Router()
    resumed.on(EventType.ORDER_CREATED)(seen.append)
    transport = _Cycle([orders, chats])
    with Client(transport=transport) as client:
        client.watch(resumed, state_path=state, max_iterations=1, use_channel=False)

    assert len(seen) == 1
    assert seen[0].id == first[0].id
    assert seen[0].payload == first[0].payload
    assert seen[0].observed_at == first[0].observed_at
    assert seen[0].delivery.attempt == 2
    assert transport.calls == 0


def test_failed_key_blocks_only_its_following_events() -> None:
    first = _event(1)
    second = replace(_event(2), ordering_key=first.ordering_key)
    independent = _event(3)
    router = Router()
    seen: list[str] = []

    @router.on()
    def handle(event: Event) -> None:
        seen.append(event.id)
        if event.id == first.id:
            raise ValueError("предыдущий переход не принят")

    result = dispatch(router, (first, second, independent))
    assert seen == [first.id, independent.id]
    assert result.failed == (first, second)
    assert result.delivered == (independent,)
    assert len(result.errors) == 1


def test_payload_mutation_does_not_change_retry(no_sleep: list[float], tmp_path: Path) -> None:
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    state = tmp_path / "state.json"
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def mutate(event: Event) -> None:
        event.payload["order_id"] = "чужой заказ"
        raise ValueError("ошибка обработчика")

    with Client(transport=_Cycle([orders, chats, _renamed_first_order(orders), chats])) as client:
        client.watch(router, state_path=state, max_iterations=2, use_channel=False)
    resumed = Router()
    seen: list[Event] = []
    resumed.on(EventType.ORDER_CREATED)(seen.append)
    with Client(transport=_Cycle([orders, chats])) as client:
        client.watch(resumed, state_path=state, max_iterations=1, use_channel=False)
    assert seen[0].payload["order_id"] == "777"


def test_successful_neighbors_are_not_replayed(no_sleep: list[float], tmp_path: Path) -> None:
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    state = tmp_path / "state.json"
    accepted: list[str] = []
    failed: list[str] = []
    router = Router()

    @router.on()
    def handle(event: Event) -> None:
        if event.type is EventType.SNAPSHOT_INCOMPLETE:
            failed.append(event.id)
            if len(failed) == 1:
                raise ValueError("повторить")
        else:
            accepted.append(event.id)

    damaged = orders.replace("tc-status", "bad-status")
    assert damaged != orders
    transport = _Cycle([damaged, chats])
    with Client(transport=transport) as client:
        client.watch(router, state_path=state, max_iterations=2, use_channel=False)
    assert len(accepted) == 1, "приветствие не повторяется вслед за другим отказом"
    assert len(failed) == 2 and failed[0] == failed[1]
    assert transport.calls == 2
    saved = StateFile(state).load()
    assert saved["watch_pending"] is None
    assert saved["cursor"]["orders"] is None
    assert saved["watch_greeted"] is True


def test_delivery_is_not_called_when_intent_cannot_be_saved(
    no_sleep: list[float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = Router()
    seen: list[Event] = []
    router.on()(seen.append)
    state = tmp_path / "state.json"

    def fail(*args: object) -> None:
        raise OSError("нет места для журнала")

    monkeypatch.setattr(StateFile, "_save", fail)
    with (
        Client(
            transport=_Cycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")])
        ) as client,
        pytest.raises(OSError, match="нет места"),
    ):
        client.watch(router, state_path=state, max_iterations=1, use_channel=False)
    assert seen == []
    assert not state.exists()
    with watch_lease(Lock(), state):
        pass


def test_failed_acknowledgement_replays_same_event(
    no_sleep: list[float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    state = tmp_path / "state.json"
    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)
    save = StateFile._save

    def fail_ack(self: StateFile, payload: dict) -> None:
        if payload.get("watch_pending") is None:
            raise OSError("не удалось подтвердить")
        save(self, payload)

    with monkeypatch.context() as patch:
        patch.setattr(StateFile, "_save", fail_ack)
        with Client(transport=_Cycle([orders, chats])) as client, pytest.raises(OSError):
            client.watch(router, state_path=state, max_iterations=1, use_channel=False)
    assert len(seen) == 1
    with Client(transport=_Cycle([orders, chats])) as client:
        client.watch(router, state_path=state, max_iterations=1, use_channel=False)
    assert len(seen) == 2
    assert seen[0].id == seen[1].id
    assert seen[0].observed_at == seen[1].observed_at
    assert [event.delivery.attempt for event in seen] == [1, 2]
    assert StateFile(state).load()["watch_pending"] is None


def test_callback_failure_releases_watch_owner(no_sleep: list[float], tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    router = Router()

    @router.on()
    def fail(event: Event) -> None:
        raise ValueError("обработчик")

    def callback(error: object) -> None:
        raise RuntimeError("callback")

    with Client(
        transport=_Cycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")])
    ) as client:
        with pytest.raises(RuntimeError, match="callback"):
            client.watch(
                router,
                state_path=state,
                max_iterations=1,
                use_channel=False,
                on_handler_error=callback,
            )
        seen: list[Event] = []
        resumed = Router()
        resumed.on()(seen.append)
        client.watch(resumed, state_path=state, max_iterations=1, use_channel=False)
        assert seen[0].delivery.attempt == 2


class AsyncCycle(_Cycle):
    async def fetch(self, path: str):
        return super().fetch(path)

    async def close(self) -> None:
        pass


def test_async_cancellation_preserves_pending_and_releases_owner(
    no_sleep: list[float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import funora._aclient as async_module

    async def no_wait(*args: object) -> None:
        pass

    monkeypatch.setattr(async_module.asyncio, "sleep", no_wait)

    async def scenario() -> None:
        state = tmp_path / "state.json"
        router = Router()
        entered = asyncio.Event()
        first: list[Event] = []

        @router.on()
        async def block(event: Event) -> None:
            first.append(event)
            entered.set()
            await asyncio.Event().wait()

        transport = AsyncCycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")])
        async with AsyncClient(transport=transport) as client:
            task = asyncio.create_task(
                client.watch(router, state_path=state, max_iterations=1, use_channel=False)
            )
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        resumed = Router()
        seen: list[Event] = []
        resumed.on()(seen.append)
        empty = AsyncCycle([])
        async with AsyncClient(transport=empty) as client:
            await client.watch(resumed, state_path=state, max_iterations=1, use_channel=False)
        assert len(seen) == 1 and seen[0].id == first[0].id
        assert seen[0].observed_at == first[0].observed_at
        assert seen[0].delivery.attempt == 2
        assert empty.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("concurrency", [1, 3])
def test_async_ordering_does_not_overtake_failure(concurrency: int) -> None:
    first = _event(1)
    second = replace(_event(2), ordering_key=first.ordering_key)
    router = Router()
    seen: list[str] = []

    @router.on()
    async def handle(event: Event) -> None:
        seen.append(event.id)
        if event.id == first.id:
            raise ValueError("отказ")

    result = asyncio.run(adispatch(router, (first, second, _event(3)), concurrency=concurrency))
    assert set(seen) == {first.id, "e3"}
    assert {event.id for event in result.failed} == {first.id, second.id}


def _batch() -> PendingBatch:
    event = primed("self", _event(1).observed_at.replace(microsecond=123456), ("orders", "chats"))
    return PendingBatch(
        (event,), {"orders": {}, "chats": {}, "threads": {}, "pending_threads": []}, True
    )


def test_journal_preserves_raw_unicode_and_microseconds(tmp_path: Path) -> None:
    batch = _batch()
    batch.events[0].payload["raw"] = {"e\u0301": ["e\u0301", None, False, 123]}
    batch.cursor["chats"]["12"] = "e\u0301"
    state = StateFile(tmp_path / "state.json")
    state.save({"watch_pending": batch.encode()})
    restored = PendingBatch.decode(state.load()["watch_pending"], account_id="self")
    assert restored == batch


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(version=True),
        lambda value: value.update(version=99),
        lambda value: value.update(greeted=1),
        lambda value: value.update(events=None),
        lambda value: value.update(cursor={}),
        lambda value: value["cursor"].update(orders=[]),
        lambda value: value["cursor"].update(chats={"12": None}),
        lambda value: value["cursor"].update(threads={"12": [None]}),
        lambda value: value["cursor"].update(pending_threads=[False]),
        lambda value: value["events"].append(value["events"][0]),
        lambda value: value["events"][0].update(extra=1),
        lambda value: value["events"][0].update(id=None),
        lambda value: value["events"][0].update(type="market.offer_appeared"),
        lambda value: value["events"][0].update(ordering_key="order:self"),
        lambda value: value["events"][0].update(observed_at="2026-09-08T10:00:00"),
        lambda value: value["events"][0].update(delivery={}),
        lambda value: value["events"][0]["delivery"].update(attempt=True),
        lambda value: value["events"][0]["delivery"].update(attempt=-1),
        lambda value: value["events"][0]["delivery"].update(coalesced=True),
        lambda value: value["events"][0].update(payload=[]),
    ],
)
def test_corrupt_journal_refuses_before_http(
    no_sleep: list[float], tmp_path: Path, mutation
) -> None:
    value = json.loads(_batch().encode())
    mutation(value)
    state = StateFile(tmp_path / "state.json")
    state.save({"watch_pending": json.dumps(value)})
    transport = _Cycle([])
    with Client(transport=transport) as client, pytest.raises(StateSchemaIncompatibleError):
        client.watch(Router(), state_path=state.path, max_iterations=1, use_channel=False)
    assert transport.calls == 0


@pytest.mark.parametrize("raw", [[], "", "null", "{}", '{"version":1,"version":1}', "[" * 1200])
def test_invalid_journal_encoding_is_loud(raw: object) -> None:
    with pytest.raises(StateSchemaIncompatibleError):
        PendingBatch.decode(raw, account_id="self")


def test_wrong_owner_refuses_before_http(no_sleep: list[float], tmp_path: Path) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"watch_pending": _batch().encode()})
    transport = _Cycle([])
    with Client(transport=transport) as client, pytest.raises(CursorIncompatibleError):
        client.watch(
            Router(),
            account_id="another",
            state_path=state.path,
            max_iterations=1,
            use_channel=False,
        )
    assert transport.calls == 0


def test_two_watchers_cannot_own_same_file(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    with (
        watch_lease(Lock(), state),
        Client(transport=_Cycle([])) as client,
        pytest.raises(ConfigurationError),
    ):
        client.watch(Router(), state_path=state, max_iterations=1, use_channel=False)
    with watch_lease(Lock(), state):
        pass


def test_v4_without_pending_attempts_migrates_without_data_loss(tmp_path: Path) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"cursor": _batch().cursor, "outbound": {"marker": 123}, "attempts": {}})
    old = state.path.read_text().replace(STATE_FORMAT, "funora-state-v4")
    state.path.write_text(old)
    state.update({"watch_pending": _batch().encode()})
    assert json.loads(state.path.read_text())["format"] == STATE_FORMAT
    assert state.load()["outbound"] == {"marker": 123}
    assert state.load()["cursor"] == _batch().cursor


def test_v4_with_pending_attempts_cannot_invent_lost_events(tmp_path: Path) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"attempts": {"lost": 2}})
    old = state.path.read_text().replace(STATE_FORMAT, "funora-state-v4")
    state.path.write_text(old)
    with pytest.raises(CursorIncompatibleError, match="v4"):
        state.load()
    assert state.path.read_text() == old


def test_killed_process_releases_lease_and_keeps_batch(
    no_sleep: list[float], tmp_path: Path
) -> None:
    state = tmp_path / "state.json"
    script = """
import os, sys
from test_watch import _Cycle, _page, _renamed_first_order
import funora._client as driver
from funora import Client, Router, EventType
driver.sleep = lambda _: None
orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
router = Router()
@router.on(EventType.ORDER_CREATED)
def stop(event):
    os._exit(23)
with Client(transport=_Cycle([orders, chats, _renamed_first_order(orders), chats])) as client:
    client.watch(router, state_path=sys.argv[1], max_iterations=2, use_channel=False)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(state)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 23, result.stderr
    saved = PendingBatch.decode(StateFile(state).load()["watch_pending"], account_id="self")
    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)
    with Client(transport=_Cycle([])) as client:
        client.watch(router, state_path=state, max_iterations=1, use_channel=False)
    assert [event.id for event in seen] == [event.id for event in saved.events]
    assert seen[0].delivery.attempt == 2


def test_reentrant_watch_refuses_without_waiting(no_sleep: list[float], tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    router = Router()
    with Client(
        transport=_Cycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")])
    ) as client:

        @router.on()
        def reenter(event: Event) -> None:
            with pytest.raises(ConfigurationError, match="уже работает"):
                client.watch(Router(), state_path=state, max_iterations=1, use_channel=False)

        client.watch(router, state_path=state, max_iterations=1, use_channel=False)
        client.watch(Router(), state_path=state, max_iterations=1, use_channel=False)


@pytest.mark.parametrize("missing", ["watch_pending", "watch_greeted", "cursor"])
def test_missing_checkpoint_field_cannot_discard_batch(tmp_path: Path, missing: str) -> None:
    state = StateFile(tmp_path / "state.json")
    payload = {
        "watch_owner": "self",
        "watch_pending": _batch().encode(),
        "watch_greeted": False,
        "cursor": _batch().cursor,
    }
    del payload[missing]
    state.save(payload)
    with pytest.raises(StateSchemaIncompatibleError):
        state.load()


def test_attempts_without_batch_are_not_reconstructed(tmp_path: Path) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"attempts": {"event": 2}, "watch_pending": None})
    with pytest.raises(StateSchemaIncompatibleError):
        state.load()


def test_duplicate_checkpoint_key_is_not_last_value_wins(tmp_path: Path) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"watch_pending": _batch().encode()})
    raw = state.path.read_text().replace('"payload":{', '"payload":{"watch_pending":null,')
    state.path.write_text(raw)
    with pytest.raises(StateSchemaIncompatibleError):
        state.load()


@pytest.mark.parametrize("cursor", [None, [], {"orders": [False]}, {"chats": {"1": False}}])
def test_bad_base_cursor_cannot_start_silently(tmp_path: Path, cursor: object) -> None:
    state = StateFile(tmp_path / "state.json")
    state.save({"cursor": cursor})
    with Client(transport=_Cycle([])) as client, pytest.raises(CursorIncompatibleError):
        client.watch(Router(), state_path=state.path, max_iterations=1, use_channel=False)


def test_quiet_poll_needs_only_one_checkpoint(
    no_sleep: list[float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    save = StateFile._save

    def record(self: StateFile, payload: dict) -> None:
        calls.append(payload.get("watch_pending") is not None)
        save(self, payload)

    monkeypatch.setattr(StateFile, "_save", record)
    with Client(
        transport=_Cycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")])
    ) as client:
        client.watch(
            Router(), state_path=tmp_path / "state.json", max_iterations=3, use_channel=False
        )
    assert calls == [True, False, False, False]


def test_state_file_alias_keeps_the_same_owner(no_sleep: list[float], tmp_path: Path) -> None:
    target, alias = tmp_path / "state.json", tmp_path / "alias.json"
    StateFile(target).save({})
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("создание символических ссылок недоступно в этом окружении")
    with Client(
        transport=_Cycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")]),
        state_path=alias,
    ) as client:
        client.watch(Router(), max_iterations=1, use_channel=False)
    assert alias.is_symlink(), "запись должна менять целевой файл, а не заменять ссылку"
    assert StateFile(target).load()["watch_greeted"] is True
    with (
        watch_lease(Lock(), target),
        Client(transport=_Cycle([])) as client,
        pytest.raises(ConfigurationError),
    ):
        client.watch(Router(), state_path=alias, max_iterations=1, use_channel=False)
