"""Асинхронный клиент: тот же способ, но через ожидание.

Файл читается рядом с [_client.py], и это не совпадение, а условие. Оба -
драйверы одного ядра из [_engine.py]: на просьбу сходить отвечают обращением, на
просьбу подождать - паузой, на просьбу раздать события - раздачей. Отличаются
ровно тремя строками, в которых стоит ``await``.

Нормативного порядка шагов здесь нет. Политики повторов нет. Расхода бюджета,
сдвига курсора, правил гашения - нет. Всё это написано один раз и проверено один
раз; сюда оно попадает готовым.

Обработчики принимаются и обычные, и асинхронные. Обычный вызывается как есть,
сопрограмма дожидается. Обратное - асинхронный обработчик в синхронном клиенте -
отвергается вслух: промолчать значило бы зарегистрировать обработчик, который
никогда не выполнится.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Generator
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from ._account import BalancePage
from ._budget import Budget
from ._catalog import CatalogPage
from ._chats import ChatsPage
from ._engine import Deliver, Engine, Fetch, Pause, Reply, Request, Submit
from ._host import host_of
from ._identity import REGISTRY
from ._observed import Observed
from ._order import OrderView
from ._orders import OrdersPage
from ._poll import Schedule
from ._proxies import DEFAULT_ACCOUNT, Proxy, ProxyPool
from ._reviews import ReviewsPage
from ._runner import SendResult
from ._secret import Secret, SecretProvider
from ._showcase import ShowcasePage
from ._thread import Thread
from ._transport import AsyncFetcher, TransportSettings
from ._watch import Router, adispatch
from ._whoami import Account, CapabilityProfile, SessionHealth
from .capabilities import Capability, CapabilityState
from .errors import ConfigurationError, FunoraError, HandlerError

if TYPE_CHECKING:
    from ._transport import Observation

__all__ = ["AsyncClient", "AsyncOrdersService", "AsyncChatsService"]

_log = logging.getLogger("funora.client")

#: Тип, которым завершается сопрограмма ядра.
T = TypeVar("T")


class AsyncOrdersService:
    """Операции над заказами.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, order_id: str) -> OrderView:
        """Читает страницу одного заказа.

        Args:
            order_id (str): Номер заказа. Тот самый, что стоит в адресе.

        Returns:
            OrderView: Заказ в том виде, в каком его отдала страница.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_order(order_id))

    async def list(self) -> OrdersPage:
        """Читает список заказов.

        Returns:
            OrdersPage: Разобранная страница. Записи выдаются через `entries()`
            либо `rows()`: первый требует признать неполноту, второй отдаёт что
            есть.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_orders())


class AsyncReviewsService:
    """Операции над отзывами.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, user_id: str) -> ReviewsPage:
        """Читает отзывы с профиля продавца.

        Полнота здесь означает «разобраны все строки, которые страница отдала»,
        а не «прочитаны все отзывы продавца»: сверить их число не с чем.

        Args:
            user_id (str): Идентификатор продавца. Тот самый, что стоит в адресе
                профиля.

        Returns:
            ReviewsPage: Разобранная страница. Отзывы выдаются через `rows()`.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_reviews(user_id))


class AsyncChatsService:
    """Операции над перепиской.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list(self) -> ChatsPage:
        """Читает список диалогов.

        Returns:
            ChatsPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_chats())

    async def send_text(
        self, node_id: str, text: str, *, declared_cold: bool = False
    ) -> SendResult:
        """Отправляет текстовое сообщение в переписку.

        ИСКЛЮЧЕНИЕ ОЗНАЧАЕТ, ЧТО СООБЩЕНИЕ НЕ УШЛО. Всё, что случилось после
        ухода запроса, возвращается исходом: у неоднозначного исхода есть своё
        значение, и брошенное исключение прочиталось бы как неудача.

        ИСХОДА ТРИ, и третий - честное незнание. Читать его надо признаком
        is_confirmed, а не истинностью самой квитанции: у неё три значения, и
        `if result` прочло бы неподтверждённое как успех.

        Args:
            node_id (str): Числовой идентификатор диалога.
            text (str): Текст сообщения.
            declared_cold (bool): Признание, что переписка холодная и вы пишете
                первым. Без него холодное обращение отвергается: отсутствие
                входящего в окне - положительный признак холода.

        Returns:
            SendResult: Исход, причина и прочитанное из ответа.

        Raises:
            FunoraError: Если отправка не состоялась - страница непригодна,
                упёрлись в предел, отказала сеть до ухода запроса.
        """
        return await self._client.run(
            self._client.engine.send_text(node_id, text, declared_cold=declared_cold)
        )

    async def thread(self, node_id: str) -> Thread:
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
        return await self._client.run(self._client.engine.read_thread(node_id))


