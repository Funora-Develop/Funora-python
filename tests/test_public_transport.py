"""Публичный рынок не получает cookie аккаунта и не останавливает личную полосу."""

from dataclasses import replace

import httpx
import pytest
from test_chips import _page as chips_page
from test_client import _FakeFetcher, _observation, _page
from test_market import _page as market_page

from funora import AsyncClient, Client, Proxy, Secret
from funora._budget import Budget
from funora._engine import Submit
from funora._transport import Fetcher
from funora.capabilities import Capability
from funora.errors import AccessBlockedError, ConfigurationError


def _response(html, *, headers=None):
    raw = html.encode()
    return httpx.Response(
        200,
        stream=httpx.ByteStream(raw),
        headers={"Content-Length": str(len(raw)), **(headers or {})},
    )


def test_public_reads_have_independent_cookie_jar_and_pool(monkeypatch):
    original = httpx.Client
    clients, requests = [], []

    def handler(request):
        requests.append(request)
        public = request.url.path.startswith("/lots/")
        return _response(
            market_page() if public else _page("orders-trade.logged.ru"),
            headers={"Set-Cookie": "guest_state=present; Path=/"} if public else {},
        )

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        result = original(**kwargs)
        clients.append(result)
        return result

    monkeypatch.setattr("funora._transport.httpx.Client", factory)
    with Client(Secret("test-key"), budget=Budget(names=())) as client:
        client.orders.list()
        client.market.offers("123")
        client.market.offers("123")
        client.orders.list()
        assert client.engine._identity is client._public_engine._identity
        assert client.engine._budget is not client._public_engine._budget
    cookies = [request.headers.get("cookie", "") for request in requests]
    assert cookies[0] == cookies[3] == "golden_key=test-key"
    assert "golden_key" not in cookies[1] + cookies[2]
    assert "guest_state=present" in cookies[2]
    assert len(clients) == 2 and all(c.is_closed for c in clients)


def test_public_client_works_without_a_secret_and_refuses_private_calls():
    fetcher = _FakeFetcher([_observation(market_page())])
    with Client(public_only=True, public_transport=fetcher, budget=Budget(names=())) as client:
        assert client.market.offers("123").rows_accepted > 0
        with pytest.raises(ConfigurationError, match="public_only"):
            client.orders.list()
    assert fetcher.calls == 1


def test_chips_uses_the_public_lane_without_a_secret():
    public = _FakeFetcher([_observation(chips_page())])
    with Client(public_only=True, public_transport=public, budget=Budget(names=())) as client:
        assert len(client.market.chips("123").offers()) > 0
        assert client.account.capabilities().state_of(Capability.CHIPS_OFFERS) == client.capability(
            Capability.CHIPS_OFFERS
        )
    assert public.calls == 1


def test_failure_of_public_page_does_not_stop_the_authenticated_client():
    personal = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    public = _FakeFetcher([replace(_observation("blocked"), status=403)])
    with Client(transport=personal, public_transport=public, budget=Budget(names=())) as client:
        with pytest.raises(AccessBlockedError):
            client.market.offers("123")
        assert client._public_engine.stopped is not None
        assert client.stopped is None
        assert len(client.orders.list())
        assert client.capability(Capability.MARKET_OFFERS) == client._public_engine.capability(
            Capability.MARKET_OFFERS
        )
        assert client.account.capabilities().state_of(
            Capability.MARKET_OFFERS
        ) == client.capability(Capability.MARKET_OFFERS)
        client.resume()
        assert client._public_engine.stopped is None


def test_custom_transport_does_not_silently_fall_back_to_real_network():
    with (
        Client(transport=_FakeFetcher([])) as client,
        pytest.raises(ConfigurationError, match="public_transport"),
    ):
        client.market.offers("123")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"public_only": True, "secret": Secret("test")},
        {"public_only": True, "state_path": "must-not-be-created.json"},
        {"secret": Secret("test"), "account_id": ""},
        {"secret": Secret("test"), "account_id": "   "},
        {"secret": Secret("test"), "account_id": None},
    ],
)
def test_conflicting_configuration_fails_before_io(kwargs):
    with pytest.raises(ConfigurationError):
        Client(**kwargs)


def test_same_or_authenticated_public_transport_is_rejected():
    fake = _FakeFetcher([])
    with pytest.raises(ConfigurationError):
        Client(transport=fake, public_transport=fake)
    with Fetcher(Secret("test")) as authenticated, pytest.raises(ConfigurationError):
        Client(public_only=True, public_transport=authenticated)


