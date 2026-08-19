"""Транспортный слой наблюдения.

Слой намеренно тонкий: задача спайка - не построить клиент, а один раз аккуратно
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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin

import httpx

from ._host import host_of, is_safe_hop
from ._secret import Secret
from .errors import NetworkError, RemoteServerError, TimeoutError

__all__ = ["Observation", "Fetcher", "TransportSettings"]

_log = logging.getLogger("funora.transport")

#: Журналы, которые печатают заголовки запроса на уровне DEBUG.
_RISKY_LOGGERS: Final[tuple[str, ...]] = ("httpx", "httpcore", "urllib3")

#: Флаг, чтобы предупреждение о журналировании выдавалось один раз за процесс.
_warned = False


@dataclass(frozen=True, slots=True)
class TransportSettings:
    """Настройки транспорта.

    Значения соответствуют spec/runtime/budget.yaml. Там они помечены как
    провизорные и будут уточнены по результатам наблюдений.

    Args:
        base_url (str): Базовый адрес площадки.
        connect_timeout_s (float): Предел на установку соединения, секунды.
        read_timeout_s (float): Предел на чтение ответа, секунды.
        max_connections (int): Предел одновременных соединений на хост.
        max_response_bytes (int): Предел размера ответа, байты.
        max_redirects (int): Предел числа переходов при ручном следовании.
        user_agent (str): Значение заголовка User-Agent. Задаётся спецификацией,
            а не оставляется на усмотрение реализации: одинаковое поведение
            шести SDK начинается с того, как они представляются.
    """

    base_url: str = "https://funpay.com"
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 20.0
    max_connections: int = 4
    max_response_bytes: int = 8 * 1024 * 1024
    max_redirects: int = 5
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


class Fetcher:
    """Выполняет одиночные запросы к площадке.

    Args:
        secret (Secret): Сессионный секрет. Разворачивается только в момент
            сборки запроса.
        cookie_name (str): Имя cookie, в которой передаётся секрет.
        settings (TransportSettings): Настройки транспорта.
    """

    __slots__ = ("_secret", "_cookie_name", "_settings", "_client")

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
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=self._settings.connect_timeout_s,
                read=self._settings.read_timeout_s,
                write=self._settings.read_timeout_s,
                pool=self._settings.connect_timeout_s,
            ),
            limits=httpx.Limits(
                max_connections=self._settings.max_connections,
                max_keepalive_connections=self._settings.max_connections,
            ),
            follow_redirects=False,
            # Хранилище cookie отключено намеренно. С включённым площадка одним
            # заголовком Set-Cookie подкладывала свой golden_key, и он уходил
            # следующим запросом ВПЕРЕДИ настоящего: сервер читает первое
            # вхождение, и клиент молча читал чужой аккаунт как свой. Ни
            # исключения, ни повреждений, ни строки в журнале - правдоподобные
            # данные не того аккаунта. Заголовок Cookie собирается вручную.
            cookies=None,
            headers={
                "User-Agent": self._settings.user_agent,
                "Accept-Language": "ru,en;q=0.8",
            },
        )

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
        на страницу входа.

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
        url = urljoin(self._settings.base_url, path)
        rejected_url: str | None = None
        redirects = 0
        sent = 0
        elapsed = 0.0

        while True:
            # Секрет разворачивается здесь и уходит только на проверенный хост.
            # Заголовок собирается вручную: хранилище cookie отключено, чтобы
            # присланное площадкой значение не оседало и не уходило следующим
            # запросом впереди настоящего.
            sent += 1
            try:
                response = self._client.get(
                    url,
                    headers={"Cookie": f"{self._cookie_name}={self._secret.reveal()}"},
                )
            except httpx.TimeoutException as exc:
                # Отказы стека переводятся в иерархию Funora здесь, а не у
                # вызывающего. Иначе обработчик, ловящий FunoraError, пропускал
                # бы обрыв связи мимо себя, и цикл наблюдения падал бы целиком
                # вместо повтора - при том, что политика повторов для сетевых
                # отказов написана и покрыта тестами.
                raise TimeoutError(f"истёк предел ожидания при обращении к {path}") from exc
            except httpx.HTTPError as exc:
                raise NetworkError(
                    f"сетевой отказ при обращении к {path}: {type(exc).__name__}"
                ) from exc
            elapsed += response.elapsed.total_seconds()

            if not (response.is_redirect and redirects < self._settings.max_redirects):
                break

            location = response.headers.get("location", "")
            if not location:
                break

            target = urljoin(url, location)
            if not is_safe_hop(url, target, expected):
                # Запрос туда не отправляется вовсе: проверка после отправки
                # была бы бесполезна, секрет уже ушёл бы.
                #
                # Отклонённый адрес всё равно объявляется конечным. Это не
                # мелочь: классификатор увидит чужой хост и поставит диагноз
                # wrong_identity, а с исходным адресом он увидел бы пустое тело
                # и сказал бы unknown - то есть «разметка изменилась» вместо
                # «нас пытались увести».
                _log.warning(
                    "переход отклонён: %s не принадлежит %s либо понижает схему",
                    host_of(target) or "адрес без хоста",
                    expected,
                )
                rejected_url = target
                redirects += 1
                break

            url = target
            redirects += 1

        raw = response.content
        if len(raw) > self._settings.max_response_bytes:
            raise RemoteServerError(
                f"ответ превысил предел {self._settings.max_response_bytes} байт: "
                f"получено {len(raw)}"
            )

        return Observation(
            status=response.status_code,
            final_url=rejected_url or str(response.url),
            html=response.text,
            elapsed_ms=int(elapsed * 1000),
            redirects=redirects,
            requests_sent=sent,
            content_length=len(raw),
            declared_length=_header_int(response, "content-length"),
            retry_after_ms=_retry_after_ms(response),
        )


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
