"""Предусловие правки различает значения и устройство всей формы лота."""

import hashlib
import inspect
import json
import os
from html import escape
from pathlib import Path

import pytest
from test_update_price import NODE, OFFER, WHEN, _observation, _page

from funora import AsyncClient, Budget, Capability, Client
from funora._lot_form import parse_lot_form
from funora.errors import PreconditionFailedError, ProtocolChangedError


def page_with(extra, *, active=True, price=None):
    page = _page(active=active, price=price)
    at = page.index("</form>", page.index("form-offer-editor"))
    return page[:at] + extra + page[at:]


def text_fields(first, second):
    return (
        f'<textarea name="zz_a">{escape(first)}</textarea>'
        f'<textarea name="zz_b">{escape(second)}</textarea>'
    )


COLLISIONS = [
    (
        text_fields("first\nzz_b=second", "third"),
        text_fields("first", "second\nzz_b=third"),
    ),
    ('<input name="zz_a=first" value="">', '<input name="zz_a" value="first=">'),
    (
        '<input name="zz_flags" type="hidden" value="0">'
        '<input name="zz_flags" type="checkbox" value="1">',
        '<input name="zz_flags" type="hidden" value="2">'
        '<input name="zz_flags" type="checkbox" value="1">',
    ),
    (
        '<input name="zz_flags" value="1">',
        '<input name="zz_flags" type="checkbox" value="1">',
    ),
]


@pytest.mark.parametrize("before,after", COLLISIONS)
def test_different_form_values_cannot_share_revision(before, after):
    first = parse_lot_form(page_with(before), observed_at=WHEN)
    second = parse_lot_form(page_with(after), observed_at=WHEN)
    assert first.to_request() != second.to_request()
    assert first.revision != second.revision


class Transport:
    def __init__(self, page, after=None):
        self.page = page
        self.after = after
        self.reads = []
        self.writes = []

    def fetch(self, path):
        self.reads.append(path)
        return _observation(self.page, url="https://funpay.com/lots/offerEdit")

    def submit(self, path, fields, *args, **kwargs):
        self.writes.append((path, fields))
        if self.after is None:
            pytest.fail("неверная форма не должна отправляться")
        self.page = self.after
        return _observation("<html></html>", url=f"https://funpay.com/lots/{NODE}/trade")

    def close(self):
        pass


class AsyncTransport(Transport):
    async def fetch(self, path):
        return super().fetch(path)

    async def submit(self, *args, **kwargs):
        return super().submit(*args, **kwargs)

    async def close(self):
        pass


async def complete(value):
    return await value if inspect.isawaitable(value) else value


def client_with(page, asynchronous, path, *, after=None):
    transport = (AsyncTransport if asynchronous else Transport)(page, after)
    client = (AsyncClient if asynchronous else Client)(
        transport=transport,
        budget=Budget(names=()),
        state_path=path,
        experimental=frozenset({Capability.LOTS_ACTIVATE, Capability.LOTS_DEACTIVATE}),
    )
    return client, transport


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["update_price", "activate", "deactivate"])
@pytest.mark.parametrize("before,after", COLLISIONS)
async def test_changed_form_refuses_all_mutations_before_submit(
    asynchronous, operation, before, after, tmp_path
):
    first = parse_lot_form(page_with(before), observed_at=WHEN)
    path = tmp_path / "state.json"
    client, transport = client_with(page_with(after), asynchronous, path)
    try:
        args = (NODE, OFFER, "99") if operation == "update_price" else (NODE, OFFER)
        with pytest.raises(PreconditionFailedError):
            await complete(getattr(client.lots, operation)(*args, expected_revision=first.revision))
        assert len(transport.reads) == 1
        assert not transport.writes
        assert not path.exists()
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("field", ["offer_id", "node_id"])
@pytest.mark.parametrize("value", ["999", ""])
@pytest.mark.parametrize("operation", ["form", "update_price", "activate", "deactivate"])
async def test_form_of_another_lot_is_rejected(asynchronous, field, value, operation, tmp_path):
    # Подмена значения уже наблюдённого скрытого идентификатора.
    page = _page()
    current = parse_lot_form(page, observed_at=WHEN).fields[field]
    page = page.replace(
        f'name="{field}" type="hidden" value="{current}"',
        f'name="{field}" type="hidden" value="{value}"',
    )
    client, transport = client_with(page, asynchronous, tmp_path / "state.json")
    try:
        args = (NODE, OFFER, "99") if operation == "update_price" else (NODE, OFFER)
        kwargs = (
            {}
            if operation == "form"
            else {"expected_revision": parse_lot_form(page, observed_at=WHEN).revision}
        )
        with pytest.raises(ProtocolChangedError):
            await complete(getattr(client.lots, operation)(*args, **kwargs))
        assert len(transport.reads) == 1 and not transport.writes
    finally:
        await complete(client.close())


