"""Возможности адаптера и их состояния.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/capabilities.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Состояний пять, и разница между ними не косметическая. Вызов блокируется
только при unsupported, то есть при позитивном свидетельстве отсутствия.
При unknown вызов выполняется оптимистично: неудачная проверка не
доказывает, что возможности нет, и блокировать по ней значило бы
запрещать работу из-за собственной неуверенности.

Признаки usable и opt_in_required вынесены в код намеренно. Именно из них
выводится решение «звать или отказать», и если каждая из шести реализаций
выведет его сама, они разойдутся в поведении, оставаясь согласными в
названиях.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, NoReturn

__all__ = ["CapabilityState", "Capability", "CAPABILITY_SOURCE", "CAPABILITY_INITIAL"]


class CapabilityState(StrEnum):
    """Состояние возможности в текущем сеансе.

    supported:
        Возможность подтверждена и доступна.
    unsupported:
        Возможность отсутствует по позитивному свидетельству. Вызов завершается
        UnsupportedCapabilityError.
    experimental:
        Возможность реализована, но контракт может измениться. Требует явного включения;
        без него вызов завершается ExperimentalCapabilityError.
    degraded:
        Возможность работает, но адаптер наблюдает частичную деградацию извлечения на
        связанной странице. Вызов разрешён, результат может быть неполным, и это
        отражено в поле completeness результата. Состояние существует, чтобы
        отличить «работает не полностью» от «не работает»: без него оба случая
        пришлось бы кодировать одним значением, и на такое состояние невозможно
        написать тест.
    unknown:
        Состояние не установлено. Вызов НЕ блокируется: выполняется оптимистично, и при
        отказе пользователь получает типизированную ошибку. Молчаливый уход в
        else-ветку невозможен по построению.
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    EXPERIMENTAL = "experimental"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"

    @property
    def usable(self) -> bool:
        """Сообщает, можно ли звать возможность без дополнительных условий.

        Отвечает на вопрос «можно ли звать прямо сейчас», а не «работает ли
        возможность вообще». У состояния experimental возможность работает,
        но звать её без явного включения нельзя, поэтому здесь False.

        Returns:
            bool: True, если вызов разрешён без включения.
        """
        return self in _USABLE

    @property
    def opt_in_required(self) -> bool:
        """Сообщает, требуется ли явное согласие вызывающего.

        Returns:
            bool: True, если без включения вызов отклоняется.
        """
        return self in _OPT_IN

    def allows_call(self, *, opted_in: bool) -> bool:
        """Решает, разрешён ли вызов в этом состоянии.

        Правило взято из predicates в spec/capabilities.yaml, где оно
        объявлено нормативным. Выводить его заново в каждой реализации
        нельзя: шесть SDK выведут шесть разных решений, оставаясь
        согласными в названиях состояний.

        Args:
            opted_in (bool): Включил ли вызывающий возможность явно.

        Returns:
            bool: True, если вызов разрешён.
        """
        return self in _USABLE or (opted_in and self in _OPT_IN)

    def __bool__(self) -> NoReturn:
        """Запрещает приведение к булеву значению.

        Состояний пять, и к двум они не сводятся. Запись
        ``if client.capability(cap): call()`` выглядит настолько
        естественно, что её пишут не задумываясь, - а состояние это
        строка, и любая непустая строка истинна. Проверка пропускала
        вызов при unsupported: ровно тот случай, ради которого она и
        написана.

        Тот же запрет стоит у Observed и по той же причине.

        Raises:
            TypeError: Всегда.
        """
        raise TypeError(
            "CapabilityState нельзя привести к булеву значению: состояний "
            "пять, а не два, и unsupported истинно как непустая строка. "
            "Спросите allows_call(opted_in=...), если нужен факт "
            "допустимости вызова, либо сравните состояние с нужным"
        )


#: Состояния, в которых вызов разрешён без дополнительных условий.
_USABLE: Final[frozenset[CapabilityState]] = frozenset(
    {
        CapabilityState.SUPPORTED,
        CapabilityState.DEGRADED,
        CapabilityState.UNKNOWN,
    }
)

#: Состояния, требующие явного включения вызывающим.
_OPT_IN: Final[frozenset[CapabilityState]] = frozenset(
    {
        CapabilityState.EXPERIMENTAL,
    }
)


