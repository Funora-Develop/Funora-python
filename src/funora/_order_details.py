"""Структурное чтение подробностей заказа.

ЕДИНСТВЕННАЯ ТОЧКА, ОТДАЮЩАЯ ЗАКАЗ СТРУКТУРНО, а не разметкой. Она даёт то,
чего страница не даёт ни в каком виде: сумму ЧИСЛОМ, код валюты, разделение
покупателя и продавца, состояние строкой и раздел парой «тип - номер».

СВОЕГО НАБЛЮДЕНИЯ У НАС ЗДЕСЬ НЕТ НИ ОДНОГО, и это отличает точку от прочих на
вторичном источнике. У поднятия наблюдён запрос; у отзыва - два поля из четырёх;
у расчёта цены - итог на странице. Здесь не наблюдено ничего.

ЭТО МОГЛО БЫ СОБРАТЬ Order, И НЕ СОБИРАЕТ. Модель Order не строится ни одной
реализацией из-за трёх причин сразу: сумма текстом, валюта знаком, покупатель и
продавец неразделимы. Эта точка снимает все три.

И всё же Order из неё не собирается. Модель контракта, построенная на ответе,
которого никто не видел, - худший вид обещания: второй SDK прочтёт её как
описание наблюдённого. Возвращается OrderDetails - проекция, отличимая по типу.

Первое живое наблюдение это изменит, и будет оно самой крупной разблокировкой
контракта.

Известно от FunPayAPI (FunPayCardinal, account.py, get_orders_by_ids).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from ._observed import Observed
from .errors import ProtocolChangedError, ValidationError
from .extraction import OrderStatus

__all__ = [
    "OrderDetails",
    "OrderDetailsBatch",
    "parse_order_details",
    "ORDER_DETAILS_PATH",
    "BATCH_MAX",
    "DEFAULT_INCLUDE",
]

#: Адрес структурного чтения. Известен от сторонней реализации.
#:
#: Полной ссылкой он там и записан: языковой префикс к нему не применяется, а
#: язык передаётся заголовком. У нас путь - хост подставляет транспорт, и это
#: то же самое, только без возможности уйти на чужой.
ORDER_DETAILS_PATH: Final[str] = "/api/orders/get"

#: Наибольший размер пачки.
#:
#: Взят у стороннего источника, и откуда там - из ответа площадки либо из
#: осторожности автора - по его коду не видно. Соблюдаем: нарушать неизвестное
#: ограничение дороже, чем сделать два запроса вместо одного.
BATCH_MAX: Final[int] = 10

#: Разделы ответа, которые запрашиваются по умолчанию.
DEFAULT_INCLUDE: Final[tuple[str, ...]] = ("details", "users")

#: Наши состояния по имени. Перечень НАШ и НАБЛЮДЁННЫЙ.
#:
#: Сторонний источник называет пять значений; мы наблюдали три - в разметке
#: списка продаж. Дописать сюда остальные по чужому слову значило бы выдать
#: чужое знание за своё наблюдение, и проверка на это стоит отдельно.
_KNOWN_STATUS: Final[dict[str, OrderStatus]] = {one.value: one for one in OrderStatus}


@dataclass(frozen=True, slots=True)
class OrderDetails:
    """Подробности одного заказа, прочитанные структурно.

    Attributes:
        order_uid (str): Идентификатор заказа.
        status_text (str): Состояние СТРОКОЙ, как назвала площадка. Всегда и
            как есть.
        status (Observed[OrderStatus]): Состояние значением НАШЕГО перечня -
            если оно там есть. Не совпало - остаётся ненаблюдённым.
        amount (Observed[str]): Сумма, приведённая к строке. Числом она здесь и
            приходит; строкой отдаётся, чтобы не потерять точность на двоичной
            дроби.
        currency (Observed[str]): Код валюты - кодом, а не знаком.
        buyer_id (Observed[str]): Идентификатор покупателя.
        buyer_name (Observed[str]): Имя покупателя.
        seller_id (Observed[str]): Идентификатор продавца.
        seller_name (Observed[str]): Имя продавца.
        section_type (Observed[str]): Вид раздела.
        section_id (Observed[str]): Номер раздела.
    """

    order_uid: str
    status_text: str
    status: Observed[OrderStatus]
    amount: Observed[str]
    currency: Observed[str]
    buyer_id: Observed[str]
    buyer_name: Observed[str]
    seller_id: Observed[str]
    seller_name: Observed[str]
    section_type: Observed[str]
    section_id: Observed[str]


@dataclass(frozen=True, slots=True)
class OrderDetailsBatch:
    """Итог чтения заказов пачкой.

    Attributes:
        asked (tuple[str, ...]): О чём спрашивали.
        found (tuple[OrderDetails, ...]): Что вернулось.
        missing (tuple[str, ...]): Спрошенное, но не вернувшееся.

            СПРОШЕННОЕ И ПОЛУЧЕННОЕ РАЗВЕДЕНЫ НАРОЧНО. Площадка вправе вернуть
            не все заказы, и молчаливая потеря одного из десяти выглядела бы
            как «такого заказа нет».
        observed_at (datetime): Момент получения ответа.
    """

    asked: tuple[str, ...]
    found: tuple[OrderDetails, ...]
    missing: tuple[str, ...]
    observed_at: datetime


def _text(source: dict[str, Any], *path: str) -> Observed[str]:
    """Достаёт вложенное значение и приводит его к строке.

    ЧИСЛО ПРИВОДИТСЯ К СТРОКЕ, А НЕ К ДРОБНОМУ. Сумма приходит числом, и
    сохранить её дробным значило бы потерять точность там, где считают деньги:
    0.1 + 0.2 даёт 0.30000000000000004 всюду, где считают двоичной дробью.

    Аргументы:
        source (dict[str, Any]): Объект ответа.
        path (str): Путь до значения по ключам.

    Возвращает:
        Observed[str]: Значение строкой либо причина отсутствия.
    """
    current: Any = source
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return Observed.missing(f"{'.'.join(path)}_not_in_response")
        current = current[key]

    # Логическое исключается: истина в Python - это единица, и сумма True
    # прочиталась бы как «один».
    if isinstance(current, bool) or current is None:
        return Observed.missing(f"{'.'.join(path)}_not_a_value")
    if isinstance(current, str):
        value = current.strip()
        return Observed.present(value) if value else Observed.empty("")
    if isinstance(current, int | float):
        return Observed.present(repr(current) if isinstance(current, float) else str(current))
    return Observed.missing(f"{'.'.join(path)}_not_a_value")


def _one(record: Any, order_uid: str) -> OrderDetails:
    """Собирает подробности одного заказа.

    Аргументы:
        record (Any): Запись из ответа.
        order_uid (str): Идентификатор заказа.

    Возвращает:
        OrderDetails: Подробности.

    Raises:
        ProtocolChangedError: Если запись непригодна.
    """
    if not isinstance(record, dict):
        raise ProtocolChangedError(
            f"запись заказа {order_uid} не объект, а {type(record).__name__}"
        )

    raw_status = record.get("status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        raise ProtocolChangedError(
            f"у заказа {order_uid} нет состояния строкой. Считать заказ "
            "оплаченным без признака нельзя: по этому признаку выдают товар"
        )
    status_text = raw_status.strip()

    # СОСТОЯНИЕ НЕ ПРИВОДИТСЯ К НАШЕМУ ПЕРЕЧНЮ НАСИЛЬНО. Совпало со знакомым -
    # отдаётся и строкой, и значением. Не совпало - только строкой.
    known = _KNOWN_STATUS.get(status_text)
    status = (
        Observed.present(known)
        if known is not None
        else Observed.missing(f"status_not_in_observed_set:{status_text}")
    )

    return OrderDetails(
        order_uid=order_uid,
        status_text=status_text,
        status=status,
        amount=_text(record, "amount"),
        currency=_text(record, "currency"),
        buyer_id=_text(record, "buyer", "user_id"),
        buyer_name=_text(record, "buyer", "name"),
        seller_id=_text(record, "seller", "user_id"),
        seller_name=_text(record, "seller", "name"),
        section_type=_text(record, "section", "type_id"),
        section_id=_text(record, "section", "local_id"),
    )


def parse_order_details(
    payload: Any, *, asked: tuple[str, ...], observed_at: datetime
) -> OrderDetailsBatch:
    """Разбирает ответ структурного чтения заказов.

    Аргументы:
        payload (Any): Разобранное тело ответа.
        asked (tuple[str, ...]): О чём спрашивали.
        observed_at (datetime): Момент получения.

    Возвращает:
        OrderDetailsBatch: Итог.

    Raises:
        ProtocolChangedError: Если ответ непригоден для чтения.
    """
    if not isinstance(payload, dict):
        raise ProtocolChangedError(f"ответ чтения заказов не объект, а {type(payload).__name__}")

    raw_status = payload.get("status")
    if raw_status != "SUCCESS":
        raise ProtocolChangedError(
            f"ответ чтения заказов несёт признак исхода {raw_status!r}, а успех "
            "обозначается строкой SUCCESS. Прочитать заказы по такому ответу "
            "нельзя: неизвестно даже, о тех ли он заказах"
        )

    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raise ProtocolChangedError(
            "в ответе чтения заказов нет словаря data. Пустой словарь и "
            "отсутствующий - разные вещи: первый означает «ни один не найден», "
            "второй - что мы читаем не тот ответ"
        )

    found: list[OrderDetails] = []
    for order_uid in sorted(raw_data):
        found.append(_one(raw_data[order_uid], order_uid))

    returned = {one.order_uid for one in found}
    return OrderDetailsBatch(
        asked=asked,
        found=tuple(found),
        missing=tuple(one for one in asked if one not in returned),
        observed_at=observed_at,
    )


def check_batch(order_ids: tuple[str, ...]) -> None:
    """Проверяет пачку до обращения к сети.

    Аргументы:
        order_ids (tuple[str, ...]): Идентификаторы заказов.

    Raises:
        ValidationError: Если пачка пуста, велика либо несёт непригодный
            идентификатор.
    """
    if not order_ids:
        raise ValidationError("пачка пуста: спрашивать не о чем")
    if len(order_ids) > BATCH_MAX:
        raise ValidationError(
            f"в пачке {len(order_ids)} заказов, а наибольший наблюдённый размер "
            f"{BATCH_MAX}. Граница взята у стороннего источника, и откуда она "
            "там - неизвестно; нарушать неизвестное ограничение дороже, чем "
            "сделать два запроса"
        )
    if len(set(order_ids)) != len(order_ids):
        raise ValidationError(
            "в пачке есть повторы. Ответ приходит словарём по идентификатору, и "
            "повтор занял бы место в пачке, ничего не добавив"
        )
    for one in order_ids:
        if not one.strip() or not one.strip().isalnum():
            raise ValidationError(f"идентификатор заказа {one!r} обязан состоять из букв и цифр")
