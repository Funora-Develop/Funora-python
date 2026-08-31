"""Расчёт цены покупателя по цене продавца.

ЗАЧЕМ ЭТО НУЖНО. Цена, которую ставит продавец, и цена, которую платит
покупатель, - РАЗНЫЕ ВЕЛИЧИНЫ: между ними комиссия площадки, и зависит она от
способа оплаты.

Разрыв этот наблюдаем нашим же снимком формы правки лота - там лежит таблица
.table-buyers-prices, которую эта точка и перерисовывает, - а в контракте до
31.08.2026 не назывался нигде. Молчание читалось как «цена одна».

ЦЕНЫ ОТДАЮТСЯ ТЕКСТОМ, и это не лень. Разделитель дробной части нам не
наблюдался, и перевод «1 234,56» в число зависит от локали.

Сторонняя реализация, у которой взят состав запроса, здесь убирает пробелы и
зовёт float. На локали с запятой это отказ САМОГО ЯЗЫКА - падение вместо ответа,
и падение у вызывающего, который ничего такого не просил.

Наблюдено нами: таблица итога на странице правки лота.
Известно от FunPayAPI (FunPayCardinal, account.py, calc): адреса, имена полей
запроса и ключи ответа.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from ._observed import Observed
from .errors import ProtocolChangedError

__all__ = [
    "PaymentMethod",
    "PriceCalculation",
    "parse_calculation",
    "LOTS_CALC_PATH",
    "CHIPS_CALC_PATH",
]

#: Адрес расчёта для обычных разделов. Известен от сторонней реализации.
LOTS_CALC_PATH: Final[str] = "/lots/calc"

#: Адрес расчёта для рынка по количеству. Оттуда же.
CHIPS_CALC_PATH: Final[str] = "/chips/calc"


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    """Один способ оплаты и цена покупателя при нём.

    Attributes:
        name (str): Название способа на локали интерфейса, КАК ЕСТЬ. Не
            разбирается и не классифицируется: выводить из него способ значило
            бы строить разбор на переводе.
        price_text (str): Цена покупателя текстом. Числом не отдаётся -
            разделитель дробной части не наблюдался.
        currency_symbol (Observed[str]): Знак валюты этой цены. Перевод в код
            делает funora.currency_of_symbol.
        sort (Observed[int]): Порядковое число, которым площадка сортирует
            способы.
    """

    name: str
    price_text: str
    currency_symbol: Observed[str]
    sort: Observed[int]


@dataclass(frozen=True, slots=True)
class PriceCalculation:
    """Что заплатит покупатель за названную цену продавца.

    Attributes:
        methods (tuple[PaymentMethod, ...]): Способы оплаты и цены при них.
            Пустой перечень означает наблюдение «способов не предложено», а не
            неудачу разбора: последнюю выражает отказ.
        min_price_text (Observed[str]): Наименьшая цена, которую площадка
            разрешает поставить, КАК ОНА ЕЁ НАЗВАЛА - со знаком валюты и
            целиком. Делить эту строку мы не станем: сторонняя реализация делит
            её по последнему пробелу и зовёт float на первой половине.
        asked_price (str): Цена продавца, о которой спрашивали.
        observed_at (datetime): Момент получения ответа.
    """

    methods: tuple[PaymentMethod, ...]
    min_price_text: Observed[str]
    asked_price: str
    observed_at: datetime


def _sort_of(raw: Any) -> Observed[int]:
    """Читает порядковое число способа оплаты.

    Аргументы:
        raw (Any): Значение из ответа.

    Возвращает:
        Observed[int]: Число либо причина отсутствия.
    """
    # Логическое исключается отдельно: истина в Python - это единица, и порядок
    # True встал бы между нулевым и вторым, ни разу не будучи числом.
    if isinstance(raw, bool):
        return Observed.missing("sort_not_a_number")
    if isinstance(raw, int):
        return Observed.present(raw)
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return Observed.present(int(raw.strip()))
    return Observed.missing("sort_not_a_number")


def parse_calculation(payload: Any, *, asked_price: str, observed_at: datetime) -> PriceCalculation:
    """Разбирает ответ площадки на расчёт цены.

    Аргументы:
        payload (Any): Разобранное тело ответа.
        asked_price (str): Цена продавца, о которой спрашивали.
        observed_at (datetime): Момент получения.

    Возвращает:
        PriceCalculation: Расчёт.

    Raises:
        ProtocolChangedError: Если ответ непригоден для чтения.
    """
    if not isinstance(payload, dict):
        raise ProtocolChangedError(f"ответ расчёта не объект, а {type(payload).__name__}")

    error = payload.get("error")
    if error:
        raise ProtocolChangedError(
            f"площадка отказала в расчёте: {error!r}. Что именно ей не подошло - "
            "неизвестно, а толковать её текст мы не станем"
        )

    raw_methods = payload.get("methods")
    if not isinstance(raw_methods, list):
        raise ProtocolChangedError(
            "в ответе расчёта нет перечня способов оплаты. Пустой перечень и "
            "отсутствующий - разные вещи: первый означает «способов не "
            "предложено», второй - что мы читаем не тот ответ"
        )

    methods: list[PaymentMethod] = []
    for one in raw_methods:
        if not isinstance(one, dict):
            raise ProtocolChangedError(f"способ оплаты не объект, а {type(one).__name__}")

        raw_price = one.get("price")
        if not isinstance(raw_price, str) or not raw_price.strip():
            raise ProtocolChangedError(
                "у способа оплаты нет цены строкой. Подставить сюда ноль значило "
                "бы сказать продавцу, что покупатель заплатит ничего"
            )

        raw_name = one.get("name")
        raw_unit = one.get("unit")
        methods.append(
            PaymentMethod(
                name=raw_name if isinstance(raw_name, str) else "",
                price_text=raw_price.strip(),
                currency_symbol=(
                    Observed.present(raw_unit.strip())
                    if isinstance(raw_unit, str) and raw_unit.strip()
                    else Observed.missing("unit_not_in_response")
                ),
                sort=_sort_of(one.get("sort")),
            )
        )

    raw_min = payload.get("minPrice")
    minimum = (
        Observed.present(raw_min.strip())
        if isinstance(raw_min, str) and raw_min.strip()
        else Observed.missing("min_price_not_in_response")
    )

    return PriceCalculation(
        methods=tuple(methods),
        min_price_text=minimum,
        asked_price=asked_price,
        observed_at=observed_at,
    )
