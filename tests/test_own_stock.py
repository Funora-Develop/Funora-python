"""Остаток собственного лота читается без подмены и без лишних запросов."""

import json
from dataclasses import fields
from html import escape
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser
from test_lot_revision import client_with, complete
from test_update_price import NODE, OFFER, WHEN

from funora._lot_form import LotForm, parse_lot_form
from funora._observed import Presence
from funora._own_lots import OwnLot, parse_own_lots
from funora.errors import PreconditionFailedError
from funora.extraction import SELECTORS

FIXTURES = Path(__file__).parent / "fixtures"
OWN = (FIXTURES / "pages/lots-trade-stock.logged.ru.skeleton.txt").read_text()
FORM = (FIXTURES / "pages/lot-edit-stock.logged.ru.skeleton.txt").read_text()


def own_page(raw, *, active=True, duplicate=False):
    tree = HTMLParser(OWN)
    row = tree.css_first(SELECTORS["lots.rows"])
    row.attrs["class"] = "tc-item" if active else "tc-item warning"
    row.attrs["data-offer"] = "101"
    cell = row.css_first(SELECTORS["lots.fields.amount_text"])
    assert cell is not None
    markup = "" if raw is None else f'<div class="tc-amount">{escape(raw)}</div>'
    if duplicate:
        markup += markup
    document = tree.html
    changed = row.html.replace(cell.html, markup, 1)
    return document.replace(row.html, changed, 1)


def form_with_control(markup):
    tree = HTMLParser(FORM)
    form = tree.css_first(SELECTORS["lot-edit.form"])
    for name, value in (("node_id", NODE), ("offer_id", OFFER)):
        form.css_first(f'[name="{name}"]').attrs["value"] = value
    control = form.css_first(SELECTORS["lot-edit.fields.amount"])
    assert control is not None
    return tree.html.replace(control.html, markup, 1)


def form_page(raw):
    markup = "" if raw is None else f'<input name="amount" type="text" value="{escape(raw)}">'
    return form_with_control(markup)


def first_lot(document):
    return parse_own_lots(document, observed_at=WHEN).lots()[0]


@pytest.mark.parametrize("raw,expected", [("0", 0), ("27", 27), (" 0007 ", 7)])
@pytest.mark.parametrize("active", [True, False])
def test_own_stock_has_identity_and_does_not_depend_on_visibility(raw, expected, active):
    lot = first_lot(own_page(raw, active=active))
    assert lot.offer_id.value == "101"
    assert lot.stock.presence is Presence.PRESENT
    assert lot.stock.value == expected
    assert lot.is_active is active
    assert lot.amount_text.value == raw.strip()


@pytest.mark.parametrize(
    "raw,reason",
    [
        (None, "stock_not_shown"),
        ("", "stock_empty"),
        ("∞", "stock_format_unknown"),
        ("1 000", "stock_format_unknown"),
        ("9" * 19, "stock_out_of_range"),
    ],
)
@pytest.mark.parametrize("surface", ["list", "form"])
def test_unobserved_own_stock_is_not_zero(surface, raw, reason):
    result = (
        first_lot(own_page(raw))
        if surface == "list"
        else parse_lot_form(form_page(raw), observed_at=WHEN)
    )
    assert result.stock.presence is Presence.NOT_OBSERVED
    assert result.stock.reason == reason
    assert result.stock.or_none() is None


def test_duplicate_own_columns_do_not_choose_a_quantity():
    assert first_lot(own_page("0", duplicate=True)).stock.reason == "stock_ambiguous"


@pytest.mark.parametrize("raw,expected", [("0", 0), ("27", 27), (" 0007 ", 7)])
def test_form_stock_normalization_preserves_request_and_revision(raw, expected):
    form = parse_lot_form(form_page(raw), observed_at=WHEN)
    assert form.stock.value == expected
    assert form.fields["amount"] == raw
    assert form.to_request(price="99")["amount"] == raw
    without_quantity = parse_lot_form(form_page(None), observed_at=WHEN)
    assert form.revision != without_quantity.revision


