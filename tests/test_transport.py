"""Проверки транспорта на живом сокете.

Набор появился после разбора, нашедшего здесь две дыры, каждая из которых
стоила аккаунта продавца целиком. Обе выглядели работающими и не ловились ни
одним из трёхсот с лишним тестов: они были про то, что уходит в сеть, а сеть в
наборе не участвовала.

Поэтому здесь поднимается настоящий сокет на локальном адресе. Внешних
соединений нет, порт выбирает операционная система, ответы заготовлены заранее.
Проверяется ровно одно: что именно клиент отправляет и куда.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from funora._secret import Secret
from funora._transport import Fetcher, TransportSettings
from funora.errors import RemoteServerError

#: Тело, которым отвечает подставной сервер.
BODY = b"<html><body>ok</body></html>"


class FakeServer:
    """Сервер, отдающий заготовленные ответы и запоминающий запросы.

    Args:
        responses (list[bytes]): Ответы в порядке выдачи.
    """

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = list(responses)
        self.requests: list[str] = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port: int = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Обслуживает заготовленное число запросов.

        Returns:
            None
        """
        for response in self._responses:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            self.requests.append(data.decode("latin-1"))
            conn.sendall(response)
            conn.close()

    def cookie_headers(self) -> list[str]:
        """Возвращает заголовок Cookie каждого пришедшего запроса.

        Returns:
            list[str]: Значения заголовка, пустая строка при его отсутствии.
        """
        found: list[str] = []
        for head in self.requests:
            lines = [x for x in head.split("\r\n") if x.lower().startswith("cookie:")]
            found.append(lines[0] if lines else "")
        return found

    def close(self) -> None:
        """Закрывает слушающий сокет.

        Returns:
            None
        """
        self._sock.close()


def ok(extra: bytes = b"") -> bytes:
    """Собирает успешный ответ.

    Args:
        extra (bytes): Дополнительные заголовки.

    Returns:
        bytes: Готовый ответ.
    """
    return (
        b"HTTP/1.1 200 OK\r\nContent-Length: "
        + str(len(BODY)).encode()
        + b"\r\nConnection: close\r\n"
        + extra
        + b"\r\n"
        + BODY
    )


def redirect_to(location: str) -> bytes:
    """Собирает ответ-перенаправление.

    Args:
        location (str): Значение заголовка Location.

    Returns:
        bytes: Готовый ответ.
    """
    return (
        b"HTTP/1.1 302 Found\r\nLocation: "
        + location.encode()
        + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )


@pytest.fixture
def secret() -> Iterator[Secret]:
    """Возвращает секрет для проверок.

    Yields:
        Secret: Ненастоящий ключ.
    """
    yield Secret("MY-REAL-KEY", label="test")


def test_response_cookies_do_not_stick(secret: Secret) -> None:
    """Проверяет, что присланная площадкой cookie не оседает.

    Дыра, найденная разбором. С включённым хранилищем cookie один заголовок
    Set-Cookie подкладывал чужой golden_key, и следующий запрос уходил с двумя
    значениями, где присланное стояло первым. Сервер читает первое вхождение, и
    клиент молча читал чужой аккаунт как свой: ни исключения, ни повреждений, ни
    строки в журнале - правдоподобные данные не того аккаунта.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([ok(b"Set-Cookie: golden_key=ATTACKER; Path=/\r\n"), ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            fetcher.fetch("/a")
            fetcher.fetch("/b")
    finally:
        server.close()

    for header in server.cookie_headers():
        assert "ATTACKER" not in header, f"чужая cookie ушла обратно: {header}"
        assert "MY-REAL-KEY" in header


def test_redirect_to_a_foreign_host_does_not_carry_the_secret(secret: Secret) -> None:
    """Проверяет, что переход на чужой хост не уносит секрет.

    Дыра, стоившая аккаунта целиком: одного заголовка Location хватало, чтобы
    сессионный ключ ушёл на произвольный адрес. Проверка после отправки была бы
    бесполезна - секрет уже ушёл бы.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    thief = FakeServer([ok()])
    # localhost и 127.0.0.1 ведут в одно место, но это разные имена хостов, и
    # правило обязано их различать.
    home = FakeServer([redirect_to(f"http://localhost:{thief.port}/steal")])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{home.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            observation = fetcher.fetch("/orders/trade")
    finally:
        home.close()
        thief.close()

    assert not thief.requests, "запрос ушёл на чужой хост"
    assert "localhost" in observation.final_url, (
        "отклонённый адрес обязан стать конечным, иначе классификатор скажет "
        "«разметка изменилась» вместо «нас пытались увести»"
    )


def test_same_host_redirect_is_followed(secret: Secret) -> None:
    """Проверяет, что переход внутри площадки выполняется.

    Защита обязана отсекать чужое, а не всё подряд: площадка уводит на страницу
    входа именно переходом, и не пойти за ним значило бы не узнать об истёкшей
    сессии.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([redirect_to("/account/login"), ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            observation = fetcher.fetch("/orders/trade")
    finally:
        server.close()

    assert observation.redirects == 1
    assert observation.status == 200
    assert "/account/login" in observation.final_url


def test_scheme_downgrade_is_refused(secret: Secret) -> None:
    """Проверяет отказ от понижения схемы.

    Переход с https на http отдал бы секрет открытым текстом любому, кто видит
    трафик, и заметить это по поведению клиента невозможно.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    from funora._host import is_safe_hop

    assert not is_safe_hop("https://funpay.com/orders", "http://funpay.com/orders", "funpay.com")
    assert is_safe_hop("https://funpay.com/a", "https://funpay.com/b", "funpay.com")


def test_oversized_body_raises_a_funora_error(secret: Secret) -> None:
    """Проверяет, что слишком большой ответ даёт ошибку из иерархии.

    Раньше здесь поднимался ValueError, то есть исключение вне иерархии Funora.
    Обработчик, ловящий FunoraError, его не поймал бы, и цикл наблюдения упал бы
    целиком вместо того, чтобы отметить отказ и продолжить.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([ok()])
    try:
        settings = TransportSettings(
            base_url=f"http://127.0.0.1:{server.port}", max_response_bytes=4
        )
        with Fetcher(secret, settings=settings) as fetcher, pytest.raises(RemoteServerError):
            fetcher.fetch("/a")
    finally:
        server.close()


def test_secret_is_sent_once_per_request(secret: Secret) -> None:
    """Проверяет, что заголовок Cookie содержит ровно одно значение.

    Дубль в заголовке - это и есть та дыра, через которую подкладывался чужой
    ключ.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            fetcher.fetch("/a")
    finally:
        server.close()

    header = server.cookie_headers()[0]
    assert header.count("golden_key=") == 1, f"ключ передан не один раз: {header}"
