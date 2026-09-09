"""Показанный остаток не смешивается с пустотой и неизвестным значением."""

import html
import json
from dataclasses import fields
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser
from test_aclient import _AsyncFakeFetcher
from test_client import _FakeFetcher, _observation
from test_showcase import PROFILE, WHEN, _page

from funora import AsyncClient, Client
from funora._observed import Presence
from funora._showcase import ShowcaseOffer, parse_showcase
from funora.extraction import SELECTORS


def with_stock(value, *, duplicate=False):
    tree = HTMLParser(_page(PROFILE))
    rows = tree.css(SELECTORS["showcase.offers.row"])
    index, row = next(
        (index, row)
        for index, row in enumerate(rows)
        if row.css_first(SELECTORS["showcase.offers.optional_columns.amount"]) is not None
    )
    cell = row.css_first(SELECTORS["showcase.offers.optional_columns.amount"])
    replacement = "" if value is None else f'<div class="tc-amount">{html.escape(value)}</div>'
    if duplicate:
        replacement += replacement
    original = row.html
    changed = original.replace(cell.html, replacement, 1)
    assert changed != original
    document = tree.html
    assert original in document
    return document.replace(original, changed, 1), index


def offer_at(page, index):
    return [offer for section in page.sections(accept_incomplete=True) for offer in section.offers][
        index
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0", 0),
        ("1", 1),
        ("27", 27),
        ("0007", 7),
        (" \n42\t ", 42),
        ("999999999999999999", 999999999999999999),
    ],
)
def test_known_stock_is_an_exact_nonnegative_integer(raw, expected):
    document, index = with_stock(raw)
    result = offer_at(parse_showcase(document, WHEN), index)
    assert result.stock.presence is Presence.PRESENT
    assert type(result.stock.value) is int
    assert result.stock.value == expected
    assert result.amount_text.value == " ".join(raw.split())


@pytest.mark.parametrize(
    "raw,reason",
    [
        (None, "stock_not_shown"),
        ("", "stock_empty"),
        (" \t ", "stock_empty"),
        ("∞", "stock_format_unknown"),
        ("—", "stock_format_unknown"),
        ("1.5", "stock_format_unknown"),
        ("1,5", "stock_format_unknown"),
        ("1 000", "stock_format_unknown"),
        ("1\u00a0000", "stock_format_unknown"),
        ("1\u202f000", "stock_format_unknown"),
        ("1\n000", "stock_format_unknown"),
        ("1e3", "stock_format_unknown"),
        ("+1", "stock_format_unknown"),
        ("-1", "stock_format_unknown"),
        ("-0", "stock_format_unknown"),
        ("١", "stock_format_unknown"),
        ("²", "stock_format_unknown"),
        ("１", "stock_format_unknown"),
        ("1 шт.", "stock_format_unknown"),
        ("в наличии", "stock_format_unknown"),
        ("нет", "stock_format_unknown"),
        ("9999999999999999999", "stock_out_of_range"),
        ("9" * 5000, "stock_out_of_range"),
    ],
    ids=lambda value: value if value is None or len(value) < 50 else "oversized",
)
def test_unknown_stock_never_becomes_zero(raw, reason):
    document, index = with_stock(raw)
    result = offer_at(parse_showcase(document, WHEN), index)
    assert result.stock.presence is Presence.NOT_OBSERVED
    assert result.stock.reason == reason
    assert result.stock.or_none() is None
    if raw is not None:
        assert result.amount_text.value == " ".join(raw.split())


@pytest.mark.parametrize("raw", ["0", "7", ""])
def test_duplicate_stock_cells_are_ambiguous_even_when_they_agree(raw):
    document, index = with_stock(raw, duplicate=True)
    result = offer_at(parse_showcase(document, WHEN), index)
    assert result.stock.presence is Presence.NOT_OBSERVED
    assert result.stock.reason == "stock_ambiguous"


def test_quantity_looking_description_does_not_replace_absent_stock():
    document, index = with_stock(None)
    tree = HTMLParser(document)
    row = tree.css(SELECTORS["showcase.offers.row"])[index]
    description = row.css_first(SELECTORS["showcase.offers.fields.description_text"])
    assert description is not None
    document = tree.html.replace(description.html, '<div class="tc-desc">Осталось 0 шт.</div>', 1)
    result = offer_at(parse_showcase(document, WHEN), index)
    assert result.stock.reason == "stock_not_shown"


def test_old_constructor_keeps_unknown_stock_instead_of_inventing_it():
    document, index = with_stock("7")
    parsed = offer_at(parse_showcase(document, WHEN), index)
    previous_fields = {f.name: getattr(parsed, f.name) for f in fields(parsed) if f.name != "stock"}
    restored = ShowcaseOffer(**previous_fields)
    assert restored.stock.reason == "stock_not_normalized"
    assert restored.stock.or_none() is None


def test_sync_showcase_returns_stock_without_extra_reads_or_writes():
    document, index = with_stock("0")
    transport = _FakeFetcher([_observation(document)])
    with Client(transport=transport) as client:
        result = client.lots.showcase("123")
    assert offer_at(result, index).stock.value == 0
    assert transport.calls == 1
    assert result.reason == "sections_look_capped"


async def test_async_showcase_uses_the_same_stock_semantics():
    document, index = with_stock("27")
    transport = _AsyncFakeFetcher([_observation(document)])
    async with AsyncClient(transport=transport) as client:
        result = await client.lots.showcase("123")
    assert offer_at(result, index).stock.value == 27
    assert transport.calls == 1
    assert result.reason == "sections_look_capped"


def test_anonymous_observation_is_accounted_for_without_private_values():
    probe = json.loads(
        (Path(__file__).parent / "fixtures/probes/showcase-stock.guest.json").read_text()
    )
    assert probe["authentication"] == "guest"
    assert probe["http_status"] == 200
    assert sum(probe["counts"].values()) == probe["rows"]
    assert probe["counts"]["absent"] > 0
    assert (
        sum(example["count"] for example in probe["numeric_examples"])
        == probe["counts"]["ascii_integer"]
    )
    for example in probe["numeric_examples"]:
        document, index = with_stock(example["text"])
        result = offer_at(parse_showcase(document, WHEN), index)
        assert result.stock.value == int(example["text"])
