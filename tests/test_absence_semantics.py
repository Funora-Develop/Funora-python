"""Отсутствующее поле не становится нулём, пустой строкой или отрицанием."""

import pytest
from selectolax.parser import HTMLParser

from funora import (
    _account,
    _chips,
    _market,
    _order,
    _order_details,
    _own_lots,
    _reviews,
    _showcase,
    _whoami,
)
from funora._matching import match_offer
from funora._observed import Observed, Presence


@pytest.mark.parametrize(
    "read",
    [
        _account._own_text,
        _market._own_text,
        _order._text,
        _own_lots._own_text,
        _showcase._own_text,
        _whoami._text,
    ],
)
def test_text_carriers_distinguish_absence_empty_and_present(read):
    missing = read(None, "description")
    empty = read(HTMLParser("<span></span>").css_first("span"), "description")
    present = read(HTMLParser("<span> ordinary text </span>").css_first("span"), "description")
    assert missing.presence is Presence.NOT_OBSERVED and not missing.is_observed
    assert empty.presence is Presence.EMPTY and empty.is_observed
    assert empty.value == ""
    assert present.presence is Presence.PRESENT and present.value == "ordinary text"


@pytest.mark.parametrize(
    "read",
    [
        _account._attribute,
        _order._attr,
        _own_lots._attribute,
        _showcase._attribute,
        _whoami._attribute,
    ],
)
def test_attribute_carriers_never_invent_a_missing_identifier(read):
    tree = HTMLParser('<a></a><span data-id=""></span><b data-id="123"></b>')
    for node in (None, tree.css_first("a")):
        assert not read(node, "data-id", "identifier").is_observed
    assert read(tree.css_first("span"), "data-id", "identifier").presence is Presence.EMPTY
    assert read(tree.css_first("b"), "data-id", "identifier").value == "123"


def test_optional_rating_and_chip_attributes_are_unknown_without_carriers():
    row = HTMLParser("<div></div>").css_first("div")
    rating, defects = _reviews._rating(row, 0)
    assert not rating.is_observed and defects == []
    assert not _chips._query_param(Observed.missing("no link"), "id", "seller").is_observed
    assert not _chips._raw_attribute(row, "rate").is_observed


def test_missing_or_conflicting_order_flags_remain_unknown():
    names = _order._classes(None)
    assert names == set()
    assert not _order._flag(names, "online").is_observed
    assert not _order._flag({"yes", "no"}, "online", {"yes": True, "no": False}).is_observed


@pytest.mark.parametrize("source", [{}, {"amount": []}, {"amount": {"nested": "value"}}])
def test_order_details_do_not_stringify_an_unread_amount(source):
    assert not _order_details._text(source, "amount").is_observed


def test_unknown_lot_description_cannot_win_matching():
    result = match_offer(
        Observed.present("known product"),
        {
            "unknown": Observed.missing("unread"),
            "known": Observed.present("known product"),
        },
    )
    assert result.offer_id.value == "known"


def test_empty_chip_price_is_unknown_and_numeric_text_is_preserved():
    assert not _chips._own_text(None, "price").is_observed
    assert not _chips._own_text(HTMLParser("<span></span>").css_first("span"), "price").is_observed
    assert (
        _chips._own_text(HTMLParser("<span> 1,20 </span>").css_first("span"), "price").value
        == "1,20"
    )
