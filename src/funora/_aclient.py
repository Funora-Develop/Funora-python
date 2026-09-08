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
from typing import TYPE_CHECKING, NoReturn, TypeVar

from ._account import BalancePage
from ._budget import Budget
from ._calc import PriceCalculation
from ._catalog import CatalogPage
from ._chat_history import ChatHistory
from ._chats import ChatsPage
from ._chips import ChipsPage
from ._currency_switch import CurrencySwitch
from ._engine import (
    Ask,
    Deliver,
    Engine,
    Fetch,
    Pause,
    Query,
    Reply,
    Request,
    Submit,
    Upload,
    public_read_request,
)
from ._field_schema import FieldSchema
from ._host import host_of
from ._identity import REGISTRY
from ._lot_form import LotForm
from ._market import MarketPage
from ._monitoring import MarketWatch, MonitoringPlan
from ._observed import Observed
from ._order import OrderView
from ._order_details import OrderDetailsBatch
from ._orders import OrdersPage
from ._own_lots import OwnLotsPage
from ._poll import Schedule
from ._proxies import DEFAULT_ACCOUNT, Proxy, ProxyPool
from ._raise import RaiseResult
from ._refund import RefundResult
from ._review_write import ReviewResult
from ._reviews import ReviewsCursor, ReviewsPage
from ._runner import SendResult
from ._secret import Secret, SecretProvider
from ._showcase import ShowcasePage
from ._snapshot import MarketSnapshot
from ._thread import Thread
from ._transport import AsyncFetcher, TransportSettings
from ._viewing import BuyerViewing
from ._watch import Router, adispatch
from ._whoami import Account, CapabilityProfile, SessionHealth
from .capabilities import Capability, CapabilityState
from .errors import ConfigurationError, FunoraError, HandlerError, NotImplementedOperationError
from .operations import OPERATIONS

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

    async def details(
        self, *order_ids: str, include: tuple[str, ...] = ("details", "users")
    ) -> OrderDetailsBatch:
        """Читает подробности заказов пачкой, структурно.

        ЧТО ЭТО ДАЁТ СВЕРХ ЧТЕНИЯ СТРАНИЦЫ: сумму числом, код валюты и
        РАЗДЕЛЕНИЕ покупателя с продавцом. Страница заказа показывает одного
        контрагента и не помечает, на которой стороне вы сами.

        НАБЛЮДЕНО НЕ НАМИ. Ни одного живого ответа этой точки мы не видели;
        состав полей известен от независимой реализации того же протокола.
        Поэтому всё, что может отсутствовать, приходит наблюдением с причиной, а
        состояние - строкой, не приведённой к нашему перечню насильно.

        Args:
            order_ids (str): Идентификаторы заказов, от одного до десяти.
            include (tuple[str, ...]): Какие разделы ответа запрашивать.

        Returns:
            OrderDetailsBatch: Спрошенное, полученное и недостающее - порознь.

        Raises:
            ValidationError: Если пачка пуста, велика либо несёт непригодный
                идентификатор.
            FunoraError: Если ответ непригоден.
        """
        return await self._client.run(
            self._client.engine.read_order_details(tuple(order_ids), include=include)
        )

    async def refund(self, order_id: str) -> RefundResult:
        """Возвращает средства покупателю по заказу.

        ДЕНЬГИ УХОДЯТ ПОКУПАТЕЛЮ, И ВЕРНУТЬ ИХ ОБРАТНО ПЛОЩАДКА НЕ ПРЕДЛАГАЕТ
        НИЧЕМ. Требует явного согласия.

        Перед отправкой читается страница заказа: не показывает площадка формы
        возврата - запрос не уходит вовсе.

        ПОВТОРА НЕТ. При неоднозначном исходе положена сверка - прочитайте
        заказ и посмотрите, - а не второй запрос: второй запрос это второй
        возврат.

        Args:
            order_id (str): Номер заказа.

        Returns:
            RefundResult: Исход. Отказ площадки - тоже исход, и он несёт
            причину текстом. Суммы здесь нет: её не называет ни запрос, ни
            ответ.

        Raises:
            ValidationError: Если номер заказа непригоден.
            UsageError: Если согласия не дано.
            PreconditionFailedError: Если площадка возврата не предлагает.
            FunoraError: Если страница либо ответ непригодны.
        """
        return await self._client.run(self._client.engine.refund_order(order_id))