class AsyncAccountService:
    """Операции с аккаунтом.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self) -> Account:
        """Читает собственный аккаунт: идентификатор, имя и метку языка.

        Балансов не читает - они на другой странице, и брать её ради профиля
        значило бы ходить на площадку дважды за одним ответом.

        Returns:
            Account: Сведения о собственном аккаунте.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_account())

    async def refresh(self) -> Account:
        """Перечитывает собственный аккаунт.

        ДЕЛАЕТ РОВНО ТО ЖЕ, что и get, и это сказано прямо. Кэша у чтения
        аккаунта нет, а значит и обходить нечего: операция объявлена контрактом
        отдельно, и молча свести её к первой значило бы обещать разницу, которой
        нет.

        Returns:
            Account: Сведения о собственном аккаунте.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_account())

    async def health(self) -> SessionHealth:
        """Проверяет пригодность сессии.

        ОТЧИТЫВАЕТСЯ, А НЕ ПАДАЕТ: отказ площадки здесь - это ответ, а не
        происшествие. Результат держится в кэше на объявленный срок.

        Returns:
            SessionHealth: Класс ответа, годность сессии и признак кэша.
        """
        return await self._client.run(self._client.engine.read_health())

    async def capabilities(self) -> CapabilityProfile:
        """Возвращает профиль возможностей.

        Собирается БЕЗ СЕТИ - из того, что уже наблюдалось.

        Returns:
            CapabilityProfile: Состояние каждой возможности контракта.
        """
        return self._client.engine.capability_profile()

    async def balance(self) -> BalancePage:
        """Читает баланс аккаунта и операции по счёту.

        Возвращает ПЕРЕЧЕНЬ балансов, а не одно значение: страница показывает
        три узла значения, по одному на валюту. Кода валюты не даёт ни одному из
        них - страница несёт только знак.

        Returns:
            BalancePage: Балансы полем, операции через `transactions()`.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_balance())


class AsyncLotsService:
    """Операции с лотами.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def showcase(self, user_id: str) -> ShowcasePage:
        """Читает публичную витрину продавца.

        Возвращает то, что видит покупатель: разделы и предложения. Ни признака
        включённости, ни средств правки на витрине нет - для них нужна страница
        управления лотами, которая пока не наблюдалась.

        Args:
            user_id (str): Идентификатор продавца.

        Returns:
            ShowcasePage: Разделы через `sections()`. Полным чтение не
            объявляется ни разу, и признание неполноты требуется всегда.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_showcase(user_id))


class AsyncCatalogService:
    """Операции с каталогом.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def categories(self) -> CatalogPage:
        """Читает каталог: игры, их варианты и разделы каждого.

        Читается только основной список. Избранное повторяет его целиком -
        наблюдено, восемь карточек из восьми, - и новых сведений не даёт.

        Returns:
            CatalogPage: Игры через `games()`.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_catalog())


