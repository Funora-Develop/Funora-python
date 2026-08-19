"""Порождение событий из двух снимков состояния.

Модуль чистый: два снимка на входе, события на выходе. Ни сети, ни часов, ни
состояния между вызовами. Это не про стиль - без такого разделения проверить
поведение на неполном снимке было бы нечем, а именно там оно и опаснее всего.

Три правила объясняют почти весь код, и все три про то, чего модуль **не**
делает.

Сравнение идёт не со вторым снимком, а с курсором - набором того, что уже
известно. Разница не в оформлении. Сравнение снимка со снимком давало ложное
событие: строка, выпавшая из прошлого чтения из-за поломки разметки, при
следующем чтении выглядела новым заказом, и бот выдавал товар по заказу, который
существовал и раньше. Курсор хранит известное и не теряет его от одной
испорченной строки.

Пустой курсор событий не порождает. Не с чем сравнивать, и объявить все
двенадцать существующих заказов новыми означало бы разослать двенадцать
уведомлений об оплате при первом же запуске бота.

Событий об исчезновении не порождается вовсе. Запись, не попавшая в частично
прочитанную страницу, не исчезла - её не прочитали, и отличить одно от другого
по странице нечем. Разница между этими случаями есть разница между «заказ
отменён» и «мы не смогли его увидеть», а обработчик по первому вернёт деньги.

События об изменении статуса не порождаются вовсе. Соответствия классов разметки
статусам не наблюдалось, статус выдаётся ненаблюдённым, и породить из него
событие значило бы выдумать факт.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from hashlib import blake2s
from typing import Any, Final

from ._chats import ChatsPage
from ._orders import OrdersPage
from ._thread import Thread
from .events import FINGERPRINT_FIELDS, ORDERING_KEY, EventType

__all__ = [
    "Event",
    "diff_orders",
    "diff_chats",
    "diff_thread",
    "orders_cursor",
    "chats_cursor",
    "thread_cursor",
]

#: Происхождение события: выведено из разметки, а не из текста.
_STRUCTURAL: Final[str] = "structural"

#: Длина отпечатка события в знаках.
_FINGERPRINT_LEN: Final[int] = 32


@dataclass(frozen=True, slots=True)
class Event:
    """Событие в конверте, описанном спецификацией.

    Attributes:
        id (str): Идентификатор события. Отпечаток от полей, перечисленных в
            спецификации; момента наблюдения и версии адаптера среди них нет.
        type (EventType): Тип события.
        ordering_key (str): Ключ упорядочивания. Порядок сохраняется внутри
            одного ключа, между разными ключами порядка нет.
        entity_id (str): Идентификатор сущности, к которой относится событие.
        observed_at (datetime): Момент наблюдения. В отпечаток не входит.
        origin (str): Как получено: структурно либо по тексту.
        payload (dict[str, Any]): Полезная нагрузка. Персональных данных в ней
            нет: содержимое сообщений и имена сюда не кладутся.
    """

    id: str
    type: EventType
    ordering_key: str
    entity_id: str
    observed_at: datetime
    origin: str
    payload: dict[str, Any]


def _fingerprint(*, account_id: str, event_type: EventType, entity_id: str, revision: str) -> str:
    """Строит устойчивый идентификатор события.

    В отпечаток не входят ни момент наблюдения, ни версия адаптера, и это
    запрет из спецификации, а не выбор реализации. Момент меняется от запуска к
    запуску, версия - при каждом исправлении разметки; включение любого из них
    обнулило бы дедупликацию ровно после перезапуска, то есть там, где она
    нужнее всего.

    Args:
        account_id (str): Идентификатор аккаунта.
        event_type (EventType): Тип события.
        entity_id (str): Идентификатор сущности.
        revision (str): Версия сущности - то, что отличает одно её состояние от
            другого.

    Returns:
        str: Отпечаток в шестнадцатеричном виде.
    """
    parts = {
        "account_id": account_id,
        "type": str(event_type),
        "entity_id": entity_id,
        "entity_revision": revision,
    }
    material = "\x1f".join(parts[name] for name in FINGERPRINT_FIELDS)
    return blake2s(material.encode("utf-8"), digest_size=16).hexdigest()[:_FINGERPRINT_LEN]


def _event(
    *,
    account_id: str,
    event_type: EventType,
    entity_id: str,
    revision: str,
    observed_at: datetime,
    key_field: str,
    payload: dict[str, Any],
) -> Event:
    """Собирает событие с ключом упорядочивания из спецификации.

    Args:
        account_id (str): Идентификатор аккаунта.
        event_type (EventType): Тип события.
        entity_id (str): Идентификатор сущности.
        revision (str): Версия сущности для отпечатка.
        observed_at (datetime): Момент наблюдения.
        key_field (str): Имя подстановки в шаблоне ключа упорядочивания.
        payload (dict[str, Any]): Полезная нагрузка.

    Returns:
        Event: Готовое событие.
    """
    return Event(
        id=_fingerprint(
            account_id=account_id,
            event_type=event_type,
            entity_id=entity_id,
            revision=revision,
        ),
        type=event_type,
        ordering_key=ORDERING_KEY[event_type].format(**{key_field: entity_id}),
        entity_id=entity_id,
        observed_at=observed_at,
        origin=_STRUCTURAL,
        payload=payload,
    )


def diff_orders(
    known: frozenset[str] | None,
    page: OrdersPage,
    *,
    account_id: str,
) -> tuple[Event, ...]:
    """Порождает события по списку заказов и курсору.

    Args:
        known (frozenset[str] | None): Идентификаторы заказов, о которых уже
            известно. None при первом запуске, когда курсора ещё нет.
        page (OrdersPage): Прочитанная страница. Может быть неполной: заказ,
            которого нет в курсоре, новый независимо от полноты чтения.
        account_id (str): Идентификатор аккаунта.

    Returns:
        tuple[Event, ...]: События. Пустой набор при отсутствии курсора:
        сравнивать не с чем, а объявить все существующие заказы новыми означало
        бы разослать уведомления обо всех сразу при первом запуске.
    """
    if known is None:
        return ()

    events: list[Event] = []
    for entry in page.rows(accept_incomplete=True):
        if entry.order_id in known:
            continue
        events.append(
            _event(
                account_id=account_id,
                event_type=EventType.ORDER_CREATED,
                entity_id=entry.order_id,
                # Версией служит сам факт появления: у заказа из списка нет
                # ничего, что менялось бы наблюдаемо. Статус ненаблюдаем, и
                # события о его изменении не порождаются вовсе.
                revision="appeared",
                observed_at=page.observed_at,
                key_field="order_id",
                payload={"href": entry.href, "row_index": entry.row_index},
            )
        )

    return tuple(events)


def orders_cursor(page: OrdersPage) -> frozenset[str]:
    """Собирает курсор по прочитанной странице заказов.

    Курсор снимается только с полного чтения, и решает это вызывающий. Снятый с
    неполного, он потерял бы выпавшие строки - и при следующем чтении они
    выглядели бы новыми заказами.

    Args:
        page (OrdersPage): Прочитанная страница.

    Returns:
        frozenset[str]: Идентификаторы заказов на странице.
    """
    return frozenset(entry.order_id for entry in page.rows(accept_incomplete=True))


def diff_chats(
    known: dict[str, str] | None,
    page: ChatsPage,
    *,
    account_id: str,
) -> tuple[Event, ...]:
    """Порождает события по списку диалогов и курсору.

    Args:
        known (dict[str, str] | None): Позиция последнего сообщения по каждому
            известному диалогу. None при первом запуске.
        page (ChatsPage): Прочитанная страница.
        account_id (str): Идентификатор аккаунта.

    Returns:
        tuple[Event, ...]: События об изменении диалогов.
    """
    if known is None:
        return ()

    events: list[Event] = []
    for entry in page.rows(accept_incomplete=True):
        position = entry.last_message_position.or_none()
        if position is None:
            continue
        if known.get(entry.node_id) == position:
            continue

        events.append(
            _event(
                account_id=account_id,
                event_type=EventType.CHAT_UNREAD_CHANGED,
                entity_id=entry.node_id,
                # Позиция и есть версия диалога: она непрозрачна, но при
                # изменении состояния меняется. Сравнивается только на
                # равенство - арифметика над ней запрещена спецификацией.
                revision=position,
                observed_at=page.observed_at,
                key_field="chat_id",
                payload={
                    "unread": entry.unread.or_none(),
                    "unread_confidence": (
                        str(entry.unread.confidence) if entry.unread.confidence else None
                    ),
                },
            )
        )

    return tuple(events)


def chats_cursor(page: ChatsPage) -> dict[str, str]:
    """Собирает курсор по прочитанной странице диалогов.

    Args:
        page (ChatsPage): Прочитанная страница.

    Returns:
        dict[str, str]: Позиция последнего сообщения по каждому диалогу.
    """
    cursor: dict[str, str] = {}
    for entry in page.rows(accept_incomplete=True):
        position = entry.last_message_position.or_none()
        if position is not None:
            cursor[entry.node_id] = position
    return cursor


def diff_thread(
    known: frozenset[str] | None,
    thread: Thread,
    *,
    account_id: str,
    chat_id: str,
) -> tuple[Event, ...]:
    """Порождает события по переписке и курсору.

    Args:
        known (frozenset[str] | None): Идентификаторы уже известных сообщений.
            None при первом чтении переписки.
        thread (Thread): Прочитанная переписка.
        account_id (str): Идентификатор аккаунта.
        chat_id (str): Идентификатор диалога.

    Returns:
        tuple[Event, ...]: События о новых сообщениях.
    """
    if known is None:
        return ()

    events: list[Event] = []
    for message in thread.messages(accept_incomplete=True):
        if not message.message_id.is_observed:
            continue
        if message.message_id.value in known:
            continue

        events.append(
            _event(
                account_id=account_id,
                event_type=EventType.MESSAGE_CREATED,
                entity_id=chat_id,
                revision=message.message_id.value,
                observed_at=thread.observed_at,
                key_field="chat_id",
                payload={
                    "message_id": message.message_id.value,
                    # Происхождение кладётся в нагрузку намеренно: обработчик
                    # обязан видеть его, не разбирая текст. Но даже origin равный
                    # system не является подтверждением оплаты - об этом сказано
                    # в spec/extraction/chats.yaml и в docstring разбора.
                    "origin": str(message.origin),
                    "external_links": len(message.external_links),
                },
            )
        )

    return tuple(events)


def thread_cursor(thread: Thread) -> frozenset[str]:
    """Собирает курсор по прочитанной переписке.

    Args:
        thread (Thread): Прочитанная переписка.

    Returns:
        frozenset[str]: Идентификаторы наблюдённых сообщений.
    """
    return frozenset(
        message.message_id.value
        for message in thread.messages(accept_incomplete=True)
        if message.message_id.is_observed
    )


def ordering_keys(events: Iterable[Event]) -> set[str]:
    """Собирает ключи упорядочивания набора событий.

    Args:
        events (Iterable[Event]): События.

    Returns:
        set[str]: Ключи. События с разными ключами обрабатываются параллельно.
    """
    return {event.ordering_key for event in events}
