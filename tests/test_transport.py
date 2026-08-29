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
from dataclasses import replace

import pytest

from funora._secret import Secret
from funora._transport import Fetcher, Observation, TransportSettings
from funora.errors import FunoraError, NetworkError, RemoteServerError

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
            # Тело дочитывается по объявленной длине. Без этого проверка
            # отправки смотрела бы только на заголовки, а весь смысл её - в том,
            # ЧТО ушло в теле.
            separator = b"\r\n\r\n"
            head, _, rest = data.partition(separator)
            length = 0
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            while len(rest) < length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                rest += chunk
            self.requests.append((head + separator + rest).decode("latin-1"))
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


def test_network_failure_becomes_a_funora_error(secret: Secret) -> None:
    """Проверяет, что сетевой отказ попадает в иерархию Funora.

    Раньше наружу уходило исключение стека. Обработчик, ловящий FunoraError,
    пропускал бы его мимо себя, и цикл наблюдения падал бы целиком вместо
    повтора - при том, что политика повторов для сетевых отказов написана и
    покрыта тестами.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    # Порт, на котором никто не слушает: сокет открыт и сразу закрыт.
    idle = socket.socket()
    idle.bind(("127.0.0.1", 0))
    port = idle.getsockname()[1]
    idle.close()

    settings = TransportSettings(base_url=f"http://127.0.0.1:{port}")
    with Fetcher(secret, settings=settings) as fetcher, pytest.raises(NetworkError) as exc:
        fetcher.fetch("/orders/trade")

    assert isinstance(exc.value, FunoraError)
    assert NetworkError.retryable, "сетевой отказ обязан быть повторяемым"


def test_compression_is_asked_off() -> None:
    """Проверяет, что клиент просит сервер не сжимать ответ.

    Это не про экономию, а про единственную защиту от обрыва тела. Библиотека
    распаковывает прозрачно, а Content-Length объявляет длину СЖАТОГО тела:
    проверка целостности сравнивала распакованную длину с объявленной сжатой -
    двести тысяч байт против двухсот пятидесяти - и проходила всегда, в том
    числе на оборванном ответе.

    Returns:
        None
    """
    from funora._transport import _client_kwargs

    headers = _client_kwargs(TransportSettings())["headers"]
    assert headers.get("Accept-Encoding") == "identity", (
        "сжатие не отключено - проверка целостности снова мертва"
    )


def test_integrity_is_unverified_when_there_is_nothing_to_compare() -> None:
    """Проверяет, что непроверяемая целостность объявляется непроверенной.

    Прежде здесь проверялась check_integrity - функция, которую не вызывал
    НИКТО. Сравнение длин живёт в классификаторе, и она была мёртвой копией его
    правила: проверка держала копию, копия создавала впечатление работающей
    защиты, а живой путь тем временем шёл мимо.

    Настоящая дыра была не в сравнении, а в том, что происходит, когда сравнить
    нечем. При chunked-передаче объявленной длины нет вовсе, при сжатии
    объявлена длина сжатого тела против полученной распакованной. В обоих
    случаях классификатор предупреждает и пропускает, а разбор объявлял чтение
    полным - то есть выдавал незнание за знание.

    Returns:
        None
    """
    from funora._engine import integrity_verified

    whole = Observation(
        status=200,
        final_url="https://funpay.com/orders/trade",
        html="<html></html>",
        elapsed_ms=1,
        redirects=0,
        content_length=100,
        declared_length=100,
    )
    assert integrity_verified(whole), "целое тело с объявленной длиной подтверждается"

    # Сжатое: объявлена длина сжатого, получена длина распакованного. Сравнение
    # бессмысленно, и подтверждать нечего.
    # Числа взяты как в жизни: объявлено 250 байт сжатого, получено 200 000
    # распакованного. Полученное БОЛЬШЕ объявленного, и сравнение «получено не
    # меньше объявленного» проходит - на оборванном ответе тоже. Первая редакция
    # этой проверки брала обратные числа и не различала снятие защиты от сжатия:
    # мутация «убрать проверку кодировки» проходила молча.
    packed = replace(whole, content_encoding="gzip", content_length=200_000, declared_length=250)
    assert not integrity_verified(packed), (
        "сжатый ответ объявлен целым по длине. Объявлена длина сжатого тела, "
        "получена длина распакованного: сравнение проходит всегда, включая "
        "оборванный ответ"
    )

    # Chunked: длины нет вовсе.
    chunked = replace(whole, declared_length=None)
    assert not integrity_verified(chunked), (
        "ответ без объявленной длины объявлен целым. Сравнивать было нечем, и "
        "это незнание, а не подтверждение"
    )

    # Тело короче объявленного: обрыв ловит классификатор, а здесь он тоже не
    # подтверждается - функция говорит только «подтверждено ли», не «цело ли».
    short = replace(whole, content_length=10)
    assert not integrity_verified(short)


def test_unverified_read_is_not_called_complete() -> None:
    """Проверяет, что непроверенное чтение не объявляется полным.

    Замер, ради которого проверка и написана: из двух тысяч обрывов снимка
    списка продаж в случайной точке 128 давали completeness=complete с числом
    строк меньше настоящего. Курсор снимается с полного чтения, недостающие
    заказы уходят из него и при следующем целом чтении приходят заново как
    order.created - бот, выдающий товар по этому событию, выдаёт его повторно.

    Returns:
        None
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from funora import Completeness
    from funora._engine import unverified
    from funora._orders import parse_orders_page

    html = (
        Path(__file__).parent / "fixtures" / "pages" / "orders-trade.logged.ru.skeleton.txt"
    ).read_text(encoding="utf-8")
    page = parse_orders_page(html, observed_at=datetime(2026, 8, 24, tzinfo=UTC))
    assert page.completeness is Completeness.COMPLETE, "снимок обязан читаться полностью"

    lowered = unverified(page)
    assert lowered.completeness is Completeness.PARTIAL, (
        "чтение с неподтверждённой целостностью осталось полным. С полного "
        "снимается курсор, и оборванная страница уводит из него настоящие заказы"
    )
    assert lowered.reason == "integrity_unverified"
    assert any(one.code == "integrity_unverified" for one in lowered.defects), (
        "понижение не оставило следа в повреждениях: вызывающий не узнает, почему"
    )
    assert lowered.rows_accepted == page.rows_accepted, (
        "понижение выбросило строки. Прочитанное остаётся доступным через "
        "rows(accept_incomplete=True) - меняется только доверие к полноте"
    )

    # Уже неполное чтение понижать нечего: причина остаётся своей.
    assert unverified(lowered) is lowered


