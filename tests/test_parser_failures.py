"""Повреждённые ответы и отказы HTML-парсера не выдаются за пустые данные."""

import html
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from selectolax.parser import HTMLParser
from test_client import _page

from funora import (
    _account,
    _calc,
    _catalog,
    _chat_history,
    _classify,
    _lot_form,
    _order,
    _order_details,
    _signals,
    _skeleton,
)
from funora._canonical import canonical_dumps
from funora._result import Completeness
from funora.errors import ProtocolChangedError, UnexpectedResponseError, ValidationError

WHEN = datetime(2026, 9, 8, tzinfo=UTC)


def remove(html_source, selector):
    tree = HTMLParser(html_source)
    node = tree.css_first(selector)
    assert node is not None
    return tree.html.replace(node.html, "", 1)


@pytest.mark.parametrize(
    "payload",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {1: "value"},
        {"é": 1, "e\u0301": 2},
        {"set"},
        object(),
    ],
)
def test_canonical_encoding_refuses_values_without_a_unique_representation(payload):
    with pytest.raises(ValidationError):
        canonical_dumps(payload)


@pytest.mark.parametrize("value", [None, [], "wrong"])
def test_structured_records_must_be_objects(value):
    with pytest.raises(ProtocolChangedError, match="способ оплаты не объект"):
        _calc.parse_calculation({"methods": [value]}, asked_price="100", observed_at=WHEN)
    with pytest.raises(ProtocolChangedError, match="запись заказа"):
        _order_details.parse_order_details(
            {"status": "SUCCESS", "data": {"ABC": value}}, asked=("ABC",), observed_at=WHEN
        )


@pytest.mark.parametrize("prefix", ["", "catalog-search.guest.ru"])
def test_search_rejects_invalid_unicode_before_accepting_results(prefix):
    markup = _page(prefix) if prefix else ""
    with pytest.raises(ProtocolChangedError, match="Unicode"):
        _catalog.parse_catalog_search(json.dumps({"html": markup + "\ud800"}), "test", WHEN)


@pytest.mark.parametrize("failure", ["exception", "no-root"])
def test_skeletonizer_refuses_parser_failure_without_returning_raw_html(monkeypatch, failure):
    def parser(source):
        if failure == "exception":
            raise ValueError("private-html-sentinel")
        return SimpleNamespace(root=None)

    monkeypatch.setattr(_skeleton, "HTMLParser", parser)
    with pytest.raises(_skeleton.SkeletonError) as caught:
        _skeleton.skeletonize("private-html-sentinel")
    assert "private-html-sentinel" not in str(caught.value)


def test_failed_text_parser_cannot_invent_a_classification(monkeypatch):
    def failed(source):
        raise ValueError("synthetic parser failure")

    monkeypatch.setattr(_classify, "HTMLParser", failed)
    assert _classify._page_text("blocked or success?") == ""
    monkeypatch.setattr(
        _classify, "HTMLParser", lambda source: SimpleNamespace(body=None, root=None)
    )
    assert _classify._page_text("blocked or success?") == ""
    assert not _classify._is_known_page(None, (".app",))


def test_history_refuses_an_unparseable_message(monkeypatch):
    monkeypatch.setattr(
        _chat_history,
        "HTMLParser",
        lambda source: SimpleNamespace(css_first=lambda _: None, body=None),
    )
    with pytest.raises(UnexpectedResponseError, match="разобрать"):
        _chat_history._message({"html": "<div>message</div>"}, "123", 0, "funpay.com")


def test_lot_form_requires_every_field_and_ignores_non_submitting_controls():
    with pytest.raises(ProtocolChangedError, match="нет формы"):
        _lot_form.parse_lot_form("<div></div>", observed_at=WHEN)
    with pytest.raises(ProtocolChangedError, match="неполна"):
        _lot_form.parse_lot_form('<form class="form-offer-editor"></form>', observed_at=WHEN)
    page = _page("lot-edit.logged.ru")
    controls = (
        '<input name="ignored" disabled value="secret">'
        '<input name="submit" type="submit" value="save">'
    )
    original = _lot_form.parse_lot_form(page, observed_at=WHEN)
    changed = _lot_form.parse_lot_form(
        page.replace("</form>", controls + "</form>", 1), observed_at=WHEN
    )
    assert changed.to_request() == original.to_request()
    assert changed.revision == original.revision


def test_order_keeps_damage_when_a_parameter_or_category_disappears():
    page = _page("order.logged.ru")
    missing_category = _order.parse_order_page(remove(page, _order._CATEGORY_ITEM), WHEN)
    assert "category_item_missing" in {one.code for one in missing_category.defects}
    missing_label = _order.parse_order_page(remove(page, _order._PARAM_LABEL), WHEN)
    assert "param_label_missing" in {one.code for one in missing_label.defects}
    amount, symbol = _order._amount(HTMLParser("<div></div>"))
    assert not amount.is_observed and not symbol.is_observed


def test_balance_requires_a_table_and_marks_a_missing_header():
    page = _page("account-balance.logged.ru")
    with pytest.raises(ProtocolChangedError, match="таблицы операций"):
        _account.parse_balance_page(remove(page, _account._TABLE), WHEN)
    result = _account.parse_balance_page(remove(page, _account._HEADER), WHEN)
    assert result.completeness is not Completeness.COMPLETE
    assert "header_missing" in {one.code for one in result.defects}


@pytest.mark.parametrize("hidden", [False, True])
def test_signal_relations_do_not_hide_missing_or_disagreeing_positions(hidden):
    markup = (
        '<a class="contact-item" data-node-msg="1"></a>'
        '<a class="contact-item" data-node-msg="1" data-user-msg="2"></a>'
        f'<span class="badge-chat {"hidden" if hidden else ""}"></span>'
    )
    result = _signals.relations(markup)
    assert result.incomplete == 1 and result.differing == 1
    report = _signals.format_relations(result)
    assert "расхождений" in report or "расходятся" in report


def test_skeleton_masks_nested_data_and_keeps_only_boolean_structure():
    data = {"channels": [True, False, None, {"user_id": "private-value-sentinel"}]}
    source = f'<div data-data="{html.escape(json.dumps(data))}"></div>'
    skeleton = _skeleton.skeletonize(source)
    masked = json.loads(HTMLParser(skeleton).css_first("div").attributes["data-data"])
    assert masked["channels"][:3] == [True, False, None]
    assert "private-value-sentinel" not in skeleton
    assert len(masked["channels"]) == 4
    for value in ("{broken-private-sentinel", "", "   "):
        assert "private-sentinel" not in _skeleton._mask_attr("data-private", value, {})
    assert _skeleton.mask_path("") == ""
    assert "личное" not in _skeleton.mask_path("/users/личное/")
