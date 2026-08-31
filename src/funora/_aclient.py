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
from collections.abc import Awaitable, Callable, Generator
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, TypeVar

from ._account import BalancePage
from ._budget import Budget
from ._calc import PriceCalculation
from ._catalog import CatalogPage
from ._chats import ChatsPage
from ._chips import ChipsPage
from ._engine import Deliver, Engine, Fetch, Pause, Reply, Request, Submit, Upload
from ._host import host_of
from ._identity import REGISTRY
from ._lot_form import LotForm
from ._market import MarketPage
from ._observed import Observed
from ._order import OrderView
from ._orders import OrdersPage
from ._own_lots import OwnLotsPage
from ._poll import Schedule
from ._proxies import DEFAULT_ACCOUNT, Proxy, ProxyPool
from ._raise import RaiseResult
from ._review_write import ReviewResult
from ._reviews import ReviewsPage
from ._runner import SendResult
from ._secret import Secret, SecretProvider
from ._showcase import ShowcasePage
from ._snapshot import MarketSnapshot
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

    async def leave(self, order_id: str, *, rating: int, text: str = "") -> ReviewResult:
        """Пишет отзыв к заказу либо правит уже написанный.

        ТРЕБУЕТ ЯВНОГО СОГЛАСИЯ: состав полей запроса известен от независимой
        реализации того же протокола. Отзыв виден покупателю и всем посетителям
        профиля.

        Args:
            order_id (str): Номер заказа.
            rating (int): Оценка от одного до пяти.
            text (str): Текст отзыва. Пустой допустим.

        Returns:
            ReviewResult: Исход. Поле applied означает «подтверждено», а не
            «получилось»: ложь требует посмотреть заказ, а не повторить вслепую.

        Raises:
            ValidationError: Если номер либо оценка непригодны.
            UsageError: Если согласия не дано.
            FunoraError: Если страница либо ответ непригодны.
        """
        return await self._client.run(
            self._client.engine.leave_review(order_id, rating=rating, text=text)
        )

    async def remove(self, order_id: str) -> ReviewResult:
        """Снимает свой отзыв к заказу.

        ПРЕЖНЕГО ТЕКСТА НИКТО НЕ ВЕРНЁТ. Прочитайте отзыв прежде, если он вам
        нужен: реализация его не сохраняет.

        Args:
            order_id (str): Номер заказа.

        Returns:
            ReviewResult: Исход. Подтверждением служит отсутствие оценки в
            перерисованном виджете.

        Raises:
            ValidationError: Если номер непригоден.
            UsageError: Если согласия не дано.
            FunoraError: Если страница либо ответ непригодны.
        """
        return await self._client.run(self._client.engine.remove_review(order_id))


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

    async def mark_read(self, node_id: str) -> None:
        """Помечает диалог прочитанным.

        ОТДЕЛЬНОГО ЗАПРОСА У ЭТОГО ДЕЙСТВИЯ НЕТ: диалог помечается прочитанным
        тем, что его узел попал в подписку обычного опроса канала обновлений.

        ТРЕБУЕТ ЯВНОГО СОГЛАСИЯ. Форма запроса наша, а вывод о том, что подписка
        снимает пометку непрочитанного, - от независимой реализации того же
        протокола. Проверить его мы не могли: непрочитанность видна у
        покупателя, а не у нас.

        Args:
            node_id (str): Числовой идентификатор диалога.

        Returns:
            None: Подтверждения площадка не даёт, и выдумывать его нечем.

        Raises:
            ValidationError: Если идентификатор непригоден.
            UsageError: Если согласия не дано.
            FunoraError: Если страница диалога непригодна.
        """
        await self._client.run(self._client.engine.mark_chat_read(node_id))

    async def send_image(
        self,
        node_id: str,
        content: bytes,
        *,
        filename: str,
        content_type: str = "image/png",
    ) -> SendResult:
        """Отправляет изображение в переписку.

        ДВА ШАГА, И ОБА НАБЛЮДЕНЫ НАМИ: файл уходит отдельным обращением и
        получает номер, затем номер отправляется обычным действием канала.
        Чужого знания здесь нет, и согласия операция не спрашивает.

        ПОБОЧНОЕ ДЕЙСТВИЕ ТО ЖЕ, ЧТО У ОТПРАВКИ ТЕКСТА: переписка помечается
        прочитанной. Иначе ответ канала не подтвердит отправку.

        Args:
            node_id (str): Числовой идентификатор диалога.
            content (bytes): Содержимое файла.
            filename (str): Имя файла, как его увидит площадка.
            content_type (str): Тип содержимого.

        Returns:
            SendResult: Исход, причина и прочитанное из ответа.

        Raises:
            ValidationError: Если идентификатор, имя либо содержимое непригодны.
            UsageError: Если файл больше объявленного площадкой предела.
            FunoraError: Если страница непригодна либо ответ загрузки непонятен.
        """
        return await self._client.run(
            self._client.engine.send_image(
                node_id, content, filename=filename, content_type=content_type
            )
        )


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

    async def form(self, node_id: str, offer_id: str) -> LotForm:
        """Читает форму правки одного предложения.

        ЕДИНСТВЕННОЕ МЕСТО, где виден признак показа лота в выдаче.

        Args:
            node_id (str): Идентификатор раздела.
            offer_id (str): Идентификатор предложения.

        Returns:
            LotForm: Прочитанная форма.

        Raises:
            ValidationError: Если идентификатор непригоден.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_lot_form(node_id, offer_id))

    async def update_price(
        self, node_id: str, offer_id: str, price: str, *, expected_revision: str
    ) -> LotForm:
        """Меняет цену предложения, не трогая ничего другого.

        Args:
            node_id (str): Идентификатор раздела.
            offer_id (str): Идентификатор предложения.
            price (str): Новая цена.
            expected_revision (str): Отпечаток, полученный через `form()`.

        Returns:
            LotForm: Форма, перечитанная после сохранения.

        Raises:
            PreconditionFailedError: Если лот успели изменить.
            UsageError: Если лот выключен либо отпечаток не передан.
            ConfigurationError: Если долговечного журнала правок нет.
            FunoraError: Если сохранение не состоялось.
        """
        return await self._client.run(
            self._client.engine.update_price(
                node_id, offer_id, price, expected_revision=expected_revision
            )
        )

    async def list_own(self, node_id: str) -> OwnLotsPage:
        """Читает собственные лоты продавца в одном разделе.

        РАДИ ИДЕНТИФИКАТОРА ПРЕДЛОЖЕНИЯ. Витрина показывает те же лоты и даже
        больше полей - количество и признак автовыдачи, - но идентификатора не
        даёт: там он лежит в строке запроса ссылки.

        ПРИЗНАКА ПОКАЗА ЛОТА В ВЫДАЧЕ ЗДЕСЬ НЕТ, и это не пробел разбора: его
        нет на самой странице. Все строки структурно одинаковы, различающего
        признака ни одного, а узел с говорящим именем .tc-visible-inside есть и
        на публичной витрине - значит признаком видимости он быть не может.

        Args:
            node_id (str): Номер раздела. Управление лотами живёт по одному
                адресу на раздел, а не по одному на аккаунт.

        Returns:
            OwnLotsPage: Лоты раздела и доводы кнопки поднятия.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_own_lots(node_id))

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

    async def promote(self, game_id: str, node_id: str) -> RaiseResult:
        """Поднимает в выдаче ВСЕ предложения раздела.

        НЕОБРАТИМО И ТРАТИТ СУТОЧНЫЙ ПРЕДЕЛ. Повтора нет: при неоднозначном
        исходе положена сверка, а не второй запрос.

        Args:
            game_id (str): Игра. Атрибут data-game у кнопки поднятия.
            node_id (str): Раздел. Атрибут data-node у той же кнопки.

        Returns:
            RaiseResult: Исход. Отказ площадки - тоже исход, и он несёт срок
            следующего поднятия.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.promote_lots(game_id, node_id))

    async def activate(self, node_id: str, offer_id: str, *, expected_revision: str) -> LotForm:
        """Включает лот в выдачу.

        ТРЕБУЕТ ЯВНОГО СОГЛАСИЯ. Вид запроса при снятом флажке нами не
        наблюдался - он известен от независимой реализации того же протокола.
        Без включённой возможности `lots.activate` операция отказывает до сети.

        Args:
            node_id (str): Идентификатор раздела.
            offer_id (str): Идентификатор предложения.
            expected_revision (str): Отпечаток, полученный чтением формы.
                Обязателен: уходит вся форма, и без него параллельная правка
                перетёрла бы описание лота.

        Returns:
            LotForm: Форма, перечитанная после сохранения. Состояние показа в
            ней сверено с тем, которого просили.

        Raises:
            UsageError: Если согласия не дано либо отпечаток не передан.
            PreconditionFailedError: Если лот успели изменить.
            FunoraError: Если сохранение не состоялось.
        """
        return await self._client.run(
            self._client.engine.set_lot_visible(
                node_id, offer_id, visible=True, expected_revision=expected_revision
            )
        )

    async def deactivate(self, node_id: str, offer_id: str, *, expected_revision: str) -> LotForm:
        """Снимает лот с выдачи - продажи по нему прекращаются.

        ТРЕБУЕТ ЯВНОГО СОГЛАСИЯ. Вид запроса при снятом флажке нами не
        наблюдался - он известен от независимой реализации того же протокола.
        Без включённой возможности `lots.deactivate` операция отказывает до сети.

        Args:
            node_id (str): Идентификатор раздела.
            offer_id (str): Идентификатор предложения.
            expected_revision (str): Отпечаток, полученный чтением формы.
                Обязателен: уходит вся форма, и без него параллельная правка
                перетёрла бы описание лота.

        Returns:
            LotForm: Форма, перечитанная после сохранения. Состояние показа в
            ней сверено с тем, которого просили.

        Raises:
            UsageError: Если согласия не дано либо отпечаток не передан.
            PreconditionFailedError: Если лот успели изменить.
            FunoraError: Если сохранение не состоялось.
        """
        return await self._client.run(
            self._client.engine.set_lot_visible(
                node_id, offer_id, visible=False, expected_revision=expected_revision
            )
        )

    async def calculate_prices(self, node_id: str, price: str) -> PriceCalculation:
        """Считает, сколько заплатит покупатель за названную цену продавца.

        ЦЕНА ПРОДАВЦА И ЦЕНА ПОКУПАТЕЛЯ - РАЗНЫЕ ВЕЛИЧИНЫ: между ними комиссия
        площадки, и зависит она от способа оплаты.

        Args:
            node_id (str): Идентификатор раздела.
            price (str): Цена продавца, как её пишут в поле.

        Returns:
            PriceCalculation: Способы оплаты и цены покупателя при них. Цены
            текстом: разделитель дробной части нам не наблюдался.

        Raises:
            ValidationError: Если цена пуста либо раздел непригоден.
            FunoraError: Если ответ непригоден.
        """
        return await self._client.run(
            self._client.engine.calculate_prices(node_id=node_id, price=price)
        )


class AsyncMarketService:
    """Публичные предложения раздела.

    То, что видит ПОКУПАТЕЛЬ. Вход в переоценку: прочитать цены соседей,
    решить, поменять свою через `lots.update_price`.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def offers(self, node_id: str) -> MarketPage:
        """Читает публичный список предложений раздела.

        Args:
            node_id (str): Номер раздела. Тот самый, что стоит в адресе.

        Returns:
            MarketPage: Разобранный список. Предложения выдаются через
            `offers()`, и неполноту он требует признать: неполный список
            неотличим от короткого, а решение о цене по нему - неверное.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_market(node_id))

    async def snapshot(self, node_id: str) -> MarketSnapshot:
        """Снимает состояние выдачи для сравнения во времени.

        Args:
            node_id (str): Номер раздела.

        Returns:
            MarketSnapshot: Снимок. Сравнивать его можно только с другим
            снимком того же запроса - это делает `funora.market.compare`.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_market_snapshot(node_id))

    async def chips(self, node_id: str) -> ChipsPage:
        """Читает публичные предложения раздела ЧИПОВ - второго рынка.

        Здесь продаётся количество, а не вещь: цена стоит за единицу, описания
        у предложения нет.

        Args:
            node_id (str): Номер раздела чипов.

        Returns:
            ChipsPage: Разобранный список.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_chips(node_id))

    async def calculate_chip_prices(self, game_id: str, price: str) -> PriceCalculation:
        """Считает цену покупателя на рынке по количеству.

        ДОВОД ЗДЕСЬ - ИГРА, А НЕ РАЗДЕЛ, и это отличие от обычных лотов. У чипов
        на странице лежат оба, и который ждёт площадка - мы не проверяли.

        Args:
            game_id (str): Идентификатор игры.
            price (str): Цена продавца, как её пишут в поле.

        Returns:
            PriceCalculation: Способы оплаты и цены покупателя при них.

        Raises:
            ValidationError: Если цена пуста либо игра непригодна.
            FunoraError: Если ответ непригоден.
        """
        return await self._client.run(
            self._client.engine.calculate_prices(game_id=game_id, price=price)
        )


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
        proxies (tuple[Proxy, ...]): Выходы, между которыми распределяются
            аккаунты. Пустой набор означает прямое соединение.
        state_path (Path | None): Файл, в котором реестр отправок, реестр
            выданного и журнал правок цены переживают перезапуск. Без него
            отправка и правка цены ОТКАЗЫВАЮТ: обе защиты держатся памятью
            процесса, а память обнуляется.
        unsafe_sends_without_ledger (bool): Разрешает отправку без долговечного
            реестра. Оставляет отметку в состоянии здоровья: снять защиту
            можно, снять её незаметно нельзя.
        unsafe_price_changes_without_audit (bool): Разрешает правку цены без
            долговечного журнала. Отметку оставляет так же. Цена послабления
            здесь - потерянная прежняя цена: истории цен у площадки нет.

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
        "market",
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
        state_path: Path | None = None,
        unsafe_sends_without_ledger: bool = False,
        unsafe_price_changes_without_audit: bool = False,
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
            state_path=state_path,
            unsafe_sends_without_ledger=unsafe_sends_without_ledger,
            unsafe_price_changes_without_audit=unsafe_price_changes_without_audit,
        )
        self.orders = AsyncOrdersService(self)
        self.chats = AsyncChatsService(self)
        self.reviews = AsyncReviewsService(self)
        self.account = AsyncAccountService(self)
        self.lots = AsyncLotsService(self)
        self.catalog = AsyncCatalogService(self)
        self.market = AsyncMarketService(self)

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
            on_handler_error=on_handler_error,
        )

    async def run(
        self,
        core: Generator[Request, Reply, T],
        *,
        router: Router | None = None,
        concurrency: int = 1,
        on_handler_error: Callable[[HandlerError], None] | None = None,
        on_idle: Callable[[int], object] | None = None,
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
            on_handler_error (Callable[[HandlerError], None] | None): Что делать
                с отказом обработчика. Причина отказа живёт только здесь.
            on_idle (Callable[[int], None] | None): Что делать в паузе между
                опросами. Вызывается ДО сна и получает длительность паузы в
                миллисекундах; потраченное вычитается из сна.

                Крючок объявлен и у синхронного клиента, и обещание у обоих
                одно. Обещание это держится не само собой: watch однажды уже
                принимал on_handler_error и не передавал его дальше - у
                синхронного клиента отказ обработчика доходил до вызывающего, у
                асинхронного пропадал молча.

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
                spent = 0.0
                if on_idle is not None:
                    started = monotonic()
                    # Сопрограмму НАДО ДОЖДАТЬСЯ. Прежде она вызывалась и не
                    # ожидалась: возвращённая сопрограмма выбрасывалась, тело
                    # крючка не выполнялось ни разу, и Python сообщал об этом
                    # предупреждением в поток ошибок - то есть никак.
                    #
                    # Обещание у двух фасадов одно, и держаться оно обязано в
                    # обе стороны: обычная функция здесь работает так же.
                    outcome = on_idle(request.ms)
                    if isinstance(outcome, Awaitable):
                        await outcome
                    spent = (monotonic() - started) * 1000
                remaining = request.ms - spent
                if remaining > 0:
                    await asyncio.sleep(remaining / 1000)
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
            elif isinstance(request, Upload):
                # Загрузка идёт мимо _fetch по той же причине, что и отправка
                # формы: переход в ответ на запись не повторяется.
                try:
                    reply = await self._fetcher.upload(
                        request.path,
                        field=request.field,
                        filename=request.filename,
                        content=request.content,
                        content_type=request.content_type,
                        headers=request.headers,
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