def test_decompressed_body_has_its_own_limit() -> None:
    """Проверяет, что у распакованного тела свой предел.

    Сжатый ответ в мегабайт разворачивается в сотни. Один предел на двоих ловит
    только тот случай, который и так виден: тело, большое ещё до распаковки.

    Returns:
        None
    """
    from funora.budget import MAX_DECOMPRESSED_BYTES, MAX_RESPONSE_BYTES

    assert MAX_DECOMPRESSED_BYTES > MAX_RESPONSE_BYTES, (
        "предел распакованного не больше предела полученного - тогда он ничего не добавляет"
    )
    settings = TransportSettings()
    assert settings.max_decompressed_bytes == MAX_DECOMPRESSED_BYTES
    assert settings.max_response_bytes == MAX_RESPONSE_BYTES


def test_transport_limits_come_from_the_spec() -> None:
    """Проверяет, что пределы транспорта не литералы.

    Прежде они были числами «по мотивам» спецификации, и её правка меняла
    порождённый файл, не меняя поведения: предел переходов, размера ответа и
    числа соединений оставался прежним. Молча.

    Returns:
        None
    """
    from funora.budget import MAX_CONNECTIONS_PER_HOST, MAX_REDIRECTS

    settings = TransportSettings()
    assert settings.max_redirects == MAX_REDIRECTS
    assert settings.max_connections == MAX_CONNECTIONS_PER_HOST


def test_accepted_languages_come_from_the_spec() -> None:
    """Проверяет, что заголовок языков собран из перечня спецификации.

    Локаль привязана к аккаунту и запросом не переключается, но заголовок обязан
    называть ровно те языки, для которых у проекта есть снимки: попросив язык
    без снимков, клиент получил бы страницу, которую не умеет разбирать, и
    объявил бы это изменением вёрстки.

    Прежде заголовок был литералом и совпадал с перечнем по совпадению.

    Returns:
        None
    """
    from funora._transport import _client_kwargs
    from funora.contract import SUPPORTED_LOCALES

    header = _client_kwargs(TransportSettings())["headers"]["Accept-Language"]
    named = [part.split(";")[0] for part in header.split(",")]

    assert named == list(SUPPORTED_LOCALES), (
        f"заголовок называет {named}, спецификация объявляет {list(SUPPORTED_LOCALES)}"
    )
    assert header.startswith(SUPPORTED_LOCALES[0]), (
        "первый язык перечня обязан идти без веса: так выражается предпочтение"
    )


