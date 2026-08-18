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

from ._secret import Secret

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
        content_length (int): Размер тела в байтах.
    """

    status: int
    final_url: str
    html: str
    elapsed_ms: int
    redirects: int
    content_length: int


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
            Observation: Результат обращения.

        Raises:
            httpx.HTTPError: При сетевом отказе.
            ValueError: Если ответ превысил предел размера или число переходов.
        """
        url = urljoin(self._settings.base_url, path)
        redirects = 0
        elapsed = 0.0

        while True:
            response = self._client.get(
                url,
                cookies={self._cookie_name: self._secret.reveal()},
            )
            elapsed += response.elapsed.total_seconds()

            if response.is_redirect and redirects < self._settings.max_redirects:
                location = response.headers.get("location", "")
                if not location:
                    break
                url = urljoin(url, location)
                redirects += 1
                continue
            break

        raw = response.content
        if len(raw) > self._settings.max_response_bytes:
            raise ValueError(f"ответ превысил предел {self._settings.max_response_bytes} байт")

        return Observation(
            status=response.status_code,
            final_url=str(response.url),
            html=response.text,
            elapsed_ms=int(elapsed * 1000),
            redirects=redirects,
            content_length=len(raw),
        )
