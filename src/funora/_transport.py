"""Транспортный слой наблюдения.

Слой намеренно тонкий: задача - не построить клиент, а один раз аккуратно
сходить на страницу и не оставить следов, которых не должно быть.

Что здесь сделано осознанно:

  * Секрет не хранится в объекте клиента и не кладётся в cookie jar. Он
    разворачивается в момент сборки запроса и живёт ровно до его отправки.
    Cookie jar - это структура, которую легко напечатать целиком при отладке.
  * Клиент не следует за переходами автоматически: конечный URL нужен
    классификатору, а автоматический переход прячет тот факт, что нас увели на
    страницу входа.
  * Переход на чужой хост не выполняется вовсе. Разбор нашёл здесь дыру, стоящую
    аккаунта целиком: одного заголовка Location хватало, чтобы секрет ушёл на
    произвольный адрес. Проверка после отправки бесполезна - секрет уже ушёл бы.
  * Хранилище cookie отключено. С включённым площадка одним Set-Cookie
    подкладывала свой golden_key, и он уходил следующим запросом впереди
    настоящего: сервер читает первое вхождение, и клиент молча читал чужой
    аккаунт как свой.
  * При запуске проверяется уровень журналирования HTTP-стека. httpx на уровне
    DEBUG печатает заголовки, то есть и сессионный ключ. Предупреждение выдаётся
    один раз: молча работать в таком режиме нельзя, а падать - слишком.
  * Числа таймаутов и пределов взяты из spec/runtime/budget.yaml и помечены там
    как провизорные.

Транспортов два - синхронный и асинхронный, - но решение о переходе у них одно
на двоих и живёт в [_hops.py]. Разъехаться правилу безопасности здесь дороже,
чем любому другому: цена расхождения - чужой доступ к аккаунту.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin

import httpx

from ._hops import Follow, Reject, next_hop
from ._host import host_of
from ._secret import Secret
from .budget import (
    MAX_CONNECTIONS_PER_HOST,
    MAX_DECOMPRESSED_BYTES,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
)
from .contract import SUPPORTED_LOCALES
from .errors import ConfigurationError, NetworkError, RemoteServerError, TimeoutError

__all__ = ["Observation", "Fetcher", "AsyncFetcher", "TransportSettings"]

_log = logging.getLogger("funora.transport")

#: Журналы, которые печатают заголовки запроса на уровне DEBUG.
_RISKY_LOGGERS: Final[tuple[str, ...]] = ("httpx", "httpcore", "urllib3")

#: Флаг, чтобы предупреждение о журналировании выдавалось один раз за процесс.
_warned = False


@dataclass(frozen=True, slots=True)
class TransportSettings:
    """Настройки транспорта.

    Числа берутся из порождённого модуля budget, а не пишутся здесь. Прежде они
    были литералами «по мотивам» спецификации, и правка спецификации меняла
    порождённый файл, не меняя поведения: предел переходов, размера ответа и
    числа соединений оставался прежним. Молча.

    В спецификации числа помечены провизорными и будут уточнены по результатам
    наблюдений - тем важнее, чтобы уточнение доходило до транспорта само.

    Args:
        base_url (str): Базовый адрес площадки.
        connect_timeout_s (float): Предел на установку соединения, секунды.
        read_timeout_s (float): Предел на чтение ответа, секунды.
        max_connections (int): Предел одновременных соединений на хост.
        max_response_bytes (int): Предел размера полученного тела, байты.
        max_decompressed_bytes (int): Предел размера тела после распаковки,
            байты. Отдельный предел, а не тот же самый: сжатый ответ в мегабайт
            разворачивается в сотни, и одна проверка на двоих ловит только тот
            случай, который и так виден.
        max_redirects (int): Предел числа переходов при ручном следовании.
        proxy_url (str | None): Через что ходить. None означает прямое
            соединение. Прокси меняет исходящий адрес, то есть сетевую
            идентичность целиком: у неё свой запас токенов и своё остывание
            после ограничения частоты.
        user_agent (str): Значение заголовка User-Agent. Задаётся спецификацией,
            а не оставляется на усмотрение реализации: одинаковое поведение
            шести SDK начинается с того, как они представляются.
    """

    base_url: str = "https://funpay.com"
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 20.0
    max_connections: int = MAX_CONNECTIONS_PER_HOST
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES
    max_redirects: int = MAX_REDIRECTS
    proxy_url: str | None = None
    user_agent: str = "Funora/0.0.1 (+https://github.com/Funora-Develop)"


@dataclass(frozen=True, slots=True)
class Observation:
    """Результат одного обращения к странице.

    Тело сохраняется, потому что дальше из него строится структурный скелет.
    В диагностику и в файлы попадает только скелет, но не это поле.

    Args:
        status (int): Код состояния HTTP.
        final_url (str): URL после всех переходов.
        html (str): Тело ответа.
        elapsed_ms (int): Длительность запроса, миллисекунды.
        redirects (int): Сколько переходов было выполнено.
        content_length (int): Размер полученного тела в байтах.
        content_encoding (str): Кодирование тела, как его объявил сервер. Пустая
            строка означает, что сервер послушался просьбы не сжимать, и длину
            можно сравнивать с объявленной.
        declared_length (int | None): Длина, объявленная заголовком
            Content-Length, если он был. Нужна для проверки целостности:
            страница, оборванная посреди таблицы, проходит и классификацию, и
            разбор, а вызывающий получает половину заказов с нулём повреждений.
            Это правдоподобный неверный ответ, о неверности которого узнать
            неоткуда, и сверка длин - единственный способ его заметить.
        retry_after_ms (int | None): Значение заголовка Retry-After в
            миллисекундах, если площадка его прислала.
        requests_sent (int): Сколько запросов ушло на самом деле, вместе с
            переходами. Расходуется бюджет именно по этому числу: спецификация
            требует считать отправленные запросы, а не логические операции, и
            переход - тоже запрос.
    """

    status: int
    final_url: str
    html: str
    elapsed_ms: int
    redirects: int
    content_length: int
    content_encoding: str = ""
    declared_length: int | None = None
    retry_after_ms: int | None = None
    requests_sent: int = 1


def _warn_if_headers_logged() -> None:
    """Предупреждает, если HTTP-стек печатает заголовки в журнал.

    На уровне DEBUG httpx выводит заголовки запроса, среди которых Cookie с
    сессионным ключом. Граница защиты секрета проходит по краю этого проекта, и
    об этом лучше сказать вслух один раз, чем не сказать вовсе.

    Returns:
        None: Побочный эффект - запись в журнал.
    """
    global _warned
    if _warned:
        return
    for name in _RISKY_LOGGERS:
        logger = logging.getLogger(name)
        if logger.isEnabledFor(logging.DEBUG):
            _log.warning(
                "журнал %s включён на уровне DEBUG: он печатает заголовки запроса, "
                "включая сессионный ключ. Funora не может этому помешать",
                name,
            )
            _warned = True
            return
    _warned = True


def _accept_language() -> str:
    """Собирает заголовок предпочитаемых языков из перечня спецификации.

    Первый язык перечня просится без веса, остальные - с убывающим: так принято
    в HTTP, и так площадка поймёт порядок предпочтения.

    Returns:
        str: Значение заголовка Accept-Language.
    """
    if not SUPPORTED_LOCALES:
        raise ConfigurationError(
            "перечень поддерживаемых локалей пуст: клиент не может назвать язык, "
            "для которого у него есть снимки страниц"
        )
    head, *rest = SUPPORTED_LOCALES
    parts = [head]
    for index, locale in enumerate(rest, start=1):
        parts.append(f"{locale};q={max(0.1, 1.0 - index * 0.2):.1f}")
    return ",".join(parts)


def _client_kwargs(settings: TransportSettings) -> dict[str, object]:
    """Собирает одинаковые для обоих транспортов настройки httpx.

    Общая функция здесь не ради краткости. Из перечисленных настроек две -
    отключённое хранилище cookie и отключённое следование за переходами -
    держат обе найденные разбором дыры закрытыми. Заданные по отдельности, они
    расходятся при первой же правке, и расхождение это молчаливое.

    Args:
        settings (TransportSettings): Настройки транспорта.

    Returns:
        dict[str, object]: Аргументы конструктора клиента httpx.
    """
    return {
        "timeout": httpx.Timeout(
            connect=settings.connect_timeout_s,
            read=settings.read_timeout_s,
            write=settings.read_timeout_s,
            pool=settings.connect_timeout_s,
        ),
        "limits": httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_connections,
        ),
        "follow_redirects": False,
        # Хранилище cookie отключено намеренно. С включённым площадка одним
        # заголовком Set-Cookie подкладывала свой golden_key, и он уходил
        # следующим запросом ВПЕРЕДИ настоящего: сервер читает первое вхождение,
        # и клиент молча читал чужой аккаунт как свой. Ни исключения, ни
        # повреждений, ни строки в журнале - правдоподобные данные не того
        # аккаунта. Заголовок Cookie собирается вручную.
        "cookies": None,
        # Прокси передаётся библиотеке как есть. Проверку схемы делает пул: там
        # же, где прокси объявляются, - иначе она обошлась бы передачей готового
        # транспорта.
        "proxy": settings.proxy_url,
        "headers": {
            "User-Agent": settings.user_agent,
            # Перечень берётся из спецификации, а не пишется здесь. Локаль
            # привязана к аккаунту и запросом не переключается, но заголовок
            # обязан называть ровно те языки, для которых у проекта есть
            # снимки: попросив язык без снимков, клиент получил бы страницу,
            # которую не умеет разбирать, и объявил бы это изменением вёрстки.
            "Accept-Language": _accept_language(),
            # Сжатие запрашивается отключённым, и это не про экономию, а про
            # единственную защиту от обрыва тела.
            #
            # Библиотека распаковывает ответ прозрачно, а заголовок
            # Content-Length объявляет длину СЖАТОГО тела. Проверка целостности
            # сравнивала распакованную длину с объявленной сжатой - двести тысяч
            # байт против двухсот пятидесяти, - и проходила всегда, в том числе
            # на оборванном ответе. Проверка была мертва ровно там, где нужна.
            #
            # Распаковка обрыв тоже не ловит: оборванный gzip разворачивается
            # частично и без ошибки. Проверено - половина потока дала 88 тысяч
            # байт правдоподобного текста.
            #
            # Цена - трафик. Запросов от этого больше не становится, а страница,
            # оборванная посреди таблицы, проходит и классификацию как
            # пригодную, и разбор как полный: вызывающий получает половину
            # заказов с нулём повреждений.
            "Accept-Encoding": "identity",
        },
    }


def _translate(exc: httpx.HTTPError, path: str) -> Exception:
    """Переводит отказ HTTP-стека в иерархию ошибок Funora.

    Перевод делается здесь, а не у вызывающего. Иначе обработчик, ловящий
    FunoraError, пропускал бы обрыв связи мимо себя, и цикл наблюдения падал бы
    целиком вместо повтора - при том, что политика повторов для сетевых отказов
    написана и покрыта тестами.

    Args:
        exc (httpx.HTTPError): Исходный отказ.
        path (str): Путь, по которому шло обращение. Нужен для сообщения.

    Returns:
        Exception: TimeoutError при истечении предела ожидания, иначе
        NetworkError.
    """
    if isinstance(exc, httpx.TimeoutException):
        return TimeoutError(f"истёк предел ожидания при обращении к {path}")
    return NetworkError(f"сетевой отказ при обращении к {path}: {type(exc).__name__}")


def _observe(
    response: httpx.Response,
    *,
    settings: TransportSettings,
    rejected_url: str | None,
    redirects: int,
    sent: int,
    elapsed: float,
) -> Observation:
    """Собирает наблюдение из ответа.

    Args:
        response (httpx.Response): Последний полученный ответ.
        settings (TransportSettings): Настройки транспорта.
        rejected_url (str | None): Адрес отвергнутого перехода либо None.
        redirects (int): Число выполненных переходов.
        sent (int): Число отправленных запросов.
        elapsed (float): Суммарная длительность запросов, секунды.

    Returns:
        Observation: Наблюдение.

    Raises:
        RemoteServerError: Если ответ превысил предел размера.
    """
    raw = response.content
    # Два предела, а не один. Полученное тело меряется одним, распакованное -
    # другим: сжатый ответ в мегабайт разворачивается в сотни, и одна проверка
    # на двоих ловит только тот случай, который и так виден.
    encoded = (response.headers.get("content-encoding") or "").strip().lower()
    limit = (
        settings.max_decompressed_bytes
        if encoded not in ("", "identity")
        else settings.max_response_bytes
    )
    if len(raw) > limit:
        raise RemoteServerError(f"ответ превысил предел {limit} байт: получено {len(raw)}")

    return Observation(
        status=response.status_code,
        final_url=rejected_url or str(response.url),
        html=response.text,
        elapsed_ms=int(elapsed * 1000),
        redirects=redirects,
        requests_sent=sent,
        content_length=len(raw),
        content_encoding=(response.headers.get("content-encoding") or "").strip().lower(),
        declared_length=_header_int(response, "content-length"),
        retry_after_ms=_retry_after_ms(response),
    )


def _log_rejected(target: str, expected: str) -> None:
    """Пишет в журнал об отвергнутом переходе.

    Args:
        target (str): Адрес, куда нас пытались увести.
        expected (str): Ожидаемый хост площадки.

    Returns:
        None: Побочный эффект - запись в журнал.
    """
    _log.warning(
        "переход отклонён: %s не принадлежит %s либо понижает схему",
        host_of(target) or "адрес без хоста",
        expected,
    )


class Fetcher:
    """Выполняет одиночные запросы к площадке.

    Args:
        secret (Secret): Сессионный секрет. Разворачивается только в момент
            сборки запроса.
        cookie_name (str): Имя cookie, в которой передаётся секрет.
        settings (TransportSettings): Настройки транспорта.
    """

    __slots__ = ("_client", "_cookie_name", "_secret", "_settings")

    def __init__(
        self,
        secret: Secret,
        cookie_name: str = "golden_key",
        settings: TransportSettings | None = None,
    ) -> None:
        _warn_if_headers_logged()
        self._secret = secret
        self._cookie_name = cookie_name
        self._settings = settings or TransportSettings()
        self._client = httpx.Client(**_client_kwargs(self._settings))  # type: ignore[arg-type]

    def __enter__(self) -> Fetcher:
        """Входит в контекстный менеджер.

        Returns:
            Fetcher: Сам объект.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Закрывает соединения при выходе из контекстного менеджера.

        Args:
            *exc (object): Сведения об исключении. Не используются.

        Returns:
            None
        """
        self.close()

    def close(self) -> None:
        """Закрывает пул соединений.

        Returns:
            None
        """
        self._client.close()

    def fetch(self, path: str) -> Observation:
        """Загружает одну страницу.

        Переходы выполняются вручную, а не средствами клиента: конечный URL нужен
        классификатору, и автоматический переход скрыл бы тот факт, что нас увели
        на страницу входа. Решение о каждом переходе принимает [_hops.next_hop] -
        то же самое, что и в асинхронном транспорте.

        Args:
            path (str): Путь или полный адрес страницы.

        Returns:
            Observation: Результат обращения. Если переход уводил на чужой хост
            либо понижал схему, возвращается ответ-перенаправление с чужим
            конечным адресом: решение принимает классификатор, а секрет туда не
            уходит вовсе.

        Raises:
            TimeoutError: Если истёк предел ожидания.
            NetworkError: При любом другом сетевом отказе.
            RemoteServerError: Если ответ превысил предел размера.
        """
        expected = host_of(self._settings.base_url)
        url = _start_url(self._settings, path)
        rejected_url: str | None = None
        redirects = 0
        sent = 0
        elapsed = 0.0

        while True:
            sent += 1
            try:
                # Секрет разворачивается здесь и уходит только на проверенный
                # хост. Заголовок собирается вручную: хранилище cookie
                # отключено, чтобы присланное площадкой значение не оседало.
                response = self._client.get(url, headers=self._cookie())
            except httpx.HTTPError as exc:
                raise _translate(exc, path) from exc
            elapsed += response.elapsed.total_seconds()

            hop = next_hop(
                current=url,
                is_redirect=response.is_redirect,
                location=response.headers.get("location", ""),
                redirects=redirects,
                max_redirects=self._settings.max_redirects,
                expected=expected,
            )
            if isinstance(hop, Follow):
                url = hop.url
                redirects += 1
                continue
            if isinstance(hop, Reject):
                _log_rejected(hop.url, expected)
                rejected_url = hop.url
                redirects += 1
            break

        return _observe(
            response,
            settings=self._settings,
            rejected_url=rejected_url,
            redirects=redirects,
            sent=sent,
            elapsed=elapsed,
        )

    def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]) -> Observation:
        """Отправляет форму и возвращает ответ.

        ПЕРЕХОДЫ НЕ ВЫПОЛНЯЮТСЯ, и это не упрощение. Повторить отправку по
        переходу значило бы отправить второй раз: у сообщения покупателю нет
        отмены, а повтор при неоднозначном исходе - второе сообщение. Переход в
        ответ на запись возвращается как есть, и решение принимает вызывающий,
        видящий, ЧТО именно он отправлял.

        Секрет уходит только на проверенный хост - тот же порядок, что и у
        чтения. Адрес собирается от базового, а не берётся у вызывающего
        целиком.

        Args:
            path (str): Путь обращения.
            fields (dict[str, str]): Поля формы.
            headers (dict[str, str]): Заголовки запроса, кроме Cookie.

        Returns:
            Observation: Результат обращения. Число переходов всегда ноль.

        Raises:
            TimeoutError: Если истёк предел ожидания.
            NetworkError: При любом другом сетевом отказе.
            RemoteServerError: Если ответ превысил предел размера.
        """
        url = _start_url(self._settings, path)
        try:
            response = self._client.post(url, data=fields, headers={**headers, **self._cookie()})
        except httpx.HTTPError as exc:
            raise _translate(exc, path) from exc

        return _observe(
            response,
            settings=self._settings,
            rejected_url=None,
            redirects=0,
            sent=1,
            elapsed=response.elapsed.total_seconds(),
        )

    def upload(
        self,
        path: str,
        *,
        field: str,
        filename: str,
        content: bytes,
        content_type: str,
        headers: dict[str, str],
    ) -> Observation:
        """Отправляет ФАЙЛ и возвращает ответ.

        ОТДЕЛЬНЫЙ МЕТОД, А НЕ ПРИЗНАК У submit, и это не оформление. Тело здесь
        собирается иначе - составное, с границей частей, - и правило у него своё:
        размер тела ограничивает ПЛОЩАДКА, и предел она объявляет на странице.
        Признак у submit означал бы, что оба правила живут в одном месте и
        различаются условием; условие однажды упростят.

        ПЕРЕХОДЫ НЕ ВЫПОЛНЯЮТСЯ, как и у submit: повторить отправку по переходу
        значило бы отправить файл второй раз.

        Args:
            path (str): Путь обращения.
            field (str): Имя поля, в котором уходит файл.
            filename (str): Имя файла, как его увидит площадка.
            content (bytes): Содержимое файла.
            content_type (str): Тип содержимого.
            headers (dict[str, str]): Заголовки запроса, кроме Cookie.

        Returns:
            Observation: Результат обращения. Число переходов всегда ноль.

        Raises:
            TimeoutError: Если истёк предел ожидания.
            NetworkError: При любом другом сетевом отказе.
            RemoteServerError: Если ответ превысил предел размера.
        """
        url = _start_url(self._settings, path)
        try:
            response = self._client.post(
                url,
                files={field: (filename, content, content_type)},
                headers={**headers, **self._cookie()},
            )
        except httpx.HTTPError as exc:
            raise _translate(exc, path) from exc

        return _observe(
            response,
            settings=self._settings,
            rejected_url=None,
            redirects=0,
            sent=1,
            elapsed=response.elapsed.total_seconds(),
        )

    def _cookie(self) -> dict[str, str]:
        """Собирает заголовок с сессионным секретом.

        Returns:
            dict[str, str]: Заголовок Cookie с единственным значением.
        """
        return {"Cookie": f"{self._cookie_name}={self._secret.reveal()}"}


class AsyncFetcher:
    """Асинхронный близнец [Fetcher].

    Отличается ровно тем, чем должен: ожиданием ответа. Решение о переходе,
    настройки клиента, сборка заголовка с секретом, перевод отказов и сборка
    наблюдения - общие с синхронным транспортом и живут в этом же модуле. Дважды
    написанное правило безопасности расходится, и цена расхождения здесь - чужой
    доступ к аккаунту.

    Args:
        secret (Secret): Сессионный секрет. Разворачивается только в момент
            сборки запроса.
        cookie_name (str): Имя cookie, в которой передаётся секрет.
        settings (TransportSettings): Настройки транспорта.
    """

    __slots__ = ("_client", "_cookie_name", "_secret", "_settings")

    def __init__(
        self,
        secret: Secret,
        cookie_name: str = "golden_key",
        settings: TransportSettings | None = None,
    ) -> None:
        _warn_if_headers_logged()
        self._secret = secret
        self._cookie_name = cookie_name
        self._settings = settings or TransportSettings()
        self._client = httpx.AsyncClient(**_client_kwargs(self._settings))  # type: ignore[arg-type]

    async def __aenter__(self) -> AsyncFetcher:
        """Входит в асинхронный контекстный менеджер.

        Returns:
            AsyncFetcher: Сам объект.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Закрывает соединения при выходе из контекстного менеджера.

        Args:
            *exc (object): Сведения об исключении. Не используются.

        Returns:
            None
        """
        await self.close()

    async def close(self) -> None:
        """Закрывает пул соединений.

        Returns:
            None
        """
        await self._client.aclose()

    async def fetch(self, path: str) -> Observation:
        """Загружает одну страницу.

        Args:
            path (str): Путь или полный адрес страницы.

        Returns:
            Observation: Результат обращения, устроенный так же, как у
            синхронного транспорта.

        Raises:
            TimeoutError: Если истёк предел ожидания.
            NetworkError: При любом другом сетевом отказе.
            RemoteServerError: Если ответ превысил предел размера.
        """
        expected = host_of(self._settings.base_url)
        url = _start_url(self._settings, path)
        rejected_url: str | None = None
        redirects = 0
        sent = 0
        elapsed = 0.0

        while True:
            sent += 1
            try:
                response = await self._client.get(url, headers=self._cookie())
            except httpx.HTTPError as exc:
                raise _translate(exc, path) from exc
            elapsed += response.elapsed.total_seconds()

            hop = next_hop(
                current=url,
                is_redirect=response.is_redirect,
                location=response.headers.get("location", ""),
                redirects=redirects,
                max_redirects=self._settings.max_redirects,
                expected=expected,
            )
            if isinstance(hop, Follow):
                url = hop.url
                redirects += 1
                continue
            if isinstance(hop, Reject):
                _log_rejected(hop.url, expected)
                rejected_url = hop.url
                redirects += 1
            break

        return _observe(
            response,
            settings=self._settings,
            rejected_url=rejected_url,
            redirects=redirects,
            sent=sent,
            elapsed=elapsed,
        )

    async def submit(
        self, path: str, fields: dict[str, str], headers: dict[str, str]
    ) -> Observation:
        """Отправляет форму и возвращает ответ.

        ПЕРЕХОДЫ НЕ ВЫПОЛНЯЮТСЯ, и это не упрощение. Повторить отправку по
        переходу значило бы отправить второй раз: у сообщения покупателю нет
        отмены, а повтор при неоднозначном исходе - второе сообщение. Переход в
        ответ на запись возвращается как есть, и решение принимает вызывающий,
        видящий, ЧТО именно он отправлял.

        Секрет уходит только на проверенный хост - тот же порядок, что и у
        чтения. Адрес собирается от базового, а не берётся у вызывающего
        целиком.

        Args:
            path (str): Путь обращения.
            fields (dict[str, str]): Поля формы.
            headers (dict[str, str]): Заголовки запроса, кроме Cookie.

        Returns:
            Observation: Результат обращения. Число переходов всегда ноль.

        Raises:
            TimeoutError: Если истёк предел ожидания.
            NetworkError: При любом другом сетевом отказе.
            RemoteServerError: Если ответ превысил предел размера.
        """
        url = _start_url(self._settings, path)
        try:
            response = await self._client.post(
                url, data=fields, headers={**headers, **self._cookie()}
            )
        except httpx.HTTPError as exc:
            raise _translate(exc, path) from exc

        return _observe(
            response,
            settings=self._settings,
            rejected_url=None,
            redirects=0,
            sent=1,
            elapsed=response.elapsed.total_seconds(),
        )

    async def upload(
        self,
        path: str,
        *,
        field: str,
        filename: str,
        content: bytes,
        content_type: str,
        headers: dict[str, str],
    ) -> Observation:
        """Отправляет ФАЙЛ и возвращает ответ.

        ОТДЕЛЬНЫЙ МЕТОД, А НЕ ПРИЗНАК У submit, и это не оформление. Тело здесь
        собирается иначе - составное, с границей частей, - и правило у него своё:
        размер тела ограничивает ПЛОЩАДКА, и предел она объявляет на странице.
        Признак у submit означал бы, что оба правила живут в одном месте и
        различаются условием; условие однажды упростят.

        ПЕРЕХОДЫ НЕ ВЫПОЛНЯЮТСЯ, как и у submit: повторить отправку по переходу
        значило бы отправить файл второй раз.

        Args:
            path (str): Путь обращения.
            field (str): Имя поля, в котором уходит файл.
            filename (str): Имя файла, как его увидит площадка.
            content (bytes): Содержимое файла.
            content_type (str): Тип содержимого.
            headers (dict[str, str]): Заголовки запроса, кроме Cookie.

        Returns:
            Observation: Результат обращения. Число переходов всегда ноль.

        Raises:
            TimeoutError: Если истёк предел ожидания.
            NetworkError: При любом другом сетевом отказе.
            RemoteServerError: Если ответ превысил предел размера.
        """
        url = _start_url(self._settings, path)
        try:
            response = await self._client.post(
                url,
                files={field: (filename, content, content_type)},
                headers={**headers, **self._cookie()},
            )
        except httpx.HTTPError as exc:
            raise _translate(exc, path) from exc

        return _observe(
            response,
            settings=self._settings,
            rejected_url=None,
            redirects=0,
            sent=1,
            elapsed=response.elapsed.total_seconds(),
        )

    def _cookie(self) -> dict[str, str]:
        """Собирает заголовок с сессионным секретом.

        Returns:
            dict[str, str]: Заголовок Cookie с единственным значением.
        """
        return {"Cookie": f"{self._cookie_name}={self._secret.reveal()}"}


def _start_url(settings: TransportSettings, path: str) -> str:
    """Приводит путь к полному адресу.

    Args:
        settings (TransportSettings): Настройки транспорта.
        path (str): Путь либо полный адрес.

    Returns:
        str: Полный адрес запроса.
    """
    return urljoin(settings.base_url, path)


def _header_int(response: httpx.Response, name: str) -> int | None:
    """Читает целочисленный заголовок ответа.

    Args:
        response (httpx.Response): Ответ.
        name (str): Имя заголовка.

    Returns:
        int | None: Значение либо None, если заголовка нет или он не число.
    """
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _retry_after_ms(response: httpx.Response) -> int | None:
    """Читает заголовок Retry-After в миллисекундах.

    Разбирается только числовая форма, в секундах. Форма с датой не
    поддерживается намеренно: она требует доверия к часам площадки и к
    согласованности часовых поясов, а ошибка здесь выражается в неверной паузе -
    то есть в поведении, которое потом объясняют чем угодно, кроме заголовка.

    Args:
        response (httpx.Response): Ответ.

    Returns:
        int | None: Пауза в миллисекундах либо None.
    """
    seconds = _header_int(response, "retry-after")
    if seconds is None or seconds < 0:
        return None
    return seconds * 1000
