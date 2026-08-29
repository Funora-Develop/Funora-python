"""Проверки асинхронного транспорта и его совпадения с синхронным.

Набор устроен не как «повторим те же проверки ещё раз». Он устроен как сверка:
одни и те же заготовленные ответы прогоняются через оба транспорта, и результаты
сравниваются между собой. Так проверяется не поведение каждого по отдельности, а
то единственное, ради чего решение о переходе вынесено в отдельный модуль, -
что они не разошлись.

Расхождение здесь стоит дороже любого другого. Обе дыры, найденные разбором,
были про то, что уходит в сеть: чужая cookie, оседающая в хранилище, и переход,
уносящий сессионный ключ на произвольный адрес. Написанное дважды правило
безопасности расходится молча, и цена расхождения - чужой доступ к аккаунту.

Сокет поднимается настоящий, на локальном адресе. Внешних соединений нет.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from test_transport import FakeServer, ok, redirect_to

from funora._secret import Secret
from funora._transport import AsyncFetcher, Fetcher, Observation, TransportSettings
from funora.errors import NetworkError


@pytest.fixture
def secret() -> Iterator[Secret]:
    """Возвращает секрет для проверок.

    Yields:
        Secret: Ненастоящий ключ.
    """
    yield Secret("MY-REAL-KEY", label="test")


def _shape(observation: Observation, port: int) -> tuple[object, ...]:
    """Сводит наблюдение к тому, что обязано совпасть у обоих транспортов.

    Длительность запроса сюда не входит намеренно: она разная по природе, и
    сравнивать её значило бы получить хрупкую проверку вместо содержательной.
    Номер порта вычищается по той же причине: сокеты у прогонов разные, и его
    совпадение ничего не сказало бы о совпадении транспортов.

    Args:
        observation (Observation): Наблюдение.
        port (int): Порт поднятого для этого прогона сервера.

    Returns:
        tuple[object, ...]: Сравнимая часть наблюдения.
    """
    return (
        observation.status,
        observation.final_url.replace(f"127.0.0.1:{port}", "127.0.0.1:ПОРТ"),
        observation.html,
        observation.redirects,
        observation.requests_sent,
        observation.content_length,
        observation.declared_length,
        observation.retry_after_ms,
    )


def _sync(
    responses: list[bytes], secret: Secret, path: str = "/a"
) -> tuple[Observation, FakeServer]:
    """Прогоняет заготовленные ответы через синхронный транспорт.

    Args:
        responses (list[bytes]): Ответы в порядке выдачи.
        secret (Secret): Секрет.
        path (str): Запрашиваемый путь.

    Returns:
        tuple[Observation, FakeServer]: Наблюдение и сервер с записанными
        запросами.
    """
    server = FakeServer(responses)
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            return fetcher.fetch(path), server
    finally:
        server.close()


async def _async(
    responses: list[bytes], secret: Secret, path: str = "/a"
) -> tuple[Observation, FakeServer]:
    """Прогоняет те же ответы через асинхронный транспорт.

    Args:
        responses (list[bytes]): Ответы в порядке выдачи.
        secret (Secret): Секрет.
        path (str): Запрашиваемый путь.

    Returns:
        tuple[Observation, FakeServer]: Наблюдение и сервер с записанными
        запросами.
    """
    server = FakeServer(responses)
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        async with AsyncFetcher(secret, settings=settings) as fetcher:
            return await fetcher.fetch(path), server
    finally:
        server.close()


async def test_plain_answer_matches(secret: Secret) -> None:
    """Проверяет совпадение на обычном ответе.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    a, sa = _sync([ok()], secret)
    b, sb = await _async([ok()], secret)
    assert _shape(a, sa.port) == _shape(b, sb.port)