def test_selected_proxy_is_applied_before_transports_are_built(monkeypatch):
    received = []
    original = httpx.Client

    def factory(**kwargs):
        received.append(kwargs.pop("proxy", None))
        kwargs["transport"] = httpx.MockTransport(lambda request: _response(market_page()))
        return original(**kwargs)

    monkeypatch.setattr("funora._transport.httpx.Client", factory)
    with Client(
        Secret("test"),
        proxies=(Proxy("first", "https://proxy.test:8443"),),
        budget=Budget(names=()),
    ) as client:
        client.market.offers("123")
    assert received == ["https://proxy.test:8443", "https://proxy.test:8443"]


async def test_async_public_client_reads_and_closes_both_lanes(monkeypatch):
    original = httpx.AsyncClient
    clients, cookies = [], []

    def handler(request):
        cookies.append(request.headers.get("cookie", ""))
        return _response(
            chips_page()
            if request.url.path.startswith("/chips/")
            else market_page()
            if request.url.path.startswith("/lots/")
            else _page("orders-trade.logged.ru")
        )

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        result = original(**kwargs)
        clients.append(result)
        return result

    monkeypatch.setattr("funora._transport.httpx.AsyncClient", factory)
    async with AsyncClient(Secret("async-test"), budget=Budget(names=())) as client:
        await client.market.snapshot("123")
        await client.market.chips("123")
        await client.orders.list()
    assert cookies == ["", "", "golden_key=async-test"]
    assert len(clients) == 2 and all(c.is_closed for c in clients)


def test_account_configuration_selects_distinct_nested_budgets():
    with (
        Client(transport=_FakeFetcher([]), account_id="a") as first,
        Client(transport=_FakeFetcher([]), account_id="b") as second,
        Client(transport=_FakeFetcher([]), account_id="a") as same,
    ):
        assert first.engine._budget is same.engine._budget
        assert first.engine._budget is not second.engine._budget
        assert first.engine._identity is second.engine._identity


def test_concurrent_public_reads_create_one_pool(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    original = httpx.Client
    created = []

    def factory(**kwargs):
        sleep(0.02)
        kwargs["transport"] = httpx.MockTransport(lambda request: _response(market_page()))
        result = original(**kwargs)
        created.append(result)
        return result

    monkeypatch.setattr("funora._transport.httpx.Client", factory)
    with Client(public_only=True, budget=Budget(names=())) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(lambda _: client.market.offers("123"), range(8)))
        assert all(page.rows_accepted > 0 for page in pages)
    assert len(created) == 1
    assert all(pool.is_closed for pool in created)


def test_closed_public_client_does_not_open_a_pool(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("закрытый клиент не должен создавать транспорт")

    monkeypatch.setattr("funora._transport.httpx.Client", forbidden)
    client = Client(public_only=True)
    client.close()
    with pytest.raises(ConfigurationError, match="закрыт"):
        client.market.offers("123")


def test_public_driver_refuses_a_mutation_before_transport():
    public = _FakeFetcher([])
    closed = []

    def unsafe():
        try:
            yield Submit("/must-not-send", {}, {})
        finally:
            closed.append(True)

    with (
        Client(public_only=True, public_transport=public) as client,
        pytest.raises(ConfigurationError, match="только чтение"),
    ):
        client.run(unsafe(), engine=client._public_engine)
    assert public.calls == 0
    assert closed == [True]


async def test_async_anonymous_lifecycle_and_failure_isolation(monkeypatch):
    original = httpx.AsyncClient
    made = []

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(403, stream=httpx.ByteStream(b"blocked"))
        )
        result = original(**kwargs)
        made.append(result)
        return result

    monkeypatch.setattr("funora._transport.httpx.AsyncClient", factory)
    async with AsyncClient(public_only=True, budget=Budget(names=())) as client:
        with pytest.raises(AccessBlockedError):
            await client.market.offers("123")
        assert client.stopped is not None
        profile = await client.account.capabilities()
        assert profile.state_of(Capability.MARKET_OFFERS) == client.capability(
            Capability.MARKET_OFFERS
        )
        client.resume()
        assert client.stopped is None
        with pytest.raises(ConfigurationError, match="public_only"):
            await client.orders.list()
    with pytest.raises(ConfigurationError, match="закрыт"):
        await client.market.offers("123")
    assert len(made) == 1 and made[0].is_closed
