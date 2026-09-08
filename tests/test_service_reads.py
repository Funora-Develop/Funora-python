"""Публичные службы возвращают разобранные страницы в обеих моделях клиента."""

from dataclasses import replace

import pytest
from test_aclient import _AsyncFakeFetcher
from test_client import _FakeFetcher, _observation, _page
from test_outbound_restore import complete

from funora import AsyncClient, Budget, Client, StateFile
from funora._result import Completeness
from funora.errors import AccessBlockedError, ValidationError


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_account_reads_bind_identity_and_refresh_from_the_page(tmp_path, asynchronous):
    replies = [_observation(_page("chat.logged.ru"))] * 2
    fetcher = (_AsyncFakeFetcher if asynchronous else _FakeFetcher)(replies)
    client = (AsyncClient if asynchronous else Client)(
        transport=fetcher, budget=Budget(names=()), state_path=tmp_path / "state.json"
    )
    try:
        account = await complete(client.account.get())
        refreshed = await complete(client.account.refresh())
        assert account.user_id.is_observed
        assert refreshed.user_id.value == account.user_id.value
        assert StateFile(tmp_path / "state.json").load()["account"] == account.user_id.value
        assert fetcher.calls == 2
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_order_and_lots_services_return_readable_identifiers(asynchronous):
    replies = [
        _observation(_page(name))
        for name in ("order.logged.ru", "lots-trade.logged.ru", "user.logged.ru")
    ]
    fetcher = (_AsyncFakeFetcher if asynchronous else _FakeFetcher)(replies)
    client = (AsyncClient if asynchronous else Client)(transport=fetcher, budget=Budget(names=()))
    try:
        order = await complete(client.orders.get("ABC"))
        own = await complete(client.lots.list_own("123"))
        showcase = await complete(client.lots.showcase("123"))
        assert order.order_number.is_observed
        assert len(own.lots()) == 20
        assert len({one.offer_id.value for one in own.lots()}) == 20
        assert showcase.offers_total == 158
        assert fetcher.calls == 3
    finally:
        await complete(client.close())


@pytest.mark.parametrize("name", ["orders.get", "lots.list_own", "lots.showcase", "reviews.get"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_bad_identifiers_are_refused_before_transport(name, asynchronous):
    fetcher = (_AsyncFakeFetcher if asynchronous else _FakeFetcher)([])
    client = (AsyncClient if asynchronous else Client)(transport=fetcher, budget=Budget(names=()))
    service, method = name.split(".")
    try:
        with pytest.raises(ValidationError):
            await complete(getattr(getattr(client, service), method)("../foreign"))
        assert fetcher.calls == 0
    finally:
        await complete(client.close())


@pytest.mark.parametrize(
    "name,fixture,args",
    [
        ("lots.showcase", "user.logged.ru", ("123",)),
        ("account.balance", "account-balance.logged.ru", ()),
        ("catalog.field_schema", "catalog-fields.logged.ru", ("123",)),
        ("chats.list", "chat.logged.ru", ()),
        ("reviews.get", "reviews-first.guest.ru", ("123",)),
        ("market.chips", "chips.trimmed.guest.ru", ("123",)),
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_missing_length_never_claims_a_complete_page(name, fixture, args, asynchronous):
    html = _page(fixture)
    if name == "reviews.get":
        html = html.replace(
            'name="user_id" type="hidden" value="T7:d#1"',
            'name="user_id" type="hidden" value="123"',
        )
    response = replace(_observation(html), declared_length=None)
    fetcher = (_AsyncFakeFetcher if asynchronous else _FakeFetcher)([response])
    public = name.startswith("market.")
    options = (
        {"public_only": True, "public_transport": fetcher} if public else {"transport": fetcher}
    )
    client = (AsyncClient if asynchronous else Client)(**options, budget=Budget(names=()))
    service, method = name.split(".")
    try:
        result = await complete(getattr(getattr(client, service), method)(*args))
        assert result.completeness is not Completeness.COMPLETE
        assert result.reason
        assert fetcher.calls == 1
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_stopped_catalog_cannot_return_cached_data(asynchronous):
    from test_outbound_restore import AsyncOffline, Offline

    client = (AsyncClient if asynchronous else Client)(
        transport=AsyncOffline() if asynchronous else Offline()
    )
    failure = AccessBlockedError("synthetic stop")
    client.engine._stopped = failure
    try:
        with pytest.raises(AccessBlockedError) as caught:
            await complete(client.catalog.categories())
        assert caught.value is failure
        client.engine.save_delivery()
    finally:
        await complete(client.close())
