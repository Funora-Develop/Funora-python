r"""Типы событий и вывод ключа упорядочивания.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/events/delivery.yaml в репозитории Funora-spec.
Перестроить: .venv\Scripts\python.exe tools/codegen.py

Правило вывода ключа упорядочивания нормативно. Две реализации,
выведшие разные ключи, получат разную степень параллелизма и разный
наблюдаемый порядок - при полном согласии в том, какие события бывают.

Поля, запрещённые в отпечатке события, перечислены здесь же. Момент
наблюдения и версия адаптера меняются от запуска к запуску и от релиза
к релизу; включение любого из них обнулит дедупликацию ровно там, где
она нужнее всего - после перезапуска.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "EventType",
    "ORDERING_KEY",
    "FINGERPRINT_FIELDS",
    "FINGERPRINT_SEPARATOR",
    "FINGERPRINT_HASH",
    "FINGERPRINT_DIGEST_BYTES",
    "FINGERPRINT_LENGTH",
    "MIN_ENTRIES_PER_KEY",
    "EVENT_LANE",
    "REVISION_APPEARED",
    "REVISION_SEPARATOR",
    "LANE_DROPPABLE",
    "DEDUP_TTL_MS",
]


class EventType(StrEnum):
    """Тип события.

    Значение совпадает с именем типа в спецификации: оно уходит в журнал
    и в конверт события, где обязано совпадать между всеми реализациями.
    """

    MESSAGE_CREATED = "message.created"
    CHAT_UNREAD_CHANGED = "chat.unread_changed"
    ORDER_CREATED = "order.created"
    ORDER_STATUS_CHANGED = "order.status_changed"
    REVIEW_CHANGED = "review.changed"
    LOT_PRICE_CHANGED = "lot.price_changed"
    LOT_STOCK_CHANGED = "lot.stock_changed"
    MARKET_OFFER_APPEARED = "market.offer_appeared"
    MARKET_OFFER_DISAPPEARED = "market.offer_disappeared"
    MARKET_PRICE_CHANGED = "market.price_changed"
    SELLER_ONLINE_CHANGED = "seller.online_changed"
    PROTOCOL_HEALTH_CHANGED = "protocol.health_changed"
    WATCH_PRIMED = "watch.primed"
    WATCH_DEGRADED = "watch.degraded"
    SNAPSHOT_INCOMPLETE = "snapshot.incomplete"
    EVENT_LOSS = "event.loss"


#: Шаблон ключа упорядочивания для каждого типа события.
#:
#: Порядок сохраняется внутри одного ключа. События с разными ключами
#: обрабатываются параллельно и порядка между собой не имеют.
ORDERING_KEY: Final[dict[EventType, str]] = {
    EventType.MESSAGE_CREATED: "chat:{chat_id}",
    EventType.CHAT_UNREAD_CHANGED: "chat:{chat_id}",
    EventType.ORDER_CREATED: "order:{order_id}",
    EventType.ORDER_STATUS_CHANGED: "order:{order_id}",
    EventType.REVIEW_CHANGED: "order:{order_id}",
    EventType.LOT_PRICE_CHANGED: "lot:{lot_id}",
    EventType.LOT_STOCK_CHANGED: "lot:{lot_id}",
    EventType.MARKET_OFFER_APPEARED: "watch:{watch_id}",
    EventType.MARKET_OFFER_DISAPPEARED: "watch:{watch_id}",
    EventType.MARKET_PRICE_CHANGED: "watch:{watch_id}",
    EventType.SELLER_ONLINE_CHANGED: "watch:{watch_id}",
    EventType.PROTOCOL_HEALTH_CHANGED: "account:{account_id}",
    EventType.WATCH_PRIMED: "watch:{watch_id}",
    EventType.WATCH_DEGRADED: "watch:{watch_id}",
    EventType.SNAPSHOT_INCOMPLETE: "watch:{watch_id}",
    EventType.EVENT_LOSS: "account:{account_id}",
}

#: Поля, из которых строится отпечаток события.
#:
#: Перечень закрытый. Добавление поля меняет идентичность всех событий
#: сразу, то есть обнуляет дедупликацию и сохранённые ключи
#: идемпотентности.
FINGERPRINT_FIELDS: Final[tuple[str, ...]] = (
    "account_id",
    "type",
    "entity_id",
    "entity_revision",
)

#: Чем разделяются части при склейке перед хэшированием.
#:
#: Управляющий знак, а не печатный: все части приходят снаружи, и любой
#: печатный разделитель рано или поздно встретится внутри части. Тогда две
#: разные четвёрки склеятся в одну строку, и два разных события получат
#: один отпечаток - молча.
FINGERPRINT_SEPARATOR: Final[str] = "\x1f"

#: Имя алгоритма хэширования из hashlib.
#:
#: Выбор не про стойкость: отпечаток не защищает ни от кого, он различает.
#: Зафиксирован он потому, что должен совпасть у шести реализаций.
FINGERPRINT_HASH: Final[str] = "blake2s"

#: Длина хэша в байтах.
FINGERPRINT_DIGEST_BYTES: Final[int] = 16

#: Длина отпечатка в знаках шестнадцатеричной записи.
#:
#: Зафиксирована отдельно от длины хэша, чтобы реализация не удлинила её
#: вслед за сменой алгоритма, не заметив, что этим обнулила сохранённые
#: ключи идемпотентности у всех, кто уже работает.
FINGERPRINT_LENGTH: Final[int] = 32

#: Сколько записей о ключе упорядочивания хранится минимум.
#:
#: Число объявлено спецификацией и прежде совпадало с ним по
#: совпадению: в реализации оно было литералом. Слишком малое
#: значение вытесняет запись о доставленном событии до истечения
#: срока, и событие приходит второй раз - тихо и не всегда.
MIN_ENTRIES_PER_KEY: Final[int] = 256

#: Сколько хранится запись о доставленном событии, миллисекунды.
DEDUP_TTL_MS: Final[int] = 3600000


#: Полоса очереди, к которой относится вид события.
#:
#: Полоса решает две вещи: можно ли выбросить событие при
#: переполнении и считается ли оно признаком активности. События о
#: самом наблюдении - приветствие, жалоба на неполноту, сообщение о
#: потере - данными не являются, и держать по ним опрос на
#: минимальном интервале значит стучаться в площадку из-за
#: собственного состояния.
EVENT_LANE: Final[dict[EventType, str]] = {
    EventType.MESSAGE_CREATED: "data_plane",
    EventType.CHAT_UNREAD_CHANGED: "data_plane",
    EventType.ORDER_CREATED: "data_plane",
    EventType.ORDER_STATUS_CHANGED: "data_plane",
    EventType.REVIEW_CHANGED: "data_plane",
    EventType.LOT_PRICE_CHANGED: "data_plane",
    EventType.LOT_STOCK_CHANGED: "data_plane",
    EventType.MARKET_OFFER_APPEARED: "monitoring",
    EventType.MARKET_OFFER_DISAPPEARED: "monitoring",
    EventType.MARKET_PRICE_CHANGED: "monitoring",
    EventType.SELLER_ONLINE_CHANGED: "monitoring",
    EventType.PROTOCOL_HEALTH_CHANGED: "control_plane",
    EventType.WATCH_PRIMED: "control_plane",
    EventType.WATCH_DEGRADED: "control_plane",
    EventType.SNAPSHOT_INCOMPLETE: "control_plane",
    EventType.EVENT_LOSS: "control_plane",
}


#: Версия события, случающегося с сущностью однажды.
#:
#: Заказ появляется в списке один раз, и различать разные появления
#: одного заказа не требуется. Любая переменная часть - время,
#: порядковый номер, состав строки - сделала бы отпечаток разным при
#: повторном чтении того же списка, то есть отменила бы гашение
#: повторов для самого частого события.
REVISION_APPEARED: Final[str] = "appeared"


#: Чем склеиваются части составной версии сущности.
#:
#: Величина контрактная, а не внутренняя. Отпечаток строится из
#: версии, поэтому две реализации, взявшие разные знаки, разойдутся
#: в отпечатке на каждом событии с составной версией.
#:
#: U+001E, а не U+001F: второй склеивает сам отпечаток, и совпади
#: они - составная версия положила бы разделитель отпечатка внутрь
#: его же части. Склейка перестала бы различать четвёрки полей.
REVISION_SEPARATOR: Final[str] = "\x1e"

#: Можно ли выбрасывать события полосы при переполнении.
LANE_DROPPABLE: Final[dict[str, bool]] = {
    "control_plane": False,
    "data_plane": False,
    "monitoring": True,
}
