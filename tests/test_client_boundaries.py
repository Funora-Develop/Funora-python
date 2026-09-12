"""Закрытие клиента и отказы транспорта действуют на все службы одинаково."""

import pytest
from test_dispatch_concurrent import BATCH
from test_outbound_restore import AsyncOffline, Offline, complete

from funora import AsyncClient, Budget, Client, Router
from funora._engine import Ask, Deliver, Fetch, Query, Submit, Upload
from funora.errors import ConfigurationError, NetworkError


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"account_id": "", "public_only": True},
        {"public_only": True, "state_path": "never-created.json"},
    ],
)
def test_async_configuration_is_rejected_before_creating_resources(kwargs):
    with pytest.raises(ConfigurationError):
        AsyncClient(**kwargs)


async def test_async_public_transport_must_be_distinct_and_anonymous():
    from funora._secret import Secret
    from funora._transport import AsyncFetcher

    transport = AsyncOffline()
    with pytest.raises(ConfigurationError):
        AsyncClient(transport=transport, public_transport=transport)
    async with AsyncFetcher(Secret("synthetic")) as authenticated:
        with pytest.raises(ConfigurationError):
            AsyncClient(public_only=True, public_transport=authenticated)


async def test_async_proxy_locale_and_missing_public_lane_do_not_trigger_io():
    from funora._observed import Observed
    from funora._proxies import Proxy

    async with AsyncClient(
        transport=AsyncOffline(),
        budget=Budget(names=()),
        proxies=(Proxy("boundary-proxy", "https://proxy.example:8080"),),
    ) as client:
        assert client.engine._settings.proxy_url == "https://proxy.example:8080"
        client.engine._state.locale = Observed.present("ru")
        assert client.locale.value == "ru"
        with pytest.raises(ConfigurationError, match="public_transport"):
            await client.market.offers("123")
    async with AsyncClient(public_only=True) as public:
        public._public_engine._state.locale = Observed.present("en")
        assert public.locale.value == "en"
        with pytest.raises(ConfigurationError, match="личный транспорт"):
            await public._fetch("/orders/")


def test_sync_anonymous_client_refuses_direct_private_fetch():
    with (
        Client(public_only=True) as client,
        pytest.raises(ConfigurationError, match="личный транспорт"),
    ):
        client._fetch("/orders/")


OPERATIONS = [
    ("orders.get", ("ABC",), {}),
    ("orders.list", (), {}),
    ("orders.details", ("ABC",), {}),
    ("orders.refund", ("ABC",), {}),
    ("reviews.get", ("123",), {}),
    ("reviews.leave", ("ABC",), {"rating": 5, "text": "test"}),
    ("reviews.remove", ("ABC",), {}),
    ("chats.list", (), {}),
    ("chats.send_text", ("123", "test"), {}),
    ("chats.thread", ("123",), {}),
    ("chats.history_before", ("123",), {"before_message_id": "1"}),
    ("chats.mark_read", ("123",), {}),
    ("chats.send_image", ("123", b"test"), {"filename": "image.png"}),
    ("chats.buyer_viewing", ("123", "456"), {}),
    ("account.get", (), {}),
    ("account.refresh", (), {}),
    ("account.health", (), {}),
    ("account.balance", (), {}),
    ("account.switch_currency", ("RUB",), {}),
    ("lots.form", ("123", "456"), {}),
    ("lots.update_price", ("123", "456", "100"), {"expected_revision": "revision"}),
    ("lots.list_own", ("123",), {}),
    ("lots.showcase", ("123",), {}),
    ("lots.promote", ("123", "456"), {}),
    ("lots.activate", ("123", "456"), {"expected_revision": "revision"}),
    ("lots.deactivate", ("123", "456"), {"expected_revision": "revision"}),
    ("lots.calculate_prices", ("123", "100"), {}),
    ("market.offers", ("123",), {}),
    ("market.snapshot", ("123",), {}),
    ("market.chips", ("123",), {}),
    ("market.calculate_chip_prices", ("123", "100"), {}),
    ("catalog.categories", (), {}),
    ("catalog.search", ("test",), {}),
    ("catalog.field_schema", ("123",), {}),
]


