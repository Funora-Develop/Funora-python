"""Курсоры переживают перезапуск и отказывают до сети при неверной области."""

import base64
import json
from dataclasses import replace

import pytest
from test_aclient import _AsyncFakeFetcher
from test_chat_history import NODE, _answer, _entry, _parse
from test_client import _FakeFetcher, _observation
from test_reviews_pagination import Transport, page

from funora import AsyncClient, Client, ReviewsCursor
from funora._budget import Budget
from funora._cursor import decode_cursor, encode_cursor
from funora._result import Completeness
from funora.contract import MAX_CURSOR_BYTES
from funora.errors import CursorIncompatibleError, IncompleteResultError, ValidationError


def pack(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unpack(token):
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))


def altered(**changes):
    token = encode_cursor("chats.history_before", NODE, "99")
    payload = json.loads(unpack(token))
    payload.update(changes)
    return pack(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


class HistoryTransport(_FakeFetcher):
    def __init__(self, replies):
        super().__init__(replies)
        self.paths = []

    def ask(self, path, headers):
        self.paths.append(path)
        return self.fetch(path)


class AsyncHistoryTransport(_AsyncFakeFetcher):
    def __init__(self, replies):
        super().__init__(replies)
        self.paths = []

    async def ask(self, path, headers):
        self.paths.append(path)
        return await self.fetch(path)


def observation(*entries):
    return _observation(json.dumps(_answer(*entries)))


def test_cursor_has_a_stable_canonical_wire_format():
    raw = (
        b'{"adapter_family":"funpay-web","canonical_form_version":3,'
        b'"kind":"chats.history_before","owner":"NDI","position":"OTk","scope":null,"version":2}'
    )
    assert unpack(encode_cursor("chats.history_before", "42", "99")) == raw
    assert decode_cursor(pack(raw), kind="chats.history_before") == ("42", "99", None)


def test_legacy_v1_history_cursor_is_read_before_io_and_rewritten_as_v2():
    raw = (
        b'{"adapter_family":"funpay-web","canonical_form_version":3,'
        b'"kind":"chats.history_before","owner":"NDI","position":"OTk","version":1}'
    )
    transport = HistoryTransport([observation(_entry(98))])
    with Client(transport=transport, budget=Budget(names=())) as client:
        result = client.chats.history_before("42", cursor=pack(raw))
    assert transport.paths == ["/chat/history?node=42&last_message=99"]
    assert json.loads(unpack(result.next_cursor))["version"] == 2


def test_legacy_v1_reviews_cursor_has_no_rating_scope():
    raw = (
        b'{"adapter_family":"funpay-web","canonical_form_version":3,'
        b'"kind":"reviews.get","owner":"MTIz","position":"b3BhcXVl","version":1}'
    )
    token = pack(raw)
    assert ReviewsCursor.from_token(token) == ReviewsCursor("123", "opaque")
    transport = Transport([_observation(page("last"))])
    with Client(transport=transport, budget=Budget(names=())) as client:
        result = client.reviews.get("123", cursor=token)
    assert result.next_cursor is None
    assert transport.forms[0][1]["filter"] == ""
    with Client(transport=Transport([])) as client, pytest.raises(CursorIncompatibleError):
        client.reviews.get("123", rating=5, cursor=token)


@pytest.mark.parametrize("scope", ["", [], "\ud800", "x" * MAX_CURSOR_BYTES])
def test_invalid_scope_cannot_be_serialized(scope):
    with pytest.raises(CursorIncompatibleError):
        encode_cursor("reviews.get", "123", "position", scope=scope)


def test_history_cursor_cannot_carry_a_review_filter():
    token = encode_cursor("chats.history_before", NODE, "99", scope="5")
    transport = HistoryTransport([])
    with Client(transport=transport) as client, pytest.raises(CursorIncompatibleError):
        client.chats.history_before(NODE, cursor=token)
    assert transport.calls == 0


@pytest.mark.parametrize("position", ["e\u0301", "\u00e9", " a=b/+? ", "ключ 🔑", "\0opaque"])
def test_remote_position_survives_without_unicode_normalization(position):
    original = ReviewsCursor("123", position)
    assert ReviewsCursor.from_token(original.to_token()) == original
    assert "=" not in original.to_token()


def test_cursors_are_bound_to_the_kind_and_owner():
    token = ReviewsCursor("123", "opaque").to_token()
    with pytest.raises(CursorIncompatibleError):
        decode_cursor(token, kind="chats.history_before")
    assert ReviewsCursor.from_token(token).user_id == "123"


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 3},
        {"version": True},
        {"version": 1.0},
        {"canonical_form_version": 2},
        {"canonical_form_version": True},
        {"adapter_family": "elsewhere"},
        {"kind": "reviews.get"},
        {"owner": ""},
        {"owner": []},
        {"position": ""},
        {"position": []},
        {"owner": "_w"},
        {"position": "_w"},
        {"scope": ""},
        {"scope": []},
        {"scope": "_w"},
        {"extra": 1},
    ],
)
def test_foreign_or_malformed_envelopes_are_rejected(changes):
    with pytest.raises(CursorIncompatibleError):
        decode_cursor(altered(**changes), kind="chats.history_before")