def test_accepted_languages_follow_the_spec_when_it_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что заголовок вправду выводится из перечня.

    Проверка выше сравнивает заголовок с перечнем и на сегодняшних данных
    проходит даже у литерала: выведенное значение совпадает с тем, что стояло
    строкой до правки, знак в знак. Совпадение это случайное, и полагаться на
    него нельзя.

    Здесь перечень подменяется, и заголовок обязан поменяться следом. Литерал
    так не умеет.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    import funora._transport as transport_module

    monkeypatch.setattr(transport_module, "SUPPORTED_LOCALES", ("de", "fr", "es"))
    header = transport_module._client_kwargs(TransportSettings())["headers"]["Accept-Language"]

    assert header.startswith("de"), f"перечень сменился, а заголовок остался: {header}"
    assert "fr" in header and "es" in header
    assert "ru" not in header, "заголовок держит язык, которого нет в перечне"


def test_a_submitted_form_goes_out_as_a_post_with_its_fields(secret: Secret) -> None:
    """Требует, чтобы отправка ушла методом POST с полями в теле.

    Проверка смотрит на ПРОВОД, а не на возвращённое значение. Ошибка здесь -
    например, поля, уехавшие строкой запроса вместо тела, - выглядела бы изнутри
    работающей: объект собран, вызов состоялся, ответ получен.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            fetcher.submit(
                "/runner/",
                {"request": '{"action": "проба"}', "csrf_token": "тк"},
                {"X-Requested-With": "XMLHttpRequest"},
            )
    finally:
        server.close()

    assert len(server.requests) == 1, "запросов ушло не один"
    sent = server.requests[0]
    assert sent.startswith("POST /runner/ "), sent.split("\r\n")[0]
    assert "x-requested-with: xmlhttprequest" in sent.lower(), "заголовок канала не ушёл"

    body = sent.split("\r\n\r\n", 1)[1]
    assert "csrf_token=" in body, "защитного поля нет в теле"
    assert "action" in body, "поля действия нет в теле"
    assert "?" not in sent.split(" ")[1], "поля уехали строкой запроса, а не телом"


def test_the_secret_rides_the_write_the_same_way_it_rides_a_read(secret: Secret) -> None:
    """Требует, чтобы секрет уходил заголовком Cookie и только им.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            fetcher.submit("/runner/", {"request": "false"}, {})
    finally:
        server.close()

    sent = server.requests[0]
    assert secret.reveal() in server.cookie_headers()[0], "секрет не уехал заголовком"

    body = sent.split("\r\n\r\n", 1)[1]
    assert secret.reveal() not in body, "секрет оказался в теле запроса"
    assert secret.reveal() not in sent.split(" ")[1], "секрет оказался в адресе"


def test_a_redirect_on_a_write_is_never_replayed(secret: Secret) -> None:
    """Требует НЕ повторять отправку по переходу.

    Чтение по переходу повторить безвредно, запись - нет. У отправленного
    сообщения нет отмены, и повтор при неоднозначном исходе означает второе
    сообщение покупателю.

    Заготовлено два ответа, а уйти обязан один запрос: второй ответ здесь
    затем, чтобы повтор было ВИДНО. Сервер, ожидающий одного запроса, на
    повторе просто повис бы, и проверка упала бы по сроку, не назвав причины.

    Args:
        secret (Secret): Секрет.

    Returns:
        None
    """
    server = FakeServer([redirect_to("/somewhere-else"), ok()])
    try:
        settings = TransportSettings(base_url=f"http://127.0.0.1:{server.port}")
        with Fetcher(secret, settings=settings) as fetcher:
            seen = fetcher.submit("/runner/", {"request": "false"}, {})
    finally:
        server.close()

    assert len(server.requests) == 1, (
        f"по переходу ушёл второй запрос: отправка повторена {len(server.requests)} раза"
    )
    assert seen.status == 302, "переход подменён чем-то другим"
    assert seen.redirects == 0
    assert seen.requests_sent == 1, "бюджету списано больше запросов, чем ушло"
