"""Структурные повреждения страниц сохраняют незнание и полноту явно."""

import html
import json
from types import SimpleNamespace

import pytest
from selectolax.parser import HTMLParser
from test_client import _page
from test_parser_failures import WHEN, remove

from funora import (
    _account,
    _chats,
    _classify,
    _lot_form,
    _order,
    _orders,
    _runner,
    _skeleton,
    _thread,
    _whoami,
)
from funora._result import Completeness
from funora.errors import ProtocolChangedError


def test_balance_values_disappearing_are_page_damage():
    source = _page("account-balance.logged.ru")
    source = source.replace('class="balances-value"', 'class="changed-value"')
    result = _account.parse_balance_page(source, WHEN)
    assert result.balances == ()
    assert "balances_missing" in {one.code for one in result.defects}
    assert result.completeness is Completeness.PARTIAL


def test_nested_transaction_carrier_and_conflicting_statuses():
    row = HTMLParser(
        '<a class="tc-item transaction-status-a transaction-status-b"><b id="nested"></b></a>'
    ).css_first("a")
    assert _account._carrier(row, "b").attributes["id"] == "nested"
    parsed = _account._row_of(row, 0)
    assert not parsed.status_class.is_observed
    assert parsed.status_class.reason == "status_classes_disagree"


@pytest.mark.parametrize("data", [None, [], {"currencies": {"rub": "broken"}}])
def test_unstructured_withdrawal_metadata_does_not_invent_channels(data):
    source = (
        '<div class="withdraw-box"'
        + (
            f' data-data="{html.escape(json.dumps(data), quote=True)}"'
            if data is not None
            else ' data-data=""'
        )
        + "></div>"
    )
    assert _account.parse_withdrawal_box(source) == ((), (), ())


@pytest.mark.parametrize("kind", ["balance", "thread"])
def test_unrecognised_children_never_become_an_empty_history(kind):
    if kind == "balance":
        source = _page("account-balance.logged.ru")
        source = source.replace('class="tc-item ', 'class="changed-row ')

        def parse(s):
            return _account.parse_balance_page(s, WHEN)
    else:
        source = _page("chat-thread.logged.ru")
        source = source.replace("chat-msg-item", "changed-message")

        def parse(s):
            return _thread.parse_thread(s, observed_at=WHEN)

    with pytest.raises(ProtocolChangedError, match="ни одно"):
        parse(source)


def test_chat_href_fallback_requires_a_real_node_identifier():
    row = HTMLParser('<a href="/chat/?node=123"></a>').css_first("a")
    entry, _ = _chats._parse_row(row, 0)
    assert entry.node_id == "123"
    missing, defects = _chats._parse_row(HTMLParser('<a href=""></a>').css_first("a"), 0)
    assert missing is None and defects[0].code == "node_id_not_extractable"


def test_chat_missing_container_refuses_and_empty_container_is_unknown():
    with pytest.raises(ProtocolChangedError, match="контейнера"):
        _chats.parse_chats_page(
            remove(_page("chat.logged.ru"), _chats._ROWS_CONTAINER), observed_at=WHEN
        )
    result = _chats.parse_chats_page(
        '<div class="chat-contacts"><div class="contact-list"></div></div>', observed_at=WHEN
    )
    assert result.completeness is Completeness.UNKNOWN
    assert not result.unread_badge_visible.is_observed


def test_one_bad_chat_row_preserves_other_contacts_as_partial():
    source = _page("chat.logged.ru")
    row = HTMLParser(source).css_first(_chats._ROW)
    source = source.replace(row.html, '<a class="contact-item" href=""></a>', 1)
    result = _chats.parse_chats_page(source, observed_at=WHEN)
    assert result.completeness is Completeness.PARTIAL
    assert result.reason == "row_defects" and result.rows_rejected == 1


def test_orders_missing_fields_are_observed_without_guessing():
    source = _page("orders-trade.logged.ru")
    row = HTMLParser(source).css_first(_orders._ROW)
    money, defect = _orders._money(None, row)
    assert not money.is_observed and defect is None
    assert _orders._classes(None, without="tc-item") == frozenset()
    tree = HTMLParser(source)
    symbol = tree.css_first(_orders._ROW).css_first(
        _orders.SELECTORS["orders.fields.currency_symbol_text"]
    )
    assert symbol is not None
    row = tree.css_first(_orders._ROW)
    broken_row = row.html.replace(symbol.html, '<span class="unit">$</span>', 1)
    broken = tree.html.replace(row.html, broken_row, 1)
    result = _orders.parse_orders_page(broken, observed_at=WHEN)
    assert any(one.field_name == "price" for one in result.defects)


