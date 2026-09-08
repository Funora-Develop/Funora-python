"""Серверный отбор заказов: URL, границы ввода и наблюдённые ответы."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_aclient import _AsyncFakeFetcher
from test_client import _FakeFetcher, _observation, _page

from funora import AsyncClient, Client, OrderStatus
from funora._account import parse_balance_page
from funora._budget import Budget
from funora._orders import parse_orders_page
from funora._result import Completeness
from funora.errors import ProtocolChangedError, ValidationError

PAGES = Path(__file__).parent / "fixtures/pages"
WHEN = datetime(2026, 9, 9, tzinfo=UTC)


class Transport(_FakeFetcher):
    def __init__(self, responses):
        super().__init__(responses)
        self.paths = []

    def fetch(self, path):
        self.paths.append(path)
        return super().fetch(path)


class AsyncTransport(_AsyncFakeFetcher):
    def __init__(self, responses):
        super().__init__(responses)
        self.paths = []

    async def fetch(self, path):
        self.paths.append(path)
        return await super().fetch(path)


def test_observed_filtered_and_empty_pages():
    page = parse_orders_page(_page("orders-filtered.logged.ru"), observed_at=WHEN)
    assert len(page.rows()) == 12
    assert all(row.status.or_none() is OrderStatus.PAID for row in page.rows())
    assert page.filters_available
    empty = parse_orders_page(_page("orders-filtered-empty.logged.ru"), observed_at=WHEN)
    assert empty.rows() == ()
    assert empty.completeness is Completeness.COMPLETE
    assert empty.reason == "empty_list"
    assert empty.filters_available


@pytest.mark.parametrize("status", ["paid", "closed", "refunded", OrderStatus.PAID])
def test_filters_use_encoded_get_and_general_read_keeps_original_path(status):
    response = _observation(_page("orders-filtered.logged.ru"))
    transport = Transport([response, response])
    with Client(transport=transport, budget=Budget(names=())) as client:
        client.orders.list(
            order_id=" AB12CD34 ",
            buyer=" Покупатель &state=closed+# ",
            status=status,
            game_id=" 333 ",
            section=" lot-1908 ",
        )
        client.orders.list()
    assert len(transport.paths) == 2
    first = urlsplit(transport.paths[0])
    assert first.path == transport.paths[1] == "/orders/trade"
    assert not first.fragment
    assert parse_qs(first.query) == {
        "id": ["AB12CD34"],
        "buyer": ["Покупатель &state=closed+#"],
        "state": [str(status)],
        "game": ["333"],
        "section": ["lot-1908"],
    }


async def test_async_filtered_empty_read():
    transport = AsyncTransport([_observation(_page("orders-filtered-empty.logged.ru"))])
    async with AsyncClient(transport=transport, budget=Budget(names=())) as client:
        result = await client.orders.list(order_id="FUNORAEMPTYTEST20260909")
    assert result.rows() == ()
    assert transport.paths == ["/orders/trade?id=FUNORAEMPTYTEST20260909"]


@pytest.mark.parametrize("name", ["order_id", "buyer", "status", "game_id", "section"])
@pytest.mark.parametrize("value", ["", " \t", False, 42, [], {}])
async def test_invalid_types_and_blank_filters_fail_before_io(name, value):
    sync = Transport([])
    with Client(transport=sync) as client, pytest.raises(ValidationError):
        client.orders.list(**{name: value})
    assert sync.paths == []
    asynchronous = AsyncTransport([])
    async with AsyncClient(transport=asynchronous) as client:
        with pytest.raises(ValidationError):
            await client.orders.list(**{name: value})
    assert asynchronous.paths == []


@pytest.mark.parametrize(
    "filters",
    [
        {"status": "PAID"},
        {"status": "unpaid"},
        {"status": "unknown"},
        {"order_id": "#AB12CD34"},
        {"order_id": "../foo"},
        {"order_id": "АБ123"},
        {"game_id": "-1"},
        {"game_id": "1.5"},
        {"game_id": "１２"},
        {"section": "lot-1908"},
        {"section": "lot-1908", "game_id": None},
    ],
)
def test_invalid_filter_values_fail_before_io(filters):
    transport = Transport([])
    with Client(transport=transport) as client, pytest.raises(ValidationError):
        client.orders.list(**filters)
    assert transport.paths == []


def test_filtered_read_rejects_page_without_filter_form():
    transport = Transport([_observation(_page("orders-trade.logged.ru"))])
    with Client(transport=transport) as client, pytest.raises(ProtocolChangedError):
        client.orders.list(status=OrderStatus.PAID)
    assert transport.paths == ["/orders/trade?state=paid"]


def test_observed_balance_continuation_reaches_terminal_state():
    first = parse_balance_page(_page("account-balance-first.logged.ru"), WHEN)
    end = parse_balance_page(_page("account-balance-end.logged.ru"), WHEN)
    assert first.rows_accepted == 25
    assert first.reason == "more_rows_available"
    assert first.completeness is Completeness.PARTIAL
    assert len(end.transactions()) == 56
    assert end.completeness is Completeness.COMPLETE
    # Это DOM после дописывания строк, а не HTTP-ответ одной следующей страницы.
    assert end.reason == "all_rows_parsed"
