"""Клиент: единственное место, где части собираются вместе.

Всё, что здесь вызывается, чистое: разбор, классификация, планировщик повторов,
ворота возможности. Грязное только одно - обращение к сети и сон между
попытками, и оно собрано в этом файле нарочно. Так проверяется всё остальное без
сети, а этот слой остаётся достаточно тонким, чтобы его можно было прочитать
целиком.

Порядок шагов нормативен и записан в spec/protocol/response-classes.yaml. Две
реализации, проверившие условия в разном порядке, разойдутся именно на той
странице, ради которой правило написано.

Чего здесь пока нет и почему это сказано вслух. Бюджет запросов не резервируется:
модуля бюджета ещё нет, числа в spec/runtime/budget.yaml помечены провизорными, и
подставить их сейчас значило бы выдать догадку за ограничение. До появления
бюджета клиент рассчитан на разумную частоту вызовов со стороны вызывающего.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import sleep
from typing import Final

from ._classify import DEFAULT_IDENTITY_CSS, classify
from ._gate import check_capability
from ._orders import Completeness, OrdersPage, parse_orders_page
from ._retry import Safety, plan_attempt
from ._secret import Secret, SecretProvider
from ._transport import Fetcher, Observation, TransportSettings
from ._verdicts import error_for
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import ConfigurationError, FunoraError, NetworkError

__all__ = ["Client", "OrdersService"]

_log = logging.getLogger("funora.client")

#: Путь страницы списка заказов.
_ORDERS_PATH: Final[str] = "/orders/trade"


@dataclass
class _State:
    """Состояние клиента, живущее между вызовами.

    Attributes:
        capabilities (dict[Capability, CapabilityState]): Текущие состояния
            возможностей. Начальные берутся из спецификации.
        session_ever_valid (bool): Подтверждалась ли сессия хоть раз. Отличает
            истёкшую сессию от неверного секрета, а это разные диагнозы с разным
            лечением.
        opted_in (frozenset[Capability]): Возможности, включённые вызывающим
            явно.
    """

    capabilities: dict[Capability, CapabilityState] = field(
        default_factory=lambda: dict(CAPABILITY_INITIAL)
    )
    session_ever_valid: bool = False
    opted_in: frozenset[Capability] = frozenset()


def _check_integrity(observation: Observation) -> None:
    """Проверяет, что тело ответа получено целиком.

    Шаг стоит до классификации намеренно. Страница, оборванная посреди таблицы,
    проходит классификацию как пригодная и разбор как полный: вызывающий
    получает половину заказов и ноль повреждений. Это правдоподобный неверный
    ответ, о неверности которого узнать неоткуда.

    Args:
        observation (Observation): Результат обращения.

    Returns:
        None

    Raises:
        NetworkError: Если полученная длина меньше объявленной.
    """
    declared = observation.declared_length
    if declared is None or observation.content_length >= declared:
        return
    raise NetworkError(
        f"тело ответа получено не целиком: объявлено {declared} байт, "
        f"получено {observation.content_length}. Это обрыв соединения, "
        "а не изменение разметки"
    )


class OrdersService:
    """Операции над заказами.

    Args:
        client (Client): Клиент, которому принадлежит служба.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> OrdersPage:
        """Читает список заказов.

        Возвращает сокращённые записи, а не полные заказы: страница не даёт ни
        валюты, ни машиночитаемого времени, ни статуса. Подробности в
        :class:`~funora._orders.OrderListEntry`.

        Признавать неполноту здесь не нужно и нельзя: страница возвращается как
        есть, а решение принимается там, где спрашивают записи. Иначе признание
        пришлось бы давать до того, как стало известно, что признавать.

        Returns:
            OrdersPage: Записи вместе с полнотой и перечнем повреждений.
            Получить записи можно методом :meth:`~funora._orders.OrdersPage.rows`.

        Raises:
            FunoraError: Любая ошибка из иерархии Funora. Какая именно, решает
                таблица соответствия вердиктов из спецификации.
        """
        return self._client._read_orders()


