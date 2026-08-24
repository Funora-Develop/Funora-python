"""Синхронный клиент: способ выполнить то, о чём просит ядро.

Логики здесь нет. Нормативный порядок шагов, политика повторов, расход бюджета,
сдвиг курсора и правила гашения живут в [_engine.py] и не знают ни о сети, ни о
том, синхронно их крутят или асинхронно. Этот файл - двенадцать строк цикла,
который на просьбу сходить отвечает вызовом, на просьбу подождать - сном, а на
просьбу раздать события - раздачей.

Разделение не эстетическое. Асинхронный клиент отличается ровно этими тремя
строками; напиши мы его вторым файлом целиком - и нормативный порядок шагов
существовал бы в двух экземплярах. Копия расходится, это в проекте уже случалось
трижды с правилом хоста, и один раз ценой сессионного ключа.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from dataclasses import replace
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Final, TypeVar

from ._budget import Budget
from ._chats import ChatsPage
from ._engine import Deliver, Engine, Fetch, Pause, Reply, Request
from ._host import host_of
from ._identity import REGISTRY
from ._observed import Observed
from ._order import OrderView
from ._orders import OrdersPage
from ._poll import Schedule
from ._proxies import DEFAULT_ACCOUNT, Proxy, ProxyPool
from ._reviews import ReviewsPage
from ._secret import Secret, SecretProvider
from ._thread import Thread
from ._transport import Fetcher, TransportSettings
from ._watch import Router, dispatch
from .capabilities import Capability, CapabilityState
from .errors import ConfigurationError, FunoraError, HandlerError, NotImplementedOperationError

if TYPE_CHECKING:
    from ._transport import Observation

__all__ = ["Client", "OrdersService", "ChatsService"]

_log = logging.getLogger("funora.client")

#: Тип, которым завершается сопрограмма ядра. Синтаксис PEP 695 не годится:
#: пакет поддерживает Python 3.11, где его ещё нет.
T = TypeVar("T")


#: Службы, объявленные контрактом, и записи реестра о том, чего в них нет.
#:
#: Перечень здесь, а не в порождённом файле: он говорит о РЕАЛИЗАЦИИ - какие
#: службы она не написала, - а не о контракте. Сверяется он проверкой, которая
#: читает spec/services и spec/conformance/not-implemented.yaml: разойдись
#: перечень с ними, обращение к новой службе давало бы голый отказ языка.
_SERVICES_IN_CONTRACT: Final[dict[str, str]] = {
    "account": "account_service_operations",
    "catalog": "catalog_service_operations",
    "lots": "lots_service_operations",
    "market": "market_service_operations",
}


class OrdersService:
    """Операции над заказами.

    Сервис - это не слой ради слоя. Он даёт вызывающему одно имя на одну
    операцию и позволяет менять способ чтения, не трогая вызов.

    Args:
        client (Client): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, order_id: str) -> OrderView:
        """Читает страницу одного заказа.

        Возвращает то, что страница вправду показывает, - не полный Order:
        сторон она не разделяет, кода валюты не даёт.

        Args:
            order_id (str): Номер заказа. Тот самый, что стоит в адресе.

        Returns:
            OrderView: Заказ, его контрагент, отзыв и идентификатор диалога.
            Параметры заказа выдаются через `params()` как есть, без имён.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_order(order_id))

    def list(self) -> OrdersPage:
        """Читает список заказов.

        Returns:
            OrdersPage: Разобранная страница. Записи выдаются через `rows()`:
            без accept_incomplete он требует полноты, с ним отдаёт что есть.

            Прежде здесь обещался ещё и `entries()` - метода с таким именем у
            страницы нет и не было.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_orders())