class AsyncReviewsService:
    """Операции над отзывами.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, user_id: str, *, cursor: ReviewsCursor | str | None = None) -> ReviewsPage:
        """Читает отзывы с профиля продавца.

        Следующую страницу запрашивают с next_cursor предыдущего результата.
        Отсутствие курсора само по себе не означает полноту: проверяйте completeness.

        Args:
            user_id (str): Идентификатор продавца. Тот самый, что стоит в адресе
                профиля.

        Returns:
            ReviewsPage: Разобранная страница. Отзывы выдаются через `rows()`.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_reviews(user_id, cursor=cursor))

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

    async def history_before(
        self, node_id: str, *, before_message_id: str | None = None, cursor: str | None = None
    ) -> ChatHistory:
        """Догружает сообщения переписки СТАРШЕ указанного.

        ЗАПРОС ЗАИМСТВОВАН ЦЕЛИКОМ - и адрес, и оба имени параметров, и форма
        ответа. Своего наблюдения этой точки нет ни одного.

        СОГЛАСИЯ НЕ ТРЕБУЕТ: это чтение, а ошибка чтения на чужом знании видна
        сразу и следа не оставляет.

        НАПРАВЛЕНИЕ СВЕРЯЕТСЯ. Пришедшие идентификаторы обязаны быть строго
        меньше курсора; иначе - отказ, а не молча отданный список.

        Args:
            node_id (str): Идентификатор диалога.
            before_message_id (str | None): Идентификатор сообщения для первого
                запроса назад. Только цифры ASCII; не передаётся вместе с cursor.
            cursor (str | None): Сохранённый next_cursor предыдущей страницы.

        Returns:
            ChatHistory: Догруженные сообщения вместе с признаком конца.

        Raises:
            ValidationError: Если идентификатор либо курсор непригодны.
            CursorIncompatibleError: Если токен несовместим или принадлежит
                другой переписке либо площадка вернула не ту сторону.
            FunoraError: Если ответ непригоден.
        """
        return await self._client.run(
            self._client.engine.read_history_before(
                node_id, before_message_id=before_message_id, cursor=cursor
            )
        )

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
        declared_cold: bool = False,
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
                node_id,
                content,
                filename=filename,
                content_type=content_type,
                declared_cold=declared_cold,
            )
        )

    async def buyer_viewing(self, node_id: str, *buyer_ids: str) -> tuple[BuyerViewing, ...]:
        """Читает, что покупатели смотрят прямо сейчас.

        ЗАЧЕМ ЭТО ПРОДАВЦУ: увидеть, что покупатель, с которым идёт переписка,
        смотрит именно ваш лот - и что именно.

        РАСКОЛ НАБЛЮДЕНИЯ. Подписка наблюдена нами; ответ на неё - нет. Поэтому
        разметка блока сохраняется КАК ЕСТЬ: не разберись наши поля, у вас
        останется то, из чего вы поймёте сами.

        Args:
            node_id (str): Диалог, со страницы которого берётся защитный токен.
            buyer_ids (str): Идентификаторы покупателей.

        Returns:
            tuple[BuyerViewing, ...]: По записи на каждого спрошенного, В ТОМ ЖЕ
            ПОРЯДКЕ. Не ответившие получают наблюдение «не смотрит».

        Raises:
            ValidationError: Если идентификатор непригоден либо перечень пуст.
            FunoraError: Если страница либо ответ непригодны.
        """
        return await self._client.run(
            self._client.engine.read_buyer_viewing(node_id, tuple(buyer_ids))
        )