@pytest.mark.parametrize(
    "token", [None, False, 42, [], "", " ", "=", "AA==", "а", "null", "a" * (MAX_CURSOR_BYTES + 1)]
)
def test_invalid_transport_values_are_typed_errors(token):
    with pytest.raises(CursorIncompatibleError):
        decode_cursor(token, kind="chats.history_before")


def test_noncanonical_json_and_nested_json_are_rejected():
    token = encode_cursor("chats.history_before", NODE, "99")
    raw = unpack(token)
    for malformed in [
        b"\n" + raw,
        raw.replace(b'"version":2', b'"version":2,"version":2'),
        b"[" * 1200 + b"0" + b"]" * 1200,
        b"[]",
        b"null",
        b"{",
        b"\xff",
    ]:
        with pytest.raises(CursorIncompatibleError):
            decode_cursor(pack(malformed), kind="chats.history_before")
    with pytest.raises(CursorIncompatibleError):
        decode_cursor(token + "=", kind="chats.history_before")


@pytest.mark.parametrize(
    "owner,position",
    [("", "x"), (None, "x"), ("1", ""), ("1", []), ("1", "\ud800"), ("1", "x" * MAX_CURSOR_BYTES)],
)
def test_invalid_or_oversized_positions_cannot_be_exported(owner, position):
    with pytest.raises(CursorIncompatibleError):
        encode_cursor("reviews.get", owner, position)
    with pytest.raises(CursorIncompatibleError):
        encode_cursor("unknown", "1", "x")


def test_history_uses_the_oldest_id_and_resumes_in_a_new_client():
    first_transport = HistoryTransport([observation(_entry(99), _entry(97), _entry(98))])
    with Client(transport=first_transport, budget=Budget(names=())) as client:
        first = client.chats.history_before(NODE, before_message_id="100")
        token = first.next_cursor
    assert token is not None
    assert decode_cursor(token, kind="chats.history_before") == (NODE, "97", None)
    second_transport = HistoryTransport([observation(_entry(96)), observation()])
    with Client(transport=second_transport, budget=Budget(names=())) as client:
        second = client.chats.history_before(NODE, cursor=token)
        end = client.chats.history_before(NODE, cursor=second.next_cursor)
        assert end.exhausted and end.next_cursor is None
    assert second_transport.paths == [
        f"/chat/history?node={NODE}&last_message=97",
        f"/chat/history?node={NODE}&last_message=96",
    ]


async def test_async_history_accepts_the_same_saved_cursor():
    token = _parse(_entry(99)).next_cursor
    transport = AsyncHistoryTransport([observation(_entry(98)), observation()])
    async with AsyncClient(transport=transport, budget=Budget(names=())) as client:
        current = await client.chats.history_before(NODE, cursor=token)
        assert decode_cursor(current.next_cursor, kind="chats.history_before") == (NODE, "98", None)
        assert (await client.chats.history_before(NODE, cursor=current.next_cursor)).exhausted


@pytest.mark.parametrize("state", [Completeness.PARTIAL, Completeness.UNKNOWN])
def test_incomplete_history_does_not_offer_a_cursor_that_skips_rows(state):
    history = replace(_parse(_entry(99)), completeness=state)
    with pytest.raises(IncompleteResultError):
        _ = history.next_cursor
    broken = _parse(_entry(99), {"id": "bad"})
    with pytest.raises(IncompleteResultError):
        _ = broken.next_cursor