class ChatsService:
    """Операции над перепиской.

    Args:
        client (Client): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> ChatsPage:
        """Читает список диалогов.

        Returns:
            ChatsPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_chats())

    def thread(self, node_id: str) -> Thread:
        """Читает переписку одного диалога.

        Args:
            node_id (str): Идентификатор диалога. Тот самый, что стоит в адресе
                после `?node=`.

        Returns:
            Thread: Разобранная переписка.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_thread(node_id))


class ReviewsService:
    """Операции над отзывами.

    Args:
        client (Client): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, user_id: str) -> ReviewsPage:
        """Читает отзывы с профиля продавца.

        Полнота здесь означает «разобраны все строки, которые страница отдала»,
        а не «прочитаны все отзывы продавца»: сверить их число не с чем. Разница
        объявлена записью reviews_page_totality в реестре неисполненного.

        Args:
            user_id (str): Идентификатор продавца. Тот самый, что стоит в адресе
                профиля.

        Returns:
            ReviewsPage: Разобранная страница. Отзывы выдаются через `rows()`.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_reviews(user_id))


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
        budget (Budget | None): Общий бюджет запросов. Передаётся, когда в одном
            процессе живут несколько клиентов: площадке видна сетевая
            идентичность, а не то, сколько клиентов мы завели у себя, и общий
            предел обходится ровно тем, что каждый заводит свой бюджет.

    Raises:
        ConfigurationError: Если не передано ни секрета, ни транспорта. Повтор
            здесь не поможет, исправлять надо вызов.
    """

    __slots__ = ("_fetcher", "chats", "engine", "orders", "pool", "reviews")

    def __init__(
        self,
        secret: Secret | SecretProvider | None = None,
        *,
        settings: TransportSettings | None = None,
        experimental: frozenset[Capability] | None = None,
        transport: Fetcher | None = None,
        budget: Budget | None = None,
        proxies: tuple[Proxy, ...] = (),
    ) -> None:
        resolved_settings = settings or TransportSettings()

        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = Fetcher(resolved, settings=resolved_settings)
        else:
            raise ConfigurationError(
                "клиенту нужен либо секрет, либо готовый транспорт: без них "
                "обратиться к площадке не от кого"
            )

        # Пул заводится до движка: бюджет берётся у выбранной идентичности, а
        # выбор идентичности - его работа.
        self.pool = ProxyPool(
            proxies,
            host=host_of(resolved_settings.base_url) or resolved_settings.base_url,
        )

        # Идентичность выбирается один раз и передаётся движку: ограничение
        # частоты обязано дойти до неё, а не до безымянного бюджета. Наблюдение
        # перепривяжет аккаунт к другой, если эта остынет.
        identity_name, proxy_url = self.pool.choose(DEFAULT_ACCOUNT)
        identity = REGISTRY.get(identity_name)
        if proxy_url is not None:
            resolved_settings = replace(resolved_settings, proxy_url=proxy_url)

        self.engine = Engine(
            resolved_settings,
            budget or identity.budget,
            experimental or frozenset(),
            identity,
        )
        self.orders = OrdersService(self)
        self.chats = ChatsService(self)
        self.reviews = ReviewsService(self)

    def __getattr__(self, name: str) -> object:
        """Отвечает на обращение к службе, которой у этой реализации нет.

        Служб в контракте шесть, написаны две. Прежде обращение к остальным
        давало голый AttributeError: бот, обернувший работу в except FunoraError,
        падал не операцией, а всем процессом, и узнавал причину из трассировки.

        Заглушек не заводится нарочно. Заглушка существует как атрибут, и
        hasattr на ней вернул бы True - проверка «умеет ли эта версия SDK
        работать с лотами» начала бы врать. Здесь атрибута нет, hasattr
        возвращает False, а обращение поднимает отказ, который ловится и общим
        перехватом, и привычным except AttributeError.

        Args:
            name (str): Имя, которого у клиента нет.

        Returns:
            object: Ничего не возвращает.

        Raises:
            NotImplementedOperationError: Если имя - объявленная контрактом
                служба. Текст называет запись реестра, где сказано, чего именно
                не хватает.
            AttributeError: Если имя просто опечатка. Обычный отказ языка: имя,
                которого нет ни в контракте, ни в реализации, - не пробел
                реализации, а ошибка вызывающего.
        """
        declared = _SERVICES_IN_CONTRACT.get(name)
        if declared is None:
            raise AttributeError(f"{type(self).__name__!r} не имеет атрибута {name!r}")
        raise NotImplementedOperationError(
            f"служба «{name}» объявлена контрактом и не написана этой "
            f"реализацией. Чего именно не хватает, сказано в записи реестра "
            f"«{declared}» - spec/conformance/not-implemented.yaml.\n\n"
            "Проверить заранее можно через hasattr: у ненаписанной службы он "
            "возвращает False."
        )

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

    @property
    def locale(self) -> Observed[str]:
        """Возвращает локаль интерфейса, как её отдала площадка.

        Локаль привязана к аккаунту, а не к адресу: переключить её запросом
        нельзя. Разбор от смены языка не ломается - он структурный, - но поля,
        приходящие текстом (описание заказа, подпись времени, имя собеседника),
        возвращаются на этом языке.

        Returns:
            Observed[str]: Локаль либо причина, по которой её не видно. До
            первого чтения - не наблюдалась.
        """
        return self.engine._state.locale

    @property
    def stopped(self) -> FunoraError | None:
        """Возвращает ошибку, остановившую клиента.

        Полная остановка наступает по признаку fail_closed у политики повторов:
        сегодня это отказ в доступе и страница проверки. Обе - ответ площадки
        на поведение клиента, а не сбой связи.

        Returns:
            FunoraError | None: Ошибка либо None, если клиент работает.
        """
        return self.engine.stopped

    def resume(self) -> None:
        """Снимает полную остановку и разрешает снова ходить на площадку.

        Решение принимает человек: он один знает, разобрался ли с причиной.
        Сама по себе остановка не истекает и по времени не снимается -
        истекающая означала бы возврат на площадку, которая отказала в доступе,
        без чьего-либо ведома.

        Returns:
            None
        """
        self.engine.resume()

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.
        """
        return self.engine.capability(capability)

    def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
        max_threads_per_step: int = 5,
        on_handler_error: Callable[[HandlerError], None] | None = None,
    ) -> None:
        """Ведёт наблюдение: опрашивает площадку и раздаёт события обработчикам.

        Метод блокирующий и спит между опросами. Цикл целиком описан ядром;
        здесь он только исполняется.

        Args:
            router (Router): Реестр обработчиков.
            account_id (str): Идентификатор аккаунта для отпечатков событий.
            max_iterations (int | None): Сколько шагов сделать. None означает
                бесконечно; ограничение нужно проверкам и разовым прогонам.
            schedule (Schedule | None): Расписание опроса. По умолчанию из
                спецификации.
            state_path (Path | None): Файл, в котором состояние гашения повторов
                переживает перезапуск.
            max_threads_per_step (int): Сколько переписок дочитывать за один
                шаг. Изменившийся диалог говорит, что в нём что-то произошло, но
                само сообщение видно только на странице переписки. Предел нужен:
                изменись разом полсотни диалогов, шаг превратился бы в полсотни
                запросов. Непрочитанные не теряются - они ждут в очереди.
            on_handler_error (Callable[[HandlerError], None] | None): Что
                делать с отказом обработчика. Вызывается по одному разу на
                каждый отказ, сразу после раздачи партии.

                Без него отказ виден только в журнале. Наблюдение при этом не
                встаёт и не слепнет - новые события порождаются по-прежнему, -
                но курсор не двигается, и непринятое событие приходит снова
                каждый шаг. Бесконечно, пока обработчик не перестанет падать.
                Заметить это, не читая журнал, нечем.

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        self.run(
            self.engine.watch(
                router,
                account_id=account_id,
                max_iterations=max_iterations,
                schedule=schedule,
                state_path=state_path,
                max_threads_per_step=max_threads_per_step,
            ),
            router=router,
            on_handler_error=on_handler_error,
        )

    def run(
        self,
        core: Generator[Request, Reply, T],
        *,
        router: Router | None = None,
        on_handler_error: Callable[[HandlerError], None] | None = None,
    ) -> T:
        """Прокручивает ядро, выполняя то, о чём оно просит.

        Отказ сети не возвращается ядру значением, а бросается внутрь. Иначе
        политику повторов пришлось бы писать здесь второй раз - а она в ядре
        написана и проверена.

        Args:
            core (Generator[Request, Reply, T]): Сопрограмма ядра.
            router (Router | None): Реестр обработчиков. Нужен только тем
                сопрограммам, которые просят раздать события.
            on_handler_error (Callable[[HandlerError], None] | None): Что делать
                с отказом обработчика. Ядру отказы не видны: оно читает у итога
                раздачи delivered, advance, fatal и длину failed. Причина
                отказа живёт только здесь.

        Returns:
            T: То, чем ядро завершилось.

        Raises:
            FunoraError: Любая ошибка, которую ядро не погасило повтором.
        """
        reply: Reply = None
        failure: FunoraError | None = None
        while True:
            try:
                request = core.throw(failure) if failure is not None else core.send(reply)
            except StopIteration as stop:
                return stop.value  # type: ignore[no-any-return]
            failure = None
            reply = None

            if isinstance(request, Pause):
                if request.ms > 0:
                    sleep(request.ms / 1000)
            elif isinstance(request, Fetch):
                try:
                    reply = self._fetch(request.path)
                except FunoraError as exc:
                    failure = exc
            elif isinstance(request, Deliver):
                if router is None:
                    raise ConfigurationError(
                        "ядро просит раздать события, но реестр обработчиков не передан"
                    )
                reply = dispatch(router, request.events)
                # Итог раздачи дальше уходит ядру, а ядро читает у него
                # delivered, advance, fatal и длину failed. Причина отказа
                # живёт только здесь, и не отдать её сейчас значит потерять
                # насовсем.
                if on_handler_error is not None:
                    # Имя намеренно не failure: так зовут переменную, которой
                    # цикл бросает ошибку ВНУТРЬ ядра. Затерев её здесь, мы
                    # отправили бы отказ обработчика в ядро как условие
                    # площадки и уронили бы наблюдение вместо жалобы.
                    for handler_error in reply.errors:
                        on_handler_error(handler_error)

    def _fetch(self, path: str) -> Observation:
        """Выполняет одно обращение к площадке.

        Args:
            path (str): Путь страницы.

        Returns:
            Observation: Результат обращения.

        Raises:
            FunoraError: При сетевом отказе либо непригодном ответе.
        """
        return self._fetcher.fetch(path)
