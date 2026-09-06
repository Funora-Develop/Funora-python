"""Каталог кэшируется только после полного чтения и инвалидируется при отказах."""

import asyncio
from dataclasses import replace

import pytest
from test_catalog import ROOT, _page
from test_client import _FakeFetcher, _observation

from funora import AsyncClient, Client
from funora._budget import Budget
from funora.errors import ProtocolChangedError, SessionExpiredError
from funora.operations import OPERATIONS

HTML = _page(ROOT)


def test_cache_preserves_observation_and_refresh_bypasses_it(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("funora._engine.monotonic", lambda: clock[0])
    transport = _FakeFetcher([_observation(HTML)] * 3)
    with Client(transport=transport, budget=Budget(names=())) as client:
        first = client.catalog.categories()
        clock[0] = 1
        assert client.catalog.categories() is first
        assert transport.calls == 1
        fresh = client.catalog.categories(refresh=True)
        assert fresh is not first
        clock[0] += OPERATIONS["catalog.categories"].cache_ttl_ms / 1000
        assert client.catalog.categories() is not fresh
        assert transport.calls == 3


def test_partial_catalog_never_enters_the_cache() -> None:
    response = _observation(HTML)
    transport = _FakeFetcher([replace(response, declared_length=None), response])
    with Client(transport=transport) as client:
        partial = client.catalog.categories()
        assert partial.completeness.value == "partial"
        assert client.catalog.categories().completeness.value == "complete"
        assert transport.calls == 2


@pytest.mark.parametrize("error", [ProtocolChangedError("changed"), SessionExpiredError("expired")])
def test_an_operation_failure_invalidates_cached_catalog(error) -> None:
    def failed_core():
        if False:
            yield
        raise error

    transport = _FakeFetcher([_observation(HTML)] * 2)
    with Client(transport=transport) as client:
        first = client.catalog.categories()
        with pytest.raises(type(error)):
            client.run(failed_core())
        assert client.catalog.categories() is not first


def test_resume_invalidates_cached_session_data() -> None:
    transport = _FakeFetcher([_observation(HTML)] * 2)
    with Client(transport=transport) as client:
        first = client.catalog.categories()
        client.resume()
        assert client.catalog.categories() is not first


async def test_concurrent_async_reads_share_one_fetch() -> None:
    from test_aclient import _AsyncFakeFetcher

    class SlowFetcher(_AsyncFakeFetcher):
        async def fetch(self, path):
            await asyncio.sleep(0)
            return await super().fetch(path)

    transport = SlowFetcher([_observation(HTML)])
    async with AsyncClient(transport=transport) as client:
        pages = await asyncio.gather(*(client.catalog.categories() for _ in range(8)))
        assert all(page is pages[0] for page in pages)
        assert transport.calls == 1


def test_guest_response_invalidates_catalog_from_a_logged_in_session():
    from test_client import _page as client_page

    from funora.capabilities import Capability

    transport = _FakeFetcher(
        [
            _observation(HTML),
            _observation(client_page("orders-trade.guest.ru")),
            _observation(HTML),
        ]
    )
    with Client(transport=transport, budget=Budget(names=())) as client:
        first = client.catalog.categories()
        client.run(
            client.engine.fetch_ok(Capability.REVIEWS_GET, "/users/123/", session_required=False)
        )
        assert client.catalog.categories() is not first
