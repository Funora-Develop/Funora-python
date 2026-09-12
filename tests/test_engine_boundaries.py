"""Ядро отказывает на ошибках драйвера до разбора и повторения действий."""

from dataclasses import replace

import pytest
from test_currency_switch import BALANCE_HTML
from test_refund import _order_page
from test_reviews_write import ORDER_HTML
from test_send_image import NODE, PNG, THREAD_HTML, _observation
from test_update_price import NODE as LOT_NODE
from test_update_price import OFFER, WHEN
from test_update_price import _page as lot_page

from funora import Budget, Router, _engine
from funora._engine import Engine, Fetch, Pause
from funora._listen import ChannelState
from funora._lot_form import parse_lot_form
from funora._secret import Secret
from funora._transport import TransportSettings
from funora.capabilities import Capability
from funora.errors import ConfigurationError, ProtocolChangedError


def engine(**kwargs):
    return Engine(
        TransportSettings(),
        Budget(names=()),
        experimental=frozenset(Capability),
        unsafe_sends_without_ledger=True,
        unsafe_price_changes_without_audit=True,
        **kwargs,
    )


def operation(core, name):
    if name in {"visible", "price"}:
        form = parse_lot_form(lot_page(), observed_at=WHEN)
        if name == "visible":
            return core.set_lot_visible(
                LOT_NODE, OFFER, visible=False, expected_revision=form.revision
            )
        return core.update_price(LOT_NODE, OFFER, "100", expected_revision=form.revision)
    return {
        "image": lambda: core.send_image(NODE, PNG, filename="a.png", declared_cold=True),
        "viewing": lambda: core.read_buyer_viewing(NODE, ("123",)),
        "mark-read": lambda: core.mark_chat_read(NODE),
        "text": lambda: core.send_text(NODE, "hello", declared_cold=True),
        "promote": lambda: core.promote_lots("1", "123"),
        "currency": lambda: core.switch_currency("RUB"),
        "calculate": lambda: core.calculate_prices(node_id="123", price="100"),
        "review": lambda: core.leave_review("ABC", rating=5, text="test"),
        "refund": lambda: core.refund_order("ABC"),
        "details": lambda: core.read_order_details(("ABC",)),
        "history": lambda: core.read_history_before(NODE, before_message_id="10"),
        "listen": lambda: core.listen_once(
            ChannelState(), own_user_id="123", token=Secret("synthetic")
        ),
        "fetch": lambda: core.read_orders(),
    }[name]()


PAGES = {
    "image": THREAD_HTML,
    "viewing": THREAD_HTML,
    "mark-read": THREAD_HTML,
    "text": THREAD_HTML,
    "currency": BALANCE_HTML,
    "review": ORDER_HTML,
    "refund": _order_page(refundable=True),
}


def advance_to_action(generator, name, *, no_token=False):
    command = next(generator)
    requests = []
    while isinstance(command, (Fetch, Pause)) and name != "fetch":
        requests.append(command)
        if isinstance(command, Pause):
            pytest.fail("empty budget must not delay this isolated operation")
        html = lot_page() if name in {"price", "visible"} else PAGES[name]
        if no_token:
            html = html.replace('"csrf-token"', '"missing-token"')
        command = generator.send(_observation(html, "https://funpay.com" + command.path))
    return command, requests


@pytest.mark.parametrize(
    "name",
    [
        "image",
        "viewing",
        "mark-read",
        "text",
        "visible",
        "price",
        "promote",
        "currency",
        "calculate",
        "review",
        "refund",
        "details",
        "history",
        "listen",
        "fetch",
    ],
)
def test_wrong_driver_reply_is_not_a_platform_failure_or_retry(name):
    core = engine()
    generator = operation(core, name)
    try:
        command, _ = advance_to_action(generator, name)
        assert not isinstance(command, Pause)
        with pytest.raises(TypeError, match="ожидалось наблюдение"):
            generator.send(object())
        assert generator.gi_frame is None
    finally:
        generator.close()