def test_orders_missing_container_refuses_and_missing_header_is_partial():
    source = _page("orders-trade.logged.ru")
    with pytest.raises(ProtocolChangedError, match="контейнера"):
        _orders.parse_orders_page(remove(source, _orders._ROWS_CONTAINER), observed_at=WHEN)
    result = _orders.parse_orders_page(remove(source, _orders._HEADER), observed_at=WHEN)
    assert result.completeness is Completeness.PARTIAL
    assert "header_missing" in {one.code for one in result.defects}


def test_thread_empty_links_and_nested_message_damage():
    source = _page("chat-thread.logged.ru")
    tree = HTMLParser(source)
    text = tree.css_first(_thread._TEXT)
    changed = tree.html.replace(
        text.html,
        '<div class="chat-msg-text"><a href=" "></a><div class="chat-msg-item"></div></div>',
        1,
    )
    result = _thread.parse_thread(changed, observed_at=WHEN)
    assert len(result) == len(tuple(result.messages(accept_incomplete=True)))
    assert "message_inside_message_text" in {one.code for one in result.defects}
    assert result.completeness is Completeness.PARTIAL


def test_ambiguous_contact_does_not_choose_the_first_row():
    tree = HTMLParser(
        '<div class="chat chat-float" data-id="123"></div>'
        '<a class="contact-item" data-id="123"></a>'
        '<a class="contact-item" data-id="123"></a>'
    )
    row, defects = _runner._active_contact(tree, tree.css_first(_runner._WIDGET))
    assert row is None and defects[0].code == "contact_rows_ambiguous"


def test_nonobject_channel_data_cannot_confirm_sending():
    result = _runner.classify_send_response(
        json.dumps({"response": {"error": None}, "objects": [{"type": "chat_node", "data": []}]}),
        sent_to="123",
    )
    assert result.outcome is _runner.SendOutcome.UNCONFIRMED


def test_missing_username_does_not_fall_back_to_other_users():
    name, defects = _whoami._username(HTMLParser('<a href="/users/123/">other</a>'))
    assert not name.is_observed and defects == []


def test_form_unnamed_and_nondata_controls_are_excluded():
    source = _page("lot-edit.logged.ru")
    extra = '<input value="ignored"><input type="file" name="ignored" value="ignored">'
    baseline = _lot_form.parse_lot_form(source, observed_at=WHEN)
    tree = HTMLParser(source)
    form = tree.css_first(_lot_form._FORM)
    changed_html = tree.html.replace(form.html, form.html.replace("</form>", extra + "</form>"), 1)
    assert extra in changed_html
    changed = _lot_form.parse_lot_form(changed_html, observed_at=WHEN)
    assert changed.to_request() == baseline.to_request()
    assert changed.revision == baseline.revision


def test_amount_selector_without_value_does_not_invent_amount(monkeypatch):
    monkeypatch.setattr(_order, "_AMOUNT_BLOCK", ".param-item")
    amount, currency = _order._amount(HTMLParser('<div class="param-item"><h5>Price</h5></div>'))
    assert not amount.is_observed and not currency.is_observed


def test_nonnumeric_review_rating_remains_unknown():
    source = _page("order.logged.ru")
    tree = HTMLParser(source)
    assert tree.css_first(_order._REVIEW_CONTAINER) is not None
    broken = tree.html.replace('data-rating=""', 'data-rating="bad"')
    assert broken != tree.html
    result = _order.parse_order_page(broken, WHEN)
    assert not result.review_rating.is_observed
    assert "review_rating_not_a_number" in {one.code for one in result.defects}


def test_boolean_html_attribute_is_rendered_without_invented_value():
    node = SimpleNamespace(tag="input", attributes={"disabled": None}, iter=lambda **kwargs: [])
    output = []
    _skeleton._render(node, output, 0, {})
    assert output == ["<input disabled/>"]


def test_compressed_body_lengths_are_not_compared_as_plaintext(caplog):
    verdict = _classify.classify(
        status=200,
        final_url="https://funpay.com/",
        html=_page("chat.logged.ru"),
        declared_length=10000,
        received_length=100,
        content_encoding="gzip",
        expected_host="funpay.com",
    )
    assert verdict.reason != "body_truncated"
    assert "целостность не проверена" in caplog.text