class AsyncAccountService:
    """Операции с аккаунтом.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def __getattr__(self, name: str) -> NoReturn:
        if name == "withdraw":
            raise NotImplementedOperationError(
                "вывод не реализован: "
                "spec/conformance/not-implemented.yaml#withdraw_stays_unwritten"
            )
        raise AttributeError(name)

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
        return self._client._capability_profile()

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

    async def switch_currency(self, currency: str) -> CurrencySwitch:
        """Меняет валюту, в которой площадка показывает суммы.

        ПОБОЧНОЕ ДЕЙСТВИЕ ГЛОБАЛЬНО: после смены КАЖДАЯ страница отдаёт другие
        числа. Снимки рынка, снятые по разные стороны от смены, сравнивать
        нельзя - сменившейся окажется каждая цена.

        ТРЕБУЕТ ЯВНОГО СОГЛАСИЯ.

        Args:
            currency (str): Код валюты по ISO 4217. Регистр не важен.

        Returns:
            CurrencySwitch: Исход. Вернула площадка окно подтверждения - смены
            НЕ БЫЛО, и подтверждать за вас реализация не станет.

        Raises:
            ValidationError: Если код не из наблюдённого набора.
            UsageError: Если согласия не дано.
            FunoraError: Если страница либо ответ непригодны.
        """
        return await self._client.run(self._client.engine.switch_currency(currency))


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


class AsyncMonitoring:
    """Планирование и наблюдение публичных выдач без авторизации."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def plan(self, *watches: MarketWatch) -> MonitoringPlan:
        """Проверяет прогноз набора вместе с действующими наблюдениями, без HTTP."""
        return self._client._public_engine._budget.monitoring_plan(watches, monotonic())

    async def watch(
        self,
        router: Router,
        *watches: MarketWatch,
        state_path: str | Path | None = None,
        max_iterations: int | None = None,
        on_handler_error: Callable[[HandlerError], None] | None = None,
    ) -> None:
        """Регистрирует набор до выхода из цикла; повторяет сохранённые события.

        Файл состояния отдельный от личного watch. Без файла история живёт
        только в текущем вызове. Набор и интервалы в файле неизменны.
        max_iterations ограничивает число шагов, включая повтор без HTTP.
        """
        engine = self._client._public_engine
        await self._client.run(
            engine.monitor_market(
                watches,
                account_id=self._client._account_id,
                state_path=state_path,
                max_iterations=max_iterations,
            ),
            engine=engine,
            router=router,
            on_handler_error=on_handler_error,
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
        return await self._client._read("market.offers", lambda engine: engine.read_market(node_id))

    async def snapshot(self, node_id: str) -> MarketSnapshot:
        """Снимает состояние выдачи для сравнения во времени.

        Args:
            node_id (str): Номер раздела.

        Returns:
            MarketSnapshot: Снимок. Сравнивать его можно только с другим
            снимком того же запроса - это делает `funora.compare`.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client._read(
            "market.snapshot", lambda engine: engine.read_market_snapshot(node_id)
        )

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
        return await self._client._read("chips.offers", lambda engine: engine.read_chips(node_id))

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

    __slots__ = ("_client", "_lock")

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._lock = asyncio.Lock()

    async def categories(self, *, refresh: bool = False) -> CatalogPage:
        """Читает каталог: игры, их варианты и разделы каждого.

        Читается только основной список. Избранное повторяет его целиком -
        наблюдено, восемь карточек из восьми, - и новых сведений не даёт.

        Returns:
            CatalogPage: Игры через `games()`.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        async with self._lock:
            return await self._client.run(self._client.engine.read_catalog(refresh=refresh))

    async def search(self, query: str) -> CatalogPage:
        """Ищет игры без секрета; games() требует явного принятия неполной выдачи."""
        return await self._client._read(
            "catalog.search", lambda engine: engine.read_catalog_search(query)
        )

    async def field_schema(self, section_id: str) -> FieldSchema:
        """Читает поля фильтра раздела; неполнота требует явного принятия."""
        return await self._client.run(self._client.engine.read_field_schema(section_id))


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
        public_transport (AsyncFetcher | None): Отдельный транспорт рынка без секрета.
            При подставном transport передаётся явно; иначе создаётся лениво.
        public_only (bool): Работа без секрета, только market.offers,
            market.snapshot, market.chips и catalog.search.
            Личный транспорт и файл состояния не принимаются.
        account_id (str): Устойчивый ключ аккаунта для квоты и привязки прокси.
            По умолчанию self: клиенты без ключа делят персональную квоту.
            Ключ не подтверждает авторизацию; её проверяет ответ площадки.
        budget (Budget | None): Общий бюджет запросов. Передаётся, когда в одном
            процессе живут несколько клиентов: площадке видна сетевая
            идентичность, а не то, сколько клиентов мы завели у себя.
        proxies (tuple[Proxy, ...]): Выходы, между которыми распределяются
            аккаунты. Пустой набор означает прямое соединение.
        state_path (str | Path | None): Файл, в котором реестр отправок, реестр
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
        ConfigurationError: Если параметры несовместимы или личному клиенту
            не передано ни секрета, ни транспорта. Повтор
            здесь не поможет, исправлять надо вызов.
    """

    __slots__ = (
        "_fetcher",
        "_public_fetcher",
        "_public_engine",
        "_custom_transport",
        "_closed",
        "account",
        "catalog",
        "chats",
        "engine",
        "lots",
        "market",
        "monitoring",
        "_account_id",
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
        public_transport: AsyncFetcher | None = None,
        public_only: bool = False,
        account_id: str = DEFAULT_ACCOUNT,
        budget: Budget | None = None,
        proxies: tuple[Proxy, ...] = (),
        state_path: str | Path | None = None,
        unsafe_sends_without_ledger: bool = False,
        unsafe_price_changes_without_audit: bool = False,
    ) -> None:
        resolved_settings = settings or TransportSettings()
        if not isinstance(account_id, str) or not account_id.strip():
            raise ConfigurationError("account_id должен быть непустой строкой")
        if public_only and (secret is not None or transport is not None or state_path is not None):
            raise ConfigurationError(
                "public_only не принимает секрет, личный транспорт или файл состояния"
            )
        if public_transport is not None and public_transport is transport:
            raise ConfigurationError("публичный и личный транспорт должны быть разными")
        if isinstance(public_transport, AsyncFetcher) and public_transport._secret is not None:
            raise ConfigurationError("публичный транспорт не должен содержать секрет")
        if not public_only and secret is None and transport is None:
            raise ConfigurationError(
                "клиенту нужен либо секрет, либо готовый транспорт; для рынка есть public_only=True"
            )
        self.pool = ProxyPool(
            proxies, host=host_of(resolved_settings.base_url) or resolved_settings.base_url
        )
        identity_name, proxy_url = self.pool.choose(account_id)
        identity = REGISTRY.get(identity_name)
        if proxy_url is not None:
            resolved_settings = replace(resolved_settings, proxy_url=proxy_url)
        root_budget = budget or identity.budget
        self.engine = Engine(
            resolved_settings,
            budget
            if budget is not None and account_id == DEFAULT_ACCOUNT
            else root_budget.for_account("account:" + account_id),
            experimental or frozenset(),
            identity,
            state_path=state_path,
            unsafe_sends_without_ledger=unsafe_sends_without_ledger,
            unsafe_price_changes_without_audit=unsafe_price_changes_without_audit,
        )
        self._public_engine = Engine(
            resolved_settings,
            root_budget.for_account("public_read"),
            experimental or frozenset(),
            identity,
        )
        self._closed = False
        self._custom_transport = transport is not None
        self._public_fetcher = public_transport
        self._fetcher: AsyncFetcher | None = None
        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = AsyncFetcher(resolved, settings=resolved_settings)

        self.orders = AsyncOrdersService(self)
        self.chats = AsyncChatsService(self)
        self.reviews = AsyncReviewsService(self)
        self.account = AsyncAccountService(self)
        self.lots = AsyncLotsService(self)
        self.catalog = AsyncCatalogService(self)
        self._account_id = account_id
        self.monitoring = AsyncMonitoring(self)
        self.market = AsyncMarketService(self)

    async def _read(
        self, operation: str, build: Callable[[Engine], Generator[Request, Reply, T]]
    ) -> T:
        engine = (
            self._public_engine
            if OPERATIONS[operation].transport_lane == "public_read"
            else self.engine
        )
        return await self.run(build(engine), engine=engine)

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
        self._closed = True
        try:
            if self._fetcher is not None:
                await self._fetcher.close()
        finally:
            if self._public_fetcher is not None:
                await self._public_fetcher.close()

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
        engine = self._public_engine if self._fetcher is None else self.engine
        return engine._state.locale

    @property
    def stopped(self) -> FunoraError | None:
        """Возвращает ошибку, остановившую клиента.

        Полная остановка наступает по признаку fail_closed у политики повторов:
        сегодня это отказ в доступе и страница проверки. Обе - ответ площадки
        на поведение клиента, а не сбой связи.

        Returns:
            FunoraError | None: Ошибка либо None, если клиент работает.
        """
        engine = self._public_engine if self._fetcher is None else self.engine
        return engine.stopped

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
        self._public_engine.resume()

    def _capability_profile(self) -> CapabilityProfile:
        profile = self.engine.capability_profile()
        public = self._public_engine.capability_profile()
        return replace(
            profile,
            observed_at=max(profile.observed_at, public.observed_at),
            _evaluations={
                capability: public.evaluation_of(capability)
                if (operation := OPERATIONS.get(capability.value))
                and operation.transport_lane == "public_read"
                else evaluation
                for capability, evaluation in profile.evaluations().items()
            },
        )

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.
        """
        operation = OPERATIONS.get(capability.value)
        engine = (
            self._public_engine
            if operation and operation.transport_lane == "public_read"
            else self.engine
        )
        return engine.capability(capability)

    async def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: str | Path | None = None,
        max_threads_per_step: int = 5,
        use_channel: bool = True,
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
            state_path (str | Path | None): Файл, в котором состояние гашения повторов
                переживает перезапуск.
            use_channel (bool): Слушать ли канал обновлений площадки.

                ПО УМОЛЧАНИЮ ДА. Канал отвечает за секунды; опрос страниц
                замечал изменение от трёх секунд до двух минут. События при этом
                по-прежнему собираются чтением страниц - из канала берётся одно
                решение «изменилось или нет», - и достоверность не меняется.

                Выключение возвращает прежнее поведение целиком.
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
                use_channel=use_channel,
            ),
            router=router,
            concurrency=concurrency,
            on_handler_error=on_handler_error,
        )

    async def run(
        self,
        core: Generator[Request, Reply, T],
        *,
        engine: Engine | None = None,
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
            engine (Engine | None): Принадлежащее клиенту ядро выбранной полосы.
                По умолчанию личное; публичное допускает только чтение рынка.
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
        active = self.engine if engine is None else engine
        if self._closed or active not in (self.engine, self._public_engine):
            core.close()
            raise ConfigurationError("клиент закрыт либо ядро принадлежит другому клиенту")
        if active is self._public_engine and self._public_fetcher is None:
            if self._custom_transport:
                core.close()
                raise ConfigurationError(
                    "для подставного клиента передайте отдельный public_transport"
                )
            self._public_fetcher = AsyncFetcher(None, settings=active._settings)
        fetcher = self._public_fetcher if active is self._public_engine else self._fetcher
        if fetcher is None:
            core.close()
            raise ConfigurationError("public_only разрешает только операции публичной полосы")
        reply: Reply = None
        failure: FunoraError | None = None
        try:
            while True:
                try:
                    request = core.throw(failure) if failure is not None else core.send(reply)
                except StopIteration as stop:
                    result: T = stop.value
                    return result
                except FunoraError as exc:
                    active.note_operation_error(exc)
                    raise
                if active is self._public_engine and not public_read_request(request):
                    core.close()
                    raise ConfigurationError("публичная полоса допускает только чтение")
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
                        reply = await (
                            self._fetch(request.path)
                            if active is self.engine
                            else fetcher.fetch(request.path)
                        )
                    except FunoraError as exc:
                        failure = exc
                elif isinstance(request, Submit):
                    # Отправка идёт мимо _fetch нарочно: у записи своё правило -
                    # переход в ответ на неё не повторяется.
                    try:
                        reply = await fetcher.submit(request.path, request.fields, request.headers)
                    except FunoraError as exc:
                        failure = exc
                elif isinstance(request, Upload):
                    # Загрузка идёт мимо _fetch по той же причине, что и отправка
                    # формы: переход в ответ на запись не повторяется.
                    try:
                        reply = await fetcher.upload(
                            request.path,
                            field=request.field,
                            filename=request.filename,
                            content=request.content,
                            content_type=request.content_type,
                            headers=request.headers,
                        )
                    except FunoraError as exc:
                        failure = exc
                elif isinstance(request, Query):
                    # Структурный вопрос идёт мимо _fetch: тело у него JSON, а не
                    # поля формы. Правило перехода при этом ЧТЕНИЯ, а не записи -
                    # повтор здесь безвреден.
                    try:
                        reply = await fetcher.query(request.path, request.payload, request.headers)
                    except FunoraError as exc:
                        failure = exc
                elif isinstance(request, Ask):
                    # Вопрос методом GET с ответом объектом. Мимо _fetch: переходы
                    # здесь не выполняются - переход отсюда означает не «страница
                    # переехала», а «нас выкинуло на страницу», и разбирать её как
                    # объект нельзя.
                    try:
                        reply = await fetcher.ask(request.path, request.headers)
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
        finally:
            core.close()

    async def _fetch(self, path: str) -> Observation:
        """Выполняет одно обращение к площадке.

        Args:
            path (str): Путь страницы.

        Returns:
            Observation: Результат обращения.

        Raises:
            FunoraError: При сетевом отказе либо непригодном ответе.
        """
        if self._fetcher is None:
            raise ConfigurationError("личный транспорт недоступен")
        return await self._fetcher.fetch(path)
