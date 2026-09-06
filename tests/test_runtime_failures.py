# ruff: noqa: F811
"""Сбои сети и очереди не теряют соседние задания и не вызывают повтор отправки."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_auto_delivery import _lot, _order
from test_bot import NODE_ID, _Tape, no_clock  # noqa: F401
from test_listen import _answer, _Channel, no_sleep  # noqa: F401

from funora import Client, Router
from funora._delivered import DeliveryLedger
from funora._result import Completeness
from funora._state import StateFile
from funora.bot import Bot, DeliveryPlan
from funora.bot._delivery import AutoDelivery
from funora.errors import ConfigurationError, NetworkError, UsageError
from funora.send_outcome import SendOutcome


def test_delivery_queue_refusal_is_recorded_and_reported(tmp_path: Path) -> None:
    ledger = DeliveryLedger()
    held = []

    def full(*args: object) -> object:
        raise UsageError("queue full")

    delivery = AutoDelivery(
        DeliveryPlan({"L2": "goods"}, lambda _: "1"), ledger, full, on_hold=held.append
    )
    with pytest.raises(UsageError):
        delivery.handle(
            _order(),
            (_lot("L2", "Аккаунт Steam с играми"),),
            page_completeness=Completeness.COMPLETE,
        )
    assert ledger.get("A1").outcome == "queue_failed"
    assert held[-1].reason == "queue_failed"


def test_draining_failure_leaves_other_commands_queued(no_clock: list[float]) -> None:
    class Broken(_Tape):
        def fetch(self, path: str):
            raise RuntimeError("unexpected failure")

    with Client(transport=Broken(), unsafe_sends_without_ledger=True) as client:
        bot = Bot(client, Router())
        bot.send(NODE_ID, "first", idempotency_key="1")
        bot.send(NODE_ID, "second", idempotency_key="2")
        with pytest.raises(RuntimeError):
            bot._drain(1)
        assert bot.outbox.pending == 1


def test_watch_defaults_to_the_client_state_file(tmp_path: Path, no_clock: list[float]) -> None:
    path = tmp_path / "state.json"
    with Client(transport=_Tape(), state_path=path) as client:
        client.watch(Router(), max_iterations=1, use_channel=False)
    assert StateFile(path).load()["cursor"]["orders"]


def test_channel_network_failure_falls_back_to_pages(no_sleep: list[float]) -> None:
    class Broken(_Channel):
        def submit(self, *args: object):
            raise NetworkError("lost channel")

    tape = Broken([_answer([])])
    with Client(transport=tape) as client:
        client.watch(Router(), max_iterations=3)
    assert tape.pages_read() == 6


def test_channel_rejects_quiet_json_with_a_failed_http_status(no_sleep: list[float]) -> None:
    class Broken(_Channel):
        def submit(self, *args: object):
            return replace(super().submit(*args), status=500)

    tape = Broken([_answer([])])
    with Client(transport=tape) as client:
        client.watch(Router(), max_iterations=3)
    assert tape.pages_read() == 6


def test_a_lost_send_response_is_unconfirmed_and_never_resent(no_clock: list[float]) -> None:
    class Lost(_Tape):
        def submit(self, *args: object):
            self.submitted.append({})
            raise NetworkError("response lost")

    tape = Lost()
    with Client(transport=tape, unsafe_sends_without_ledger=True) as client:
        result = client.chats.send_text(NODE_ID, "goods", declared_cold=True)
    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == "transport_error"
    assert len(tape.submitted) == 1


def test_image_requires_the_same_durable_ledger_as_text() -> None:
    from test_send_image import NODE, PNG, _Scripted

    from funora._budget import Budget
    from funora._engine import Engine
    from funora._transport import TransportSettings

    tape = _Scripted()
    with pytest.raises(ConfigurationError):
        tape.run(Engine(TransportSettings(), Budget()).send_image(NODE, PNG, filename="a.png"))
    assert not tape.uploads and not tape.submits


def test_image_and_text_share_the_per_chat_quota() -> None:
    from test_send_image import NODE, PNG, _engine, _Scripted

    from funora.errors import BudgetExhaustedError

    tape = _Scripted()
    engine = _engine()
    tape.run(engine.send_image(NODE, PNG, filename="a.png"))
    with pytest.raises(BudgetExhaustedError):
        tape.run(engine.send_image(NODE, PNG, filename="b.png"))
    assert len(tape.uploads) == len(tape.submits) == 1


def test_image_transport_failure_returns_uncertainty() -> None:
    from test_send_image import NODE, PNG, THREAD_HTML, _engine, _observation

    from funora._engine import Fetch, Submit, Upload

    core = _engine().send_image(NODE, PNG, filename="a.png")
    request = next(core)
    assert isinstance(request, Fetch)
    request = core.send(_observation(THREAD_HTML, "https://funpay.com/chat/"))
    assert isinstance(request, Upload)
    request = core.send(_observation('{"fileId": 1}', "https://funpay.com/file/addChatImage"))
    assert isinstance(request, Submit)
    with pytest.raises(StopIteration) as done:
        core.throw(NetworkError("lost response"))
    assert done.value.value.outcome is SendOutcome.UNCONFIRMED


def test_bot_releases_direct_send_ownership_after_stopping(no_clock: list[float]) -> None:
    with Client(transport=_Tape()) as client:
        bot = Bot(client, Router())
        bot.run(max_iterations=0)
        with pytest.raises(UsageError):
            bot.send_now(NODE_ID, "outside run")


def test_second_run_cannot_steal_or_release_the_active_owner() -> None:
    with Client(transport=_Tape()) as client:
        bot = Bot(client, Router())
        bot.outbox.claim()
        try:
            with pytest.raises(UsageError):
                bot.run(max_iterations=0)
            assert bot.outbox.is_owner()
        finally:
            bot.outbox.release()
