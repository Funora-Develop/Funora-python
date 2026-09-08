"""Цены рынка: точный показанный текст, границы Money и числовое сравнение."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_catalog import WHEN
from test_client import _FakeFetcher, _observation

from funora import AsyncClient, Client, Money
from funora._budget import Budget
from funora._market import parse_market
from funora._money import parse_display_price
from funora._result import Severity
from funora._snapshot import compare, snapshot_of
from funora.errors import ValidationError

PROBE = json.loads(
    (Path(__file__).parent / "fixtures/probes/market-price-display.guest.json").read_text(
        encoding="utf-8"
    )
)
CASES = [example for page in PROBE["pages"] for example in page["examples"]]


def html(text="1.16", symbol="₽", sort="1.160896"):
    return f'''<div class="navbar-toggle-guest"></div><a class="tc-item" href="https://funpay.com/lots/offer?id=123">
    <div class="tc-user"><div class="avatar-photo" data-href="https://funpay.com/users/456/"></div></div>
    <div class="tc-price" data-s="{sort}"><div>{text} <span class="unit">{symbol}</span></div></div>
    </a>'''


@pytest.mark.parametrize(
    "text,symbol,minor",
    [
        ("1.16", "₽", 1160000),
        ("0.0118", "€", 11800),
        ("0.000012", "€", 12),
        ("0.00116", "₽", 1160),
        ("1 234.56", "$", 1234560000),
        ("0", "₽", 0),
        ("1234.560000", "$", 1234560000),
        ("9223372036854.775807", "$", 2**63 - 1),
    ],
)
def test_displayed_amount_is_exact(text, symbol, minor):
    value = parse_display_price(text, symbol)
    assert value.amount_minor == minor and value.scale == 6
    assert value.currency == {"₽": "RUB", "$": "USD", "€": "EUR"}[symbol]


@pytest.mark.parametrize("example", CASES)
def test_observed_price_fragments_round_trip_without_precision_loss(example):
    value = parse_display_price(example["text"], example["symbol"])
    units, _, fraction = example["text"].replace(" ", "").partition(".")
    assert value.amount_minor == int(units + fraction.ljust(6, "0"))
    assert value.scale == 6


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "-1",
        "+1",
        "1,16",
        "1,234.56",
        "1\u00a0234.56",
        "1 23.45",
        "1 234 56",
        "1e-6",
        "NaN",
        "inf",
        "０.５",
        ".5",
        "1.",
        "1.0000001",
        "9223372036854.775808",
        "9" * 1000,
        " 1.00",
        "1.00\n",
    ],
)
def test_unobserved_or_unrepresentable_formats_are_not_guessed(text):
    with pytest.raises(ValidationError):
        parse_display_price(text, "₽")


@pytest.mark.parametrize("currency", ["", "usd", "USD\n", "$", None, 123])
def test_money_requires_an_exact_currency_code(currency):
    with pytest.raises(ValidationError):
        Money(1, currency)


@pytest.mark.parametrize("scale", [-1, 7, True, False, 2.0, float("nan"), "2", None])
def test_money_scale_matches_the_integer_contract(scale):
    with pytest.raises(ValidationError):
        Money(1, "RUB", scale)


@pytest.mark.parametrize("amount", [-(2**63) - 1, 2**63, True, 1.0])
def test_money_rejects_non_integer_or_overflowing_amounts(amount):
    with pytest.raises(ValidationError):
        Money(amount, "RUB")


def test_money_arithmetic_cannot_overflow_storage():
    assert Money(-(2**63), "RUB").amount_minor == -(2**63)
    maximum = Money(2**63 - 1, "RUB")
    for operation in (
        lambda: maximum + Money(1, "RUB"),
        lambda: maximum * 2,
        lambda: Money(-(2**63), "RUB") - Money(1, "RUB"),
    ):
        with pytest.raises(ValidationError):
            operation()


def test_market_reads_shown_price_and_retains_sort_value():
    page = parse_market(html(), observed_at=WHEN)
    offer = page.offers(accept_incomplete=True)[0]
    assert offer.price.value == Money(1160000, "RUB", 6)
    assert offer.price_text.value == "1.16"
    assert offer.sort_value.value == "1.160896"


@pytest.mark.parametrize("text,symbol", [("1,16", "₽"), ("0.0000001", "€"), ("1.16", "£")])
def test_bad_price_is_a_field_defect_and_preserves_the_offer(text, symbol):
    page = parse_market(html(text, symbol), observed_at=WHEN)
    offer = page.offers(accept_incomplete=True)[0]
    assert not offer.price.is_observed
    assert offer.price.reason == "price_not_normalizable"
    assert offer.price_text.value == text
    assert page.rows_total == page.rows_accepted == 1
    defects = [x for x in page.defects if x.code == "price_not_normalizable"]
    assert len(defects) == 1 and defects[0].severity is Severity.FIELD
    assert defects[0].field_name == "price"


def test_unobserved_price_source_is_never_zero():
    page = parse_market(html(""), observed_at=WHEN)
    assert not page.offers(accept_incomplete=True)[0].price.is_observed


def test_formatting_does_not_create_a_price_change():
    before = snapshot_of(parse_market(html("1 234.56"), observed_at=WHEN), node_id="922")
    same = snapshot_of(parse_market(html("1234.5600", sort="999"), observed_at=WHEN), node_id="922")
    changed = snapshot_of(parse_market(html("1234.560001"), observed_at=WHEN), node_id="922")
    assert before.offers["123"].price.value == Money(1234560000, "RUB", 6)
    assert not compare(before, same).price_changed
    assert len(compare(before, changed).price_changed) == 1


def test_currency_changes_are_not_converted():
    before = snapshot_of(parse_market(html(symbol="$"), observed_at=WHEN), node_id="922")
    after = snapshot_of(parse_market(html(symbol="€"), observed_at=WHEN), node_id="922")
    assert len(compare(before, after).price_changed) == 1


def test_legacy_or_unparsed_snapshot_retains_text_comparison():
    before = snapshot_of(parse_market(html("not parsed"), observed_at=WHEN), node_id="922")
    after = snapshot_of(parse_market(html("different"), observed_at=WHEN), node_id="922")
    assert len(compare(before, after).price_changed) == 1
    assert not compare(before, replace(before, taken_at=WHEN)).price_changed


def test_public_client_exposes_price_and_snapshot():
    transport = _FakeFetcher([_observation(html())] * 2)
    with Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client:
        assert (
            client.market.offers("922").offers(accept_incomplete=True)[0].price.value.amount_minor
            == 1160000
        )
        assert client.market.snapshot("922").offers["123"].price.value == Money(1160000, "RUB", 6)


async def test_async_public_client_uses_same_normalization():
    from test_aclient import _AsyncFakeFetcher

    transport = _AsyncFakeFetcher([_observation(html())] * 2)
    async with AsyncClient(
        public_only=True, public_transport=transport, budget=Budget(names=())
    ) as client:
        page = await client.market.offers("922")
        assert page.offers(accept_incomplete=True)[0].price.value == Money(1160000, "RUB", 6)
        assert (await client.market.snapshot("922")).offers[
            "123"
        ].price.value.amount_minor == 1160000