class AsyncClient:
    """Асинхронный клиент площадки.

    Args:
        secret (Secret | SecretProvider | None): Сессионный секрет либо его
            источник. Не нужен, если передан готовый транспорт.
        settings (TransportSettings | None): Настройки транспорта.
        experimental (frozenset[Capability] | None): Возможности, которые
            вызывающий включает явно, соглашаясь на возможную смену контракта.
        transport (AsyncFetcher | None): Готовый транспорт. Нужен там, где
            вызывающий собирает его сам, и в проверках.
        budget (Budget | None): Общий бюджет запросов. Передаётся, когда в одном
            процессе живут несколько клиентов: площадке видна сетевая
            идентичность, а не то, сколько клиентов мы завели у себя.

    Raises:
        ConfigurationError: Если не передано ни секрета, ни транспорта. Повтор
            здесь не поможет, исправлять надо вызов.
    """

    __slots__ = (
        "_fetcher",
        "account",
        "catalog",
        "chats",
        "engine",
        "lots",
        "orders",
        "pool",
        "reviews",
    )

    def __init__(
        self,
        secret: Secret | SecretProvider | None = None,
        *,
        settings: TransportSettings | None = None,
        experimental: frozenset[Capability] | None = None,
        transport: AsyncFetcher | None = None,
        budget: Budget | None = None,
        proxies: tuple[Proxy, ...] = (),
    ) -> None:
        resolved_settings = settings or TransportSettings()

        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = AsyncFetcher(resolved, settings=resolved_settings)
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
        self.orders = AsyncOrdersService(self)
        self.chats = AsyncChatsService(self)
        self.reviews = AsyncReviewsService(self)
        self.account = AsyncAccountService(self)
        self.lots = AsyncLotsService(self)
        self.catalog = AsyncCatalogService(self)

    async def __aenter__(self) -> AsyncClient:
        """Входит в асинхронный контекстный менеджер.

        Returns:
            AsyncClient: Сам объект.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Закрывает соединения при выходе.

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
        await self._fetcher.close()

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

    async def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
        max_threads_per_step: int = 5,
        concurrency: int = 1,
        on_handler_error: Callable[[HandlerError], None] | None = None,
    ) -> None:
        """Ведёт наблюдение: опрашивает площадку и раздаёт события обработчикам.

        Метод не блокирует поток: между опросами он отдаёт управление циклу
        событий. Сам цикл наблюдения целиком описан ядром и совпадает с
        синхронным до строки.

        Args:
            router (Router): Реестр обработчиков. Обработчики могут быть как
                обычными функциями, так и сопрограммами.
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
            concurrency (int): Сколько ключей упорядочивания раздавать
                одновременно. Единица - последовательно, как в синхронном
                клиенте. Больше единицы означает, что обработчики могут
                выполняться одновременно: счётчик, дописывание в файл или
                соединение с базой перестают быть в единоличном пользовании, и
                просить об этом надо явно. Порядок внутри одного ключа
                сохраняется в любом случае.

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        await self.run(
            self.engine.watch(
                router,
                account_id=account_id,
                max_iterations=max_iterations,
                schedule=schedule,
                state_path=state_path,
                max_threads_per_step=max_threads_per_step,
            ),
            router=router,
            concurrency=concurrency,
        )

    async def run(
        self,
        core: Generator[Request, Reply, T],
        *,
        router: Router | None = None,
        concurrency: int = 1,
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
            concurrency (int): Сколько ключей упорядочивания раздавать
                одновременно.

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
                result: T = stop.value
                return result
            failure = None
            reply = None

            if isinstance(request, Pause):
                if request.ms > 0:
                    await asyncio.sleep(request.ms / 1000)
            elif isinstance(request, Fetch):
                try:
                    reply = await self._fetch(request.path)
                except FunoraError as exc:
                    failure = exc
            elif isinstance(request, Submit):
                # Отправка идёт мимо _fetch нарочно: у записи своё правило -
                # переход в ответ на неё не повторяется.
                try:
                    reply = await self._fetcher.submit(
                        request.path, request.fields, request.headers
                    )
                except FunoraError as exc:
                    failure = exc
            elif isinstance(request, Deliver):
                if router is None:
                    raise ConfigurationError(
                        "ядро просит раздать события, но реестр обработчиков не передан"
                    )
                reply = await adispatch(router, request.events, concurrency=concurrency)
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

    async def _fetch(self, path: str) -> Observation:
        """Выполняет одно обращение к площадке.

        Args:
            path (str): Путь страницы.

        Returns:
            Observation: Результат обращения.

        Raises:
            FunoraError: При сетевом отказе либо непригодном ответе.
        """
        return await self._fetcher.fetch(path)
