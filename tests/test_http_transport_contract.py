"""Проверяет настоящий HTTPX-транспорт с потоковыми ответами MockTransport."""

from __future__ import annotations

import inspect
import json
from urllib.parse import parse_qs

import httpx
import pytest
from test_bot import NODE_ID, _thread_html

from funora import AsyncClient, Client, Secret
from funora._transport import AsyncFetcher, Fetcher
from funora.errors import TimeoutError as FunoraTimeoutError
from funora.send_outcome import SendOutcome


def request_args(method: str):
    if method == "ask":
        return ("/chat/history?node=1", {}), {}
    if method == "query":
        return ("/orders/gets", {"ids": ["A1"]}, {}), {}
    if method == "submit":
        return ("/runner/", {"content": "Привет & ="}, {}), {}
    return ("/file/addChatImage",), {
        "field": "file",
        "filename": "image.png",
        "content": b"image-bytes",
        "content_type": "image/png",
        "headers": {},
    }


async def complete(value):
    return await value if inspect.isawaitable(value) else value


def streamed_response(status: int, body: str, **kwargs) -> httpx.Response:
    # Готовое content= закрывает ответ до привязки таймера HTTPX. Поток проходит
    # тот же цикл чтения/закрытия, что и ответ сетевого транспорта.
    return httpx.Response(status, stream=httpx.ByteStream(body.encode()), **kwargs)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method", ["ask", "query", "submit", "upload"])
async def test_http_methods_keep_encoding_and_do_not_follow_write_redirects(
    asynchronous: bool, method: str
) -> None:
    received = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        assert request.url.host == "funpay.com"
        assert request.headers["cookie"] == "golden_key=synthetic-test-key"
        if method == "submit":
            assert parse_qs(request.content.decode())["content"] == ["Привет & ="]
        if method == "query":
            assert json.loads(request.content) == {"ids": ["A1"]}
        if method == "upload":
            assert b'name="file"' in request.content
            assert b"image-bytes" in request.content
        return streamed_response(302, "", headers={"location": "https://foreign.invalid/"})

    fetcher = (
        AsyncFetcher(Secret("synthetic-test-key"))
        if asynchronous
        else Fetcher(Secret("synthetic-test-key"))
    )
    await complete(fetcher.close())
    transport = httpx.MockTransport(handler)
    fetcher._client = (
        httpx.AsyncClient(transport=transport)
        if asynchronous
        else httpx.Client(transport=transport)
    )
    try:
        args, kwargs = request_args(method)
        observed = await complete(getattr(fetcher, method)(*args, **kwargs))
        assert observed.status == 302
        assert observed.requests_sent == 1
        assert len(received) == 1
    finally:
        await complete(fetcher.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method", ["ask", "query", "submit", "upload"])
async def test_http_timeouts_are_translated_without_repeating_requests(
    asynchronous: bool, method: str
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    fetcher = (
        AsyncFetcher(Secret("synthetic-test-key"))
        if asynchronous
        else Fetcher(Secret("synthetic-test-key"))
    )
    await complete(fetcher.close())
    transport = httpx.MockTransport(handler)
    fetcher._client = (
        httpx.AsyncClient(transport=transport)
        if asynchronous
        else httpx.Client(transport=transport)
    )
    try:
        args, kwargs = request_args(method)
        with pytest.raises(FunoraTimeoutError):
            await complete(getattr(fetcher, method)(*args, **kwargs))
        assert len(calls) == 1
    finally:
        await complete(fetcher.close())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("receipt", ["complete", "missing", "duplicate"])
async def test_image_from_public_client_to_http_and_back(
    asynchronous: bool, receipt: str, tmp_path
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return streamed_response(200, _thread_html())
        if request.url.path == "/file/addChatImage":
            return streamed_response(200, json.dumps({"fileId": 41}))
        fields = parse_qs(request.content.decode())
        data = json.loads(fields["request"][0])["data"]
        assert data["image_id"] == 41
        assert data["content"] == ""
        body = json.dumps(
            {
                "response": {"error": None},
                "objects": [
                    {
                        "type": "chat_node",
                        "data": {"node": {"name": data["node"]}, "messages": [{"id": 42}]},
                    }
                ],
            }
        )
        if receipt == "missing":
            body = body.replace('"error": null', '"another": null')
        elif receipt == "duplicate":
            body = body.replace('"error": null', '"error": "refused", "error": null')
        return streamed_response(200, body)

    fetcher = (
        AsyncFetcher(Secret("synthetic-test-key"))
        if asynchronous
        else Fetcher(Secret("synthetic-test-key"))
    )
    await complete(fetcher.close())
    transport = httpx.MockTransport(handler)
    fetcher._client = (
        httpx.AsyncClient(transport=transport)
        if asynchronous
        else httpx.Client(transport=transport)
    )
    cls = AsyncClient if asynchronous else Client
    client = cls(transport=fetcher, state_path=tmp_path / "state.json")
    try:
        result = await complete(
            client.chats.send_image(
                NODE_ID, b"image-bytes", filename="image.png", declared_cold=True
            )
        )
        if receipt == "complete":
            assert result.outcome is SendOutcome.CONFIRMED
            assert result.channel_message_id.value == 42
        else:
            assert result.outcome is SendOutcome.UNCONFIRMED
            assert result.reason == (
                "response_error_missing" if receipt == "missing" else "body_not_json"
            )
            assert not result.channel_message_id.is_observed
        assert len(requests) == 3
        assert [one.method for one in requests] == ["GET", "POST", "POST"]
    finally:
        await complete(client.close())