class Capability(StrEnum):
    """Возможность адаптера.

    Значение члена совпадает с именем возможности в спецификации, поэтому
    перечисление пригодно и для сравнения, и для записи в журнал.
    """

    #: Чтение профиля аккаунта, от имени которого работает клиент.
    ACCOUNT_PROFILE = "account.profile"
    #: Чтение баланса аккаунта.
    ACCOUNT_BALANCE = "account.balance"
    #: Чтение дерева разделов каталога.
    CATALOG_CATEGORIES = "catalog.categories"
    #: Чтение схемы полей конкретной подкатегории.
    CATALOG_FIELD_SCHEMA = "catalog.field_schema"
    #: Чтение публичных предложений по подкатегории.
    MARKET_OFFERS = "market.offers"
    #: Снимок рынка для сравнения и мониторинга.
    MARKET_SNAPSHOT = "market.snapshot"
    #: Чтение собственных предложений продавца.
    LOTS_LIST_OWN = "lots.list_own"
    #: Включение лота в выдачу.
    LOTS_ACTIVATE = "lots.activate"
    #: Снятие лота с выдачи.
    LOTS_DEACTIVATE = "lots.deactivate"
    #: Изменение цены лота.
    LOTS_UPDATE_PRICE = "lots.update_price"
    #: Поднятие лота в выдаче.
    LOTS_PROMOTE = "lots.promote"
    #: Чтение списка заказов.
    ORDERS_LIST = "orders.list"
    #: Чтение конкретного заказа.
    ORDERS_GET = "orders.get"
    #: Распознавание событий заказа из канала обновлений.
    ORDERS_EVENTS = "orders.events"
    #: Чтение списка личных переписок.
    CHATS_LIST = "chats.list"
    #: Чтение недавней истории переписки.
    CHATS_HISTORY = "chats.history"
    #: Догрузка более старых сообщений через курсор.
    CHATS_HISTORY_PAGINATION = "chats.history_pagination"
    #: Отправка текстового сообщения.
    CHATS_SEND_TEXT = "chats.send_text"
    #: Отправка изображения или вложения.
    CHATS_SEND_IMAGE = "chats.send_image"
    #: Отметка переписки прочитанной.
    CHATS_MARK_READ = "chats.mark_read"
    #: Чтение отзывов по заказу или профилю.
    REVIEWS_GET = "reviews.get"
    #: Локали интерфейса, на которых адаптер умеет распознавать системные тексты. Не булева
    #: возможность: значением является множество локалей.
    PROTOCOL_LOCALE = "protocol.locale"


#: Откуда берётся состояние возможности.
#:
#: static - известно из спецификации, probe - выясняется проверкой,
#: derived - выводится из состояния других возможностей.
CAPABILITY_SOURCE: Final[dict[Capability, str]] = {
    Capability.ACCOUNT_PROFILE: "static",
    Capability.ACCOUNT_BALANCE: "probe",
    Capability.CATALOG_CATEGORIES: "static",
    Capability.CATALOG_FIELD_SCHEMA: "probe",
    Capability.MARKET_OFFERS: "static",
    Capability.MARKET_SNAPSHOT: "derived",
    Capability.LOTS_LIST_OWN: "static",
    Capability.LOTS_ACTIVATE: "probe",
    Capability.LOTS_DEACTIVATE: "probe",
    Capability.LOTS_UPDATE_PRICE: "probe",
    Capability.LOTS_PROMOTE: "probe",
    Capability.ORDERS_LIST: "static",
    Capability.ORDERS_GET: "static",
    Capability.ORDERS_EVENTS: "probe",
    Capability.CHATS_LIST: "static",
    Capability.CHATS_HISTORY: "static",
    Capability.CHATS_HISTORY_PAGINATION: "probe",
    Capability.CHATS_SEND_TEXT: "static",
    Capability.CHATS_SEND_IMAGE: "probe",
    Capability.CHATS_MARK_READ: "probe",
    Capability.REVIEWS_GET: "probe",
    Capability.PROTOCOL_LOCALE: "static",
}

#: Состояние возможности до первой проверки.
#:
#: Начальное значение unknown означает «ещё не выяснено», а не «нет».
#: Разница определяет, будет вызов выполнен или отклонён.
CAPABILITY_INITIAL: Final[dict[Capability, CapabilityState]] = {
    Capability.ACCOUNT_PROFILE: CapabilityState.SUPPORTED,
    Capability.ACCOUNT_BALANCE: CapabilityState.UNKNOWN,
    Capability.CATALOG_CATEGORIES: CapabilityState.SUPPORTED,
    Capability.CATALOG_FIELD_SCHEMA: CapabilityState.UNKNOWN,
    Capability.MARKET_OFFERS: CapabilityState.SUPPORTED,
    Capability.MARKET_SNAPSHOT: CapabilityState.SUPPORTED,
    Capability.LOTS_LIST_OWN: CapabilityState.SUPPORTED,
    Capability.LOTS_ACTIVATE: CapabilityState.UNKNOWN,
    Capability.LOTS_DEACTIVATE: CapabilityState.UNKNOWN,
    Capability.LOTS_UPDATE_PRICE: CapabilityState.UNKNOWN,
    Capability.LOTS_PROMOTE: CapabilityState.UNKNOWN,
    Capability.ORDERS_LIST: CapabilityState.SUPPORTED,
    Capability.ORDERS_GET: CapabilityState.SUPPORTED,
    Capability.ORDERS_EVENTS: CapabilityState.UNKNOWN,
    Capability.CHATS_LIST: CapabilityState.SUPPORTED,
    Capability.CHATS_HISTORY: CapabilityState.SUPPORTED,
    Capability.CHATS_HISTORY_PAGINATION: CapabilityState.UNKNOWN,
    Capability.CHATS_SEND_TEXT: CapabilityState.SUPPORTED,
    Capability.CHATS_SEND_IMAGE: CapabilityState.UNKNOWN,
    Capability.CHATS_MARK_READ: CapabilityState.UNKNOWN,
    Capability.REVIEWS_GET: CapabilityState.UNKNOWN,
    Capability.PROTOCOL_LOCALE: CapabilityState.SUPPORTED,
}