@pytest.mark.parametrize("name,args,kwargs", OPERATIONS, ids=[one[0] for one in OPERATIONS])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_closed_services_never_reopen_transport(name, args, kwargs, asynchronous):
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    client = kind(transport=transport, budget=Budget(names=()))
    await complete(client.close())
    service, method = name.split(".")
    with pytest.raises(ConfigurationError, match="закрыт"):
        await complete(getattr(getattr(client, service), method)(*args, **kwargs))
    assert client._public_fetcher is None


REQUESTS = [
    Fetch("/test"),
    Submit("/test", {}, {}),
    Upload("/test", "file", "a.png", b"image", "image/png", {}),
    Query("/test", {}, {}),
    Ask("/test", {}),
]


@pytest.mark.parametrize("command", REQUESTS)
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_transport_failure_is_returned_to_the_core_once(command, asynchronous):
    calls = []
    failure = NetworkError("synthetic failure")

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    async def afail(*args, **kwargs):
        fail(*args, **kwargs)

    class Broken(AsyncOffline if asynchronous else Offline):
        fetch = submit = upload = query = ask = staticmethod(afail if asynchronous else fail)

    client = (AsyncClient if asynchronous else Client)(transport=Broken(), budget=Budget(names=()))
    closed = []

    def core():
        try:
            try:
                yield command
            except NetworkError as exc:
                assert exc is failure
                return "uncertain"
            pytest.fail("отказ транспорта потерян")
        finally:
            closed.append(True)

    try:
        assert await complete(client.run(core())) == "uncertain"
        assert len(calls) == 1
        assert closed == [True]
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_driver_reports_each_handler_failure_and_closes_the_core(asynchronous):
    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    client = kind(transport=transport, budget=Budget(names=()))
    router = Router()
    seen, errors, closed = [], [], []

    @router.on()
    def broken(event):
        seen.append(event.id)
        raise ValueError("synthetic handler failure")

    def core():
        try:
            result = yield Deliver((BATCH[0], BATCH[1], BATCH[3]))
            return result
        finally:
            closed.append(True)

    try:
        result = await complete(client.run(core(), router=router, on_handler_error=errors.append))
        assert seen == [BATCH[0].id, BATCH[3].id]
        assert tuple(errors) == result.errors and len(errors) == 2
        assert closed == [True]
        closed.clear()
        with pytest.raises(ConfigurationError, match="реестр обработчиков"):
            await complete(client.run(core()))
        assert closed == [True]
    finally:
        await complete(client.close())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_unimplemented_withdrawal_is_a_typed_absent_member(asynchronous):
    from funora.errors import NotImplementedOperationError

    kind, transport = (AsyncClient, AsyncOffline()) if asynchronous else (Client, Offline())
    client = kind(transport=transport, budget=Budget(names=()))
    try:
        with pytest.raises(NotImplementedOperationError, match="withdraw_stays_unwritten"):
            assert client.account.withdraw is None
        assert not hasattr(client.account, "withdraw")
        with pytest.raises(AttributeError) as caught:
            assert client.account.typo is None
        assert type(caught.value) is AttributeError
    finally:
        await complete(client.close())


def test_close_while_waiting_for_public_transport_does_not_reopen_it():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    waiting, closed = Event(), Event()
    gate = Lock()

    class ObservedLock:
        def __enter__(self):
            waiting.set()
            gate.acquire()

        def __exit__(self, *args):
            gate.release()

    class Transport(Offline):
        def close(self):
            closed.set()

    client = Client(transport=Transport(), budget=Budget(names=()))
    client._public_lock = ObservedLock()
    gate.acquire()
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(client.market.offers, "123")
        try:
            assert waiting.wait(5), "public read did not reach the lock"
            closing = pool.submit(client.close)
            assert closed.wait(5), "close did not disable the client"
        finally:
            gate.release()
        closing.result(timeout=5)
        with pytest.raises(ConfigurationError, match="закрыт"):
            pending.result(timeout=5)
    assert client._closed
    assert client._public_fetcher is None