def legacy_revision(form):
    fields = {**form.fields, **form._checkbox_values}
    parts = [
        f"{name}={value}"
        for name, value in sorted(fields.items())
        if name not in {"csrf_token", "form_created_at"}
    ]
    parts.extend(f"{name}:checked" for name in sorted(form.checked))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["update_price", "activate", "deactivate"])
async def test_legacy_revision_requires_fresh_read(asynchronous, operation, tmp_path):
    page = _page()
    form = parse_lot_form(page, observed_at=WHEN)
    client, transport = client_with(page, asynchronous, tmp_path / "state.json")
    try:
        args = (NODE, OFFER, "99") if operation == "update_price" else (NODE, OFFER)
        with pytest.raises(PreconditionFailedError):
            await complete(
                getattr(client.lots, operation)(*args, expected_revision=legacy_revision(form))
            )
        assert len(transport.reads) == 1 and not transport.writes
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["update_price", "activate", "deactivate"])
async def test_fresh_revision_allows_one_write_preserving_other_fields(
    asynchronous, operation, tmp_path
):
    extra = text_fields("описание\nzz_b=часть описания", "ответ = покупателю")
    before = page_with(extra, price="1", active=operation != "activate")
    after = page_with(
        extra, price="99" if operation == "update_price" else "1", active=operation != "deactivate"
    )
    client, transport = client_with(before, asynchronous, tmp_path / "state.json", after=after)
    try:
        form = await complete(client.lots.form(NODE, OFFER))
        args = (NODE, OFFER, "99") if operation == "update_price" else (NODE, OFFER)
        result = await complete(
            getattr(client.lots, operation)(*args, expected_revision=form.revision)
        )
        expected = (
            form.to_request(price="99")
            if operation == "update_price"
            else form.to_request(active=operation == "activate")
        )
        assert transport.writes == [("/lots/offerSave", expected)]
        assert len(transport.reads) == 3
        assert result.revision == parse_lot_form(after, observed_at=WHEN).revision
        assert result.revision != form.revision
    finally:
        await complete(client.close())


def test_field_order_and_observation_time_do_not_change_revision():
    first = '<input name="zz_a" value="a"><input name="zz_b" value="b">'
    reordered = '<input name="zz_b" value="b"><input name="zz_a" value="a">'
    a = parse_lot_form(page_with(first), observed_at=WHEN)
    b = parse_lot_form(page_with(reordered), observed_at=WHEN.replace(year=2027))
    assert a.to_request() == b.to_request()
    assert a.revision == b.revision


def test_unicode_is_not_normalized_in_form_revision():
    first = parse_lot_form(page_with(text_fields("é", "😀")), observed_at=WHEN)
    second = parse_lot_form(page_with(text_fields("e\u0301", "😀")), observed_at=WHEN)
    assert first.revision != second.revision


def test_hidden_value_is_kept_in_revision_while_checkbox_is_checked():
    def form(value):
        return parse_lot_form(
            page_with(
                f'<input name="zz_flags" type="hidden" value="{value}">'
                '<input name="zz_flags" type="checkbox" value="1" checked>'
            ),
            observed_at=WHEN,
        )

    first, second = form("0"), form("2")
    assert first.to_request() == second.to_request()
    assert first.revision != second.revision


def test_revision_matches_declared_json_frame():
    from funora._lot_form import _revision_of

    fields = {"z": "a\nb=c", "a": "é😀", "csrf_token": "ignored", "form_created_at": "1"}
    material = '["lot-form-v2",[["a","\\u00e9\\ud83d\\ude00"],["z","a\\nb=c"]],[["a","on"]],["a"]]'
    assert json.loads(material)[1] == [["a", "é😀"], ["z", "a\nb=c"]]
    assert (
        _revision_of(fields, frozenset({"a"}), {"a": "on"})
        == hashlib.sha256(material.encode("ascii")).hexdigest()[:16]
    )


def test_specification_declares_the_implemented_revision():
    source = os.environ.get("FUNORA_SPEC_DIR")
    if not source:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    import yaml

    doc = yaml.safe_load(
        (Path(source) / "spec/extraction/lot-edit.yaml").read_text(encoding="utf-8")
    )["local_revision"]
    assert {key: value for key, value in doc.items() if key not in {"rule", "migration"}} == {
        "format": "lot-form-v2",
        "serialization": "ascii_json_array",
        "parts": ["format", "fields", "checkbox_values", "checked_names"],
        "sort": "unicode_codepoint",
        "exclude_fields": ["csrf_token", "form_created_at"],
        "hash": "sha256",
        "hex_length": 16,
    }