@pytest.mark.parametrize(
    "markup,reason",
    [
        ('<input name="amount" type="text" value="0" disabled>', "stock_disabled"),
        ('<input name="amount" type="hidden" value="0">', "stock_control_unknown"),
        ('<input name="amount" type="number" value="0">', "stock_control_unknown"),
        ('<input name="amount" type="checkbox" checked>', "stock_control_unknown"),
        ('<textarea name="amount">0</textarea>', "stock_control_unknown"),
        ('<select name="amount"><option>0</option></select>', "stock_control_unknown"),
        ('<input name="amount" type="text">', "stock_empty"),
        ('<input name="amount" value="0"><input name="amount" type="checkbox">', "stock_ambiguous"),
    ],
)
def test_unknown_quantity_control_never_confirms_zero(markup, reason):
    form = parse_lot_form(form_with_control(markup), observed_at=WHEN)
    assert form.stock.reason == reason
    assert form.stock.or_none() is None


@pytest.mark.parametrize("extra", ["", 'type="TEXT"', "readonly"])
def test_default_text_type_and_readonly_are_readable(extra):
    page = form_with_control(f'<input name="amount" value="7" {extra}>')
    assert parse_lot_form(page, observed_at=WHEN).stock.value == 7


def test_form_metadata_cannot_replace_an_absent_quantity_control():
    page = form_page(None)
    tree = HTMLParser(page)
    tree.css_first(SELECTORS["lot-edit.form"]).attrs["data-offer"] = '{"amount":0}'
    assert parse_lot_form(tree.html, observed_at=WHEN).stock.reason == "stock_not_shown"


def test_quantity_outside_the_selected_form_is_ignored():
    page = form_page(None).replace("</body>", '<input name="amount" value="0"></body>', 1)
    assert parse_lot_form(page, observed_at=WHEN).stock.reason == "stock_not_shown"


@pytest.mark.parametrize("positional", [False, True])
@pytest.mark.parametrize("kind", ["list", "form"])
def test_previous_constructors_keep_stock_unknown(kind, positional):
    obj = (
        first_lot(own_page("7"))
        if kind == "list"
        else parse_lot_form(form_page("7"), observed_at=WHEN)
    )
    cls = OwnLot if kind == "list" else LotForm
    added = {"stock", "amount_text"} if kind == "list" else {"stock"}
    previous = {f.name: getattr(obj, f.name) for f in fields(obj) if f.name not in added}
    restored = cls(*previous.values()) if positional else cls(**previous)
    assert restored.stock.reason == "stock_not_normalized"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kind", ["list", "form"])
async def test_both_clients_return_own_stock_in_one_read(asynchronous, kind, tmp_path):
    page = own_page("0") if kind == "list" else form_page("0")
    client, transport = client_with(page, asynchronous, tmp_path / "state.json")
    try:
        if kind == "list":
            page = await complete(client.lots.list_own(NODE))
            result = page.lots()[0]
        else:
            result = await complete(client.lots.form(NODE, OFFER))
        assert result.stock.value == 0
        assert len(transport.reads) == 1
        assert not transport.writes
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["update_price", "activate", "deactivate"])
async def test_changed_stock_blocks_stale_mutations_before_submit(
    asynchronous, operation, tmp_path
):
    first = parse_lot_form(form_page("7"), observed_at=WHEN)
    path = tmp_path / "state.json"
    client, transport = client_with(form_page("0"), asynchronous, path)
    try:
        args = (NODE, OFFER, "99") if operation == "update_price" else (NODE, OFFER)
        with pytest.raises(PreconditionFailedError):
            await complete(getattr(client.lots, operation)(*args, expected_revision=first.revision))
        assert len(transport.reads) == 1
        assert not transport.writes and not path.exists()
    finally:
        await complete(client.close())


def test_observed_sources_are_present_in_the_anonymized_documents():
    own = HTMLParser(OWN)
    rows = own.css(SELECTORS["lots.rows"])
    assert len(rows) == 20
    assert all(len(row.css(SELECTORS["lots.fields.amount_text"])) == 1 for row in rows)
    form = HTMLParser(FORM).css_first(SELECTORS["lot-edit.form"])
    controls = form.css(SELECTORS["lot-edit.fields.amount"])
    assert len(controls) == 1 and controls[0].attributes["type"] == "text"
    probe = json.loads((FIXTURES / "probes/own-stock.logged.json").read_text())
    assert probe["form"]["value"] == probe["showcase_value_for_same_offer"] == "1"
    assert first_lot(own_page(probe["form"]["value"])).stock.value == 1