@pytest.mark.parametrize(
    "client_type,transport_type", [(Client, HistoryTransport), (AsyncClient, AsyncHistoryTransport)]
)
async def test_invalid_cursor_and_arguments_fail_before_network(client_type, transport_type):
    transport = transport_type([])
    client = client_type(transport=transport, budget=Budget(names=()))
    cases = [
        ({}, ValidationError),
        ({"before_message_id": "100", "cursor": "bad"}, ValidationError),
        ({"cursor": "bad"}, CursorIncompatibleError),
        ({"cursor": encode_cursor("chats.history_before", "other", "99")}, CursorIncompatibleError),
        ({"cursor": ReviewsCursor(NODE, "99").to_token()}, CursorIncompatibleError),
        ({"cursor": encode_cursor("chats.history_before", NODE, "²")}, CursorIncompatibleError),
        ({"before_message_id": "²"}, ValidationError),
        ({"before_message_id": 99}, ValidationError),
        ({"before_message_id": "9" * 129}, ValidationError),
    ]
    try:
        for kwargs, expected in cases:
            with pytest.raises(expected):
                result = client.chats.history_before(NODE, **kwargs)
                if isinstance(client, AsyncClient):
                    await result
        assert transport.calls == 0
    finally:
        if isinstance(client, AsyncClient):
            await client.close()
        else:
            client.close()


def test_reviews_use_the_saved_raw_position_and_enforce_owner():
    raw_position = "e\u0301=a/+ "
    token = ReviewsCursor("123", raw_position).to_token()
    transport = Transport([_observation(page("last"))])
    with Client(transport=transport, budget=Budget(names=())) as client:
        result = client.reviews.get("123", cursor=token)
        assert result.next_cursor is None
        assert transport.forms[0][1]["continue"] == raw_position
        with pytest.raises(CursorIncompatibleError):
            client.reviews.get("456", cursor=token)
        assert transport.calls == 1


async def test_async_reviews_use_the_saved_cursor():
    class AsyncTransport(_AsyncFakeFetcher):
        async def submit(self, path, fields, headers):
            assert fields["continue"] == "previous"
            return await self.fetch(path)

    transport = AsyncTransport([_observation(page("last"))])
    async with AsyncClient(transport=transport, budget=Budget(names=())) as client:
        result = await client.reviews.get("123", cursor=ReviewsCursor("123", "previous").to_token())
        assert result.next_cursor is None


@pytest.mark.parametrize(
    "key,value",
    [
        (key, "unsupported")
        for key in [
            "format_version",
            "accepted_format_versions",
            "max_token_bytes",
            "alphabet",
            "envelope",
            "owner_and_position",
            "scope",
            "kinds",
        ]
    ]
    + [
        ("format_version", True),
        ("format_version", 2.0),
        ("accepted_format_versions", [True, 2]),
        ("accepted_format_versions", [1, 2.0]),
        ("max_token_bytes", 16384.0),
    ],
)
def test_codegen_rejects_an_unsupported_cursor_format(tmp_path, key, value):
    import os
    import shutil
    import sys
    from pathlib import Path

    import yaml

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import codegen

    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(spec) / "spec", tmp_path / "spec")
    path = tmp_path / "spec/types.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["types"]["cursor"]["encoding"][key] = value
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit, match="формат курсора"):
        codegen.render_contract(tmp_path)


@pytest.mark.parametrize(
    "identifier",
    ["²", "٢", "9" * 129, 10**5000, ""],
    ids=["superscript", "arabic", "too-long", "huge-int", "empty"],
)
def test_invalid_response_identifier_is_a_defect_not_an_untyped_crash(identifier):
    history = _parse(_entry(99), {"id": identifier})
    assert history.completeness is Completeness.PARTIAL
    assert history.rows_rejected == 1
    assert history.defects[0].code == "identifier_unreadable"
    with pytest.raises(IncompleteResultError):
        _ = history.next_cursor


def test_history_schema_covers_data_and_computed_cursor():
    import os
    from dataclasses import fields
    from pathlib import Path

    from funora._chat_history import ChatHistory

    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    schema = json.loads((Path(spec) / "spec/models/chat-history.schema.json").read_text())
    assert set(schema["properties"]) == {
        f.name for f in fields(ChatHistory) if not f.name.startswith("_")
    } | {"messages", "next_cursor"}
    assert schema["properties"]["next_cursor"]["anyOf"] == [
        {"x-funora-type": "cursor"},
        {"type": "null", "x-funora-nullable": "not_applicable"},
    ]
