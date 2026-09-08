"""Повреждённый канал не подтверждает тишину, метки или отправку."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

import pytest
from test_listen import _Channel, _observation
from test_listen import no_sleep as no_sleep
from test_send_outcome import NODE
from test_send_outcome import _answer as send_answer
from test_watch import _renamed_first_order

from funora import AsyncClient, Client, Event, EventType, Router
from funora._budget import Budget
from funora._engine import Engine, Submit
from funora._listen import ChannelSignal, ChannelState, classify_signal
from funora._runner import classify_send_response
from funora._secret import Secret
from funora._transport import TransportSettings
from funora._updates import parse_updates_answer
from funora.errors import ProtocolChangedError
from funora.send_outcome import SendOutcome


def poll(objects: Iterable[object] = (), response: object = False) -> str:
    return json.dumps({"objects": list(objects), "response": response})


VALID_OBJECT = {"type": "orders_counters", "id": 77, "tag": "new", "data": {"seller": 1}}

INVALID_JSON = [
    '{"objects": [null], "objects": [], "response": false}',
    '{"objects": [], "response": {"error": "refused", "error": null}}',
    '{"objects": [], "response": false, "extra": {"same": 1, "same": 2}}',
    *[
        f'{{"objects": [], "response": false, "extra": {value}}}'
        for value in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999", "9" * 5000)
    ],
    '{"objects": [], "response": false, "extra": ' + "[" * 10000 + "0" + "]" * 10000 + "}",
]

INVALID_POLL = [
    '{"objects": []}',
    *[poll(response=value) for value in (None, 0, 1, "", "false", [], {})],
    *[poll([value]) for value in (None, False, 1, "object", [], {})],
    *[
        poll([{**VALID_OBJECT, field: value}])
        for field, value in (
            ("type", ""),
            ("type", 1),
            ("id", None),
            ("id", True),
            ("id", 1.5),
            ("id", ""),
            ("id", []),
            ("tag", None),
            ("tag", 2),
            ("tag", ""),
            ("data", None),
            ("data", False),
            ("data", []),
        )
    ],
    *[
        poll([{key: value for key, value in VALID_OBJECT.items() if key != missing}])
        for missing in VALID_OBJECT
    ],
    poll([VALID_OBJECT, {**VALID_OBJECT, "id": "77", "tag": "another"}]),
    poll([VALID_OBJECT, None]),
    *[poll([{**VALID_OBJECT, field: "\ud800"}]) for field in ("type", "id", "tag")],
]


@pytest.mark.parametrize("body", INVALID_JSON + INVALID_POLL, ids=lambda body: body[:80])
def test_unusable_poll_is_a_protocol_error(body: str) -> None:
    with pytest.raises(ProtocolChangedError):
        parse_updates_answer(body)


@pytest.mark.parametrize("error", ["", 0, False, [], {}, "отказ", {"code": 42}])
def test_only_explicit_null_means_no_reported_error(error: object) -> None:
    answer = parse_updates_answer(poll(response={"error": error}))
    assert answer.error == error
    assert classify_signal(answer) == (ChannelSignal.DEGRADED, "channel_reported_error")


@pytest.mark.parametrize("response", [False, True, {"error": None}])
def test_observed_envelopes_keep_their_meaning(response: object) -> None:
    answer = parse_updates_answer(poll([VALID_OBJECT], response))
    assert answer.error is None
    assert answer.tags() == {("orders_counters", "77"): "new"}
    assert answer.objects[0].number("seller") == 1


def test_unknown_well_formed_object_is_preserved() -> None:
    answer = parse_updates_answer(poll([{**VALID_OBJECT, "type": "future_kind"}]))
    assert answer.objects[0].type == "future_kind"
    assert classify_signal(answer) == (ChannelSignal.CHANGED, "")


@pytest.mark.parametrize("body", INVALID_JSON, ids=lambda body: body[:80])
def test_ambiguous_json_cannot_confirm_or_refuse_a_send(body: str) -> None:
    # Сохраняем повреждение, но добавляем положительное свидетельство отправки.
    objects = json.loads(send_answer())["objects"]
    body = body.replace('"objects": []', '"objects": ' + json.dumps(objects))
    body = body.replace('"response": false', '"response": {"error": null}')
    result = classify_send_response(body, sent_to=NODE)
    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == "body_not_json"


def test_missing_error_does_not_confirm_a_send() -> None:
    result = classify_send_response(send_answer(response={}), sent_to=NODE)
    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == "response_error_missing"


@pytest.mark.parametrize("body", INVALID_JSON + INVALID_POLL, ids=lambda body: body[:80])
def test_bad_answer_never_commits_even_the_valid_tags(body: str) -> None:
    engine = Engine(TransportSettings(), budget=Budget(names=()))
    channel = ChannelState(tags={("orders_counters", "77"): "before"})
    before = dict(channel.tags)
    operation = engine.listen_once(channel, own_user_id="77", token=Secret("test"))
    assert isinstance(next(operation), Submit)
    with pytest.raises(StopIteration) as stopped:
        operation.send(_observation(body))
    assert stopped.value.value[0] is ChannelSignal.DEGRADED
    assert channel.tags == before


@pytest.mark.parametrize(
    "body",
    [INVALID_POLL[0], poll([None]), INVALID_JSON[-1], INVALID_JSON[1]],
    ids=["missing-response", "invalid-object", "depth", "duplicate-error"],
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_watch_falls_back_and_recovers(
    body: str, asynchronous: bool, no_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    class ChangingPages(_Channel):
        def fetch(self, path: str):
            observation = super().fetch(path)
            if path.startswith("/orders") and self.submitted:
                html = _renamed_first_order(observation.html)
                size = len(html.encode("utf-8"))
                return replace(observation, html=html, content_length=size, declared_length=size)
            return observation

    tape = ChangingPages([body, poll()])
    seen: list[Event] = []
    router = Router()
    router.on(EventType.ORDER_CREATED)(seen.append)
    if not asynchronous:
        with Client(transport=tape, budget=Budget(names=())) as client:
            client.watch(router, max_iterations=3)
    else:
        import funora._aclient as async_module

        async def no_wait(seconds: float) -> None:
            import funora._client as sync_module

            sync_module.sleep(seconds)

        class AsyncTape:
            async def fetch(self, path: str):
                return tape.fetch(path)

            async def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]):
                return tape.submit(path, fields, headers)

            async def close(self) -> None:
                pass

        monkeypatch.setattr(async_module.asyncio, "sleep", no_wait)

        async def run() -> None:
            async with AsyncClient(transport=AsyncTape(), budget=Budget(names=())) as client:
                await client.watch(router, max_iterations=3)

        asyncio.run(run())
    assert tape.pages_read() == 4  # Начальный снимок и немедленный откат после повреждения.
    assert len(tape.submitted) == 2  # Следующий корректный тихий ответ снова экономит чтение.
    assert len(seen) == 1
    assert seen[0].payload["order_id"] == "777"


@pytest.mark.parametrize(
    "response,reason",
    [
        ("missing", "response_error_missing"),
        ("duplicate", "body_not_json"),
        ("depth", "body_not_json"),
    ],
)
def test_ambiguous_send_reads_history_without_resubmitting(response: str, reason: str) -> None:
    from test_send_text_operation import NODE_ID, _confirmation, _engine, _page, _run

    engine = _engine()
    body = _confirmation(_page())
    if response == "missing":
        body = body.replace('"error": null', '"another": null')
    elif response == "duplicate":
        body = body.replace('"error": null', '"error": "refused", "error": null')
    else:
        body = body[:-1] + ',"extra":' + "[" * 10000 + "0" + "]" * 10000 + "}"
    result, requests = _run(engine, engine.send_text(NODE_ID, "тест"), answer=body)
    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == reason
    assert sum(isinstance(request, Submit) for request in requests) == 1