async def test_redirect_chain_matches(secret: Secret) -> None:
    """Проверяет совпадение на цепочке разрешённых переходов.

    Считается не только тело, но и число отправленных запросов: именно по нему
    расходуется бюджет, и разойдись транспорты здесь - один из них тратил бы
    больше другого при одинаковой нагрузке на площадку.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    script = [redirect_to("/b"), redirect_to("/c"), ok()]
    a, sa = _sync(list(script), secret)
    b, sb = await _async(list(script), secret)
    assert _shape(a, sa.port) == _shape(b, sb.port)
    assert a.requests_sent == 3
    assert len(sa.requests) == len(sb.requests) == 3


async def test_foreign_redirect_matches_and_carries_no_secret(secret: Secret) -> None:
    """Проверяет, что оба транспорта одинаково отказываются уйти на чужой хост.

    Главная проверка набора. Дыра стоила аккаунта целиком, и повторять её в
    асинхронной ветке было бы худшим способом добавить асинхронность.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    script = [redirect_to("https://evil.example/steal"), ok()]
    a, sa = _sync(list(script), secret)
    b, sb = await _async(list(script), secret)

    assert _shape(a, sa.port) == _shape(b, sb.port)
    assert a.final_url == "https://evil.example/steal"
    # Запрос ушёл ровно один - на площадку. Второй заготовленный ответ никто не
    # забрал: до чужого адреса дело не дошло.
    assert len(sa.requests) == len(sb.requests) == 1


async def test_response_cookie_does_not_stick_in_async(secret: Secret) -> None:
    """Проверяет, что присланная площадкой cookie не оседает и в асинхронном.

    Вторая дыра разбора. С включённым хранилищем клиент молча читал чужой
    аккаунт как свой: ни исключения, ни повреждений, ни строки в журнале.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([ok(b"Set-Cookie: golden_key=ATTACKER; Path=/\r\n"), ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        async with AsyncFetcher(secret, settings=settings) as fetcher:
            await fetcher.fetch("/a")
            await fetcher.fetch("/b")
    finally:
        server.close()

    for header in server.cookie_headers():
        assert "ATTACKER" not in header, f"чужая cookie ушла обратно: {header}"
        assert "MY-REAL-KEY" in header


async def test_network_failure_is_translated(secret: Secret) -> None:
    """Проверяет, что сетевой отказ переводится в иерархию Funora.

    Без перевода обработчик, ловящий FunoraError, пропускал бы обрыв связи мимо
    себя, и цикл наблюдения падал бы целиком вместо повтора.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([])
    port = server.port
    server.close()

    settings = TransportSettings(base_url=f"http://127.0.0.1:{port}")
    async with AsyncFetcher(secret, settings=settings) as fetcher:
        with pytest.raises(NetworkError):
            await fetcher.fetch("/a")


async def test_a_write_matches_and_is_never_replayed_by_either(secret: Secret) -> None:
    """Требует, чтобы оба транспорта отправляли одинаково и не повторяли записи.

    Правило безопасности, написанное дважды, расходится молча. Здесь цена
    расхождения - второе сообщение покупателю: у отправленного сообщения нет
    отмены, а переход в ответ на запись выглядит как обычное приглашение
    повторить запрос по другому адресу.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    fields = {"request": "false", "csrf_token": "тк"}
    headers = {"X-Requested-With": "XMLHttpRequest"}

    sync_server = FakeServer([redirect_to("/somewhere-else"), ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{sync_server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            a = fetcher.submit("/runner/", fields, headers)
    finally:
        sync_server.close()

    async_server = FakeServer([redirect_to("/somewhere-else"), ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{async_server.port}")
        async with AsyncFetcher(secret, settings=settings) as fetcher:
            b = await fetcher.submit("/runner/", fields, headers)
    finally:
        async_server.close()

    assert _shape(a, sync_server.port) == _shape(b, async_server.port)
    assert len(sync_server.requests) == len(async_server.requests) == 1, (
        "один из транспортов повторил отправку по переходу"
    )

    # Тела запросов обязаны совпасть посимвольно: порядок полей формы - тоже
    # часть того, что уходит в сеть.
    sent = [one.requests[0].split("\r\n\r\n", 1)[1] for one in (sync_server, async_server)]
    assert sent[0] == sent[1], f"транспорты отправили разное: {sent}"