@pytest.mark.parametrize("name", ["viewing", "currency", "review", "refund"])
def test_missing_token_refuses_before_action(name):
    generator = operation(engine(), name)
    try:
        with pytest.raises(ProtocolChangedError, match="токена"):
            advance_to_action(generator, name, no_token=True)
        assert generator.gi_frame is None
    finally:
        generator.close()


def test_unknown_operation_defaults_to_unsafe_and_interactive(monkeypatch):
    from funora.budget import RequestClass
    from funora.operations import Safety

    monkeypatch.setattr(_engine, "OPERATIONS", {})
    assert _engine._class_of(Capability.ORDERS_LIST) is RequestClass.INTERACTIVE
    assert _engine._safety_of(Capability.ORDERS_LIST) is Safety.UNSAFE


@pytest.mark.parametrize("account", ["", " ", None, 123])
def test_watch_invalid_owner_is_refused_before_io(account):
    core = engine().watch(Router(), account_id=account, max_iterations=0)
    with pytest.raises(ConfigurationError, match="account_id"):
        next(core)


def test_watch_cannot_switch_the_existing_state_file(tmp_path):
    core = engine(state_path=tmp_path / "original.json")
    watch = core.watch(
        Router(), account_id="self", state_path=tmp_path / "other.json", max_iterations=0
    )
    with pytest.raises(ConfigurationError, match="один файл"):
        next(watch)
    assert not (tmp_path / "other.json").exists()


def test_rate_limit_on_send_updates_identity_without_retry():
    from funora._runner import SendOutcome, parse_runner_context

    core = engine()
    generator = core._submit_message(parse_runner_context(THREAD_HTML), {"content": "test"})
    assert isinstance(next(generator), _engine.Submit)
    limited = replace(
        _observation("{}", "https://funpay.com/runner/"), status=429, retry_after_ms=1234
    )
    with pytest.raises(StopIteration) as done:
        generator.send(limited)
    assert done.value.value.outcome is SendOutcome.UNCONFIRMED
    assert core._identity.cooldown_until > _engine.monotonic()


def test_channel_rate_limit_waits_on_virtual_clock_and_keeps_tags(monkeypatch):
    from funora._listen import ChannelSignal

    clock = [1000.0]
    monkeypatch.setattr(_engine, "monotonic", lambda: clock[0])
    core = engine()
    channel = ChannelState()
    generator = core.listen_once(channel, own_user_id="123", token=Secret("synthetic"))
    assert isinstance(next(generator), _engine.Submit)
    response = replace(
        _observation("{}", "https://funpay.com/runner/"), status=429, retry_after_ms=1234
    )
    command = generator.send(response)
    waits = []
    while True:
        assert isinstance(command, Pause)
        waits.append(command.ms)
        clock[0] += command.ms / 1000
        try:
            command = generator.send(None)
        except StopIteration as done:
            assert done.value == (ChannelSignal.DEGRADED, "http_429")
            break
    assert sum(waits) >= 1234
    assert channel.tags == {}


def test_channel_does_not_accept_an_answer_to_an_unrequested_action():
    from test_listen import _answer

    from funora._listen import ChannelSignal

    channel = ChannelState()
    core = engine().listen_once(channel, own_user_id="123", token=Secret("synthetic"))
    next(core)
    with pytest.raises(StopIteration) as done:
        core.send(_observation(_answer([], response={"error": None}), "https://funpay.com/runner/"))
    assert done.value.value[0] is ChannelSignal.DEGRADED
    assert channel.tags == {}


def test_reconciliation_stops_after_the_first_conclusive_read():
    from test_client import _page

    from funora._runner import take_anchor
    from funora.reconciliation import ReconcileVerdict

    html = _page("chat-thread.logged.ru")
    generator = engine()._reconcile_send(NODE, take_anchor(html))
    assert isinstance(next(generator), Pause)
    assert isinstance(generator.send(None), Fetch)
    with pytest.raises(StopIteration) as done:
        generator.send(_observation(html, f"https://funpay.com/chat/?node={NODE}"))
    assert done.value.value is ReconcileVerdict.ABSENT_FROM_HISTORY