class Client:
    """Клиент площадки.

    Args:
        secret (Secret | SecretProvider | None): Сессионный секрет либо его
            источник. Не нужен, если передан готовый транспорт.
        settings (TransportSettings | None): Настройки транспорта.
        experimental (frozenset[Capability] | None): Возможности, которые
            вызывающий включает явно, соглашаясь на возможную смену контракта.
        transport (Fetcher | None): Готовый транспорт. Нужен там, где вызывающий
            собирает его сам, и в проверках: создание транспорта поднимает
            контекст TLS, а это полсекунды на каждый вызов, из-за чего набор
            проверок начинают выключать.

    Raises:
        ConfigurationError: Если не передано ни секрета, ни транспорта. Повтор
            здесь не поможет, исправлять надо вызов.
    """

    __slots__ = ("_fetcher", "_settings", "_state", "orders")

    def __init__(
        self,
        secret: Secret | SecretProvider | None = None,
        *,
        settings: TransportSettings | None = None,
        experimental: frozenset[Capability] | None = None,
        transport: Fetcher | None = None,
    ) -> None:
        self._settings = settings or TransportSettings()

        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = Fetcher(resolved, settings=self._settings)
        else:
            raise ConfigurationError(
                "клиенту нужен либо секрет, либо готовый транспорт: без них "
                "обратиться к площадке не от кого"
            )

        self._state = _State(opted_in=experimental or frozenset())
        self.orders = OrdersService(self)

    def __enter__(self) -> Client:
        """Входит в контекстный менеджер.

        Returns:
            Client: Сам объект.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Закрывает соединения при выходе.

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
        self._fetcher.close()

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.
        """
        return self._state.capabilities[capability]

    def _read_orders(self) -> OrdersPage:
        """Выполняет чтение списка заказов по нормативному порядку шагов.

        Returns:
            OrdersPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        capability = Capability.ORDERS_LIST
        check_capability(
            capability,
            state=self._state.capabilities[capability],
            opted_in=capability in self._state.opted_in,
        )

        host = self._settings.base_url.split("//", 1)[-1].split("/", 1)[0]
        attempt = 0
        while True:
            attempt += 1
            # Заголовок Retry-After живёт в ответе, а до ответа его нет. Значение
            # держится отдельной переменной, чтобы обработчик не зависел от
            # того, успел ли ответ появиться.
            retry_after_ms: int | None = None
            try:
                observation = self._fetcher.fetch(_ORDERS_PATH)
                retry_after_ms = observation.retry_after_ms
                _check_integrity(observation)
                verdict = classify(
                    status=observation.status,
                    final_url=observation.final_url,
                    html=observation.html,
                    expected_host=host,
                    identity_css=DEFAULT_IDENTITY_CSS,
                )
                error = error_for(verdict, session_ever_valid=self._state.session_ever_valid)
                if error is not None:
                    raise error
            except FunoraError as exc:
                self._note_failure(capability, exc)
                plan = plan_attempt(
                    exc,
                    attempt=attempt,
                    safety=Safety.SAFE,
                    retry_after_ms=retry_after_ms,
                )
                if not plan.retry:
                    raise
                _log.info(
                    "повтор после %s через %d мс (попытка %d, причина %s)",
                    type(exc).__name__,
                    plan.delay_ms,
                    attempt,
                    plan.reason,
                )
                sleep(plan.delay_ms / 1000)
                continue

            self._state.session_ever_valid = True
            page = parse_orders_page(observation.html, observed_at=datetime.now(UTC))
            self._note_success(capability, page)
            return page

    def _note_success(self, capability: Capability, page: OrdersPage) -> None:
        """Записывает состояние возможности по успешному чтению.

        Args:
            capability (Capability): Возможность.
            page (OrdersPage): Прочитанная страница.

        Returns:
            None
        """
        if page.completeness is Completeness.COMPLETE:
            self._state.capabilities[capability] = CapabilityState.SUPPORTED
            return

        self._state.capabilities[capability] = CapabilityState.DEGRADED
        # Предупреждение пишется независимо от того, признал ли вызывающий
        # неполноту. Это единственная защита от того, чтобы признание
        # выродилось в ритуал: молча принятая неполнота перестаёт быть
        # заметной уже на второй неделе.
        _log.warning(
            "чтение %s неполно: %s (%s), собрано %d из %d, повреждений %d",
            capability.value,
            page.completeness,
            page.reason,
            page.rows_accepted,
            page.rows_total,
            len(page.defects),
        )

    def _note_failure(self, capability: Capability, error: FunoraError) -> None:
        """Записывает состояние возможности по неудачному чтению.

        Состояние unsupported не выставляется никогда. Позитивным свидетельством
        отсутствия была бы полученная страница с совпавшим отпечатком и
        отсутствующим разделом заказов; такой страницы никто не видел, и
        выставлять состояние по отказу значило бы принимать сбой сети за
        отсутствие возможности.

        Args:
            capability (Capability): Возможность.
            error (FunoraError): Полученная ошибка.

        Returns:
            None
        """
        if getattr(error, "provisional", False):
            return
        if isinstance(error, NetworkError):
            return
        _log.debug(
            "состояние возможности %s не изменено после %s",
            capability.value,
            type(error).__name__,
        )