def test_locale_change_invalidates_catalog_and_stop_is_sticky():
    from funora.errors import AccessBlockedError

    core = engine()
    core.note_locale('<html lang="ru"></html>')
    sentinel = object()
    core._state.catalog_cached = sentinel
    core.note_locale('<html lang="en"></html>')
    assert core._state.catalog_cached is None
    first, second = AccessBlockedError("first"), AccessBlockedError("second")
    core.note_stop(first)
    core.note_stop(second)
    assert core.stopped is first


@pytest.mark.parametrize("mode", ["personal", "market"])
@pytest.mark.parametrize("problem", ["owner", "greeted"])
def test_restored_watch_metadata_refuses_before_io(tmp_path, mode, problem):
    from test_monitoring import WATCH

    from funora._monitoring import initial_cursor
    from funora._state import StateFile
    from funora.errors import CursorIncompatibleError

    cursor = (
        initial_cursor((WATCH,))
        if mode == "market"
        else {"orders": {}, "chats": {}, "threads": {}, "pending_threads": []}
    )
    payload = {
        "watch_owner": "self",
        "watch_greeted": True,
        "watch_pending": None,
        "cursor": cursor,
    }
    payload["watch_owner" if problem == "owner" else "watch_greeted"] = "invalid"
    state = StateFile(tmp_path / "state.json")
    state.save(payload)
    before = state.path.read_bytes()
    core = engine()
    generator = (
        core.watch(Router(), account_id="self", state_path=state.path, max_iterations=0)
        if mode == "personal"
        else core.monitor_market(
            (WATCH,), account_id="self", state_path=state.path, max_iterations=0
        )
    )
    with pytest.raises(CursorIncompatibleError):
        next(generator)
    assert state.path.read_bytes() == before


def test_legacy_order_cursor_is_restored_and_tokenless_watch_uses_pages(tmp_path):
    from test_client import _FakeFetcher, _page

    from funora import Client
    from funora._state import StateFile

    state = StateFile(tmp_path / "state.json")
    state.save({"cursor": {"orders": ["known-order"]}})
    chats = _page("chat.logged.ru").replace("data-app-data=", "data-old-app-data=")
    transport = _FakeFetcher(
        [
            _observation(_page("orders-trade.logged.ru"), "https://funpay.com/orders/trade"),
            _observation(chats, "https://funpay.com/chat/"),
        ]
    )
    with Client(transport=transport, budget=Budget(names=())) as client:
        client.watch(Router(), state_path=state.path, max_iterations=1)
    assert state.load()["watch_pending"] is None
    assert state.load()["watch_greeted"] is True


def test_wrong_delivery_result_preserves_pending_batch_and_cursor(tmp_path):
    from test_monitoring import WATCH, history, snapshot

    from funora._monitoring import initial_cursor
    from funora._poll import Deduplicator
    from funora._state import StateFile
    from funora._watch_state import PendingBatch

    target, events = history(snapshot())
    previous = initial_cursor((WATCH,))
    state = StateFile(tmp_path / "state.json")
    generator = engine()._deliver_pending(
        PendingBatch(tuple(events), target, True).encode(),
        account_id="self",
        state=state,
        base_cursor=previous,
        greeted=False,
        dedup=Deduplicator(),
        persist_outbound=False,
    )
    command = next(generator)
    assert isinstance(command, _engine.Deliver)
    before = state.path.read_bytes()
    with pytest.raises(TypeError, match="итог раздачи"):
        generator.send(None)
    assert state.path.read_bytes() == before
    restored = state.load()
    assert restored["cursor"] == previous
    pending = PendingBatch.decode(restored["watch_pending"], account_id="self")
    assert pending.events[0].id == events[0].id


@pytest.mark.parametrize("name", ["image", "mark-read"])
def test_missing_token_is_checked_even_if_context_claims_sendable(monkeypatch, name):
    from funora._runner import parse_runner_context

    context = replace(parse_runner_context(THREAD_HTML), csrf_token=None)
    assert context.can_send
    monkeypatch.setattr(_engine, "parse_runner_context", lambda _: context)
    generator = operation(engine(), name)
    with pytest.raises(ProtocolChangedError, match="токена"):
        advance_to_action(generator, name)
    assert generator.gi_frame is None
