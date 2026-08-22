"""Проверяет денежную сумму как доменный тип.

Тип объявлен спецификацией давно и не был построен ни разу: цена отдавалась
текстом, а арифметики не существовало вовсе. Вызывающий складывал цены своим
кодом - в котором нет ни проверки валют, ни защиты от плавающей точки, ради
которых тип и объявлен.

Чего здесь нет: сборки суммы из наблюдённого текста. Она требует вывести код
валюты из символа, а символ в снимках замаскирован и не наблюдался ни разу.
"""

from __future__ import annotations

import json

import pytest

from funora import Money
from funora._canonical import canonical_dumps
from funora.errors import CurrencyMismatchError, ValidationError


def test_addition_stays_exact() -> None:
    """Проверяет, что сложение не заводит дробной ошибки.

    0.1 + 0.2 даёт 0.30000000000000004 всюду, где считают двоичной дробью, и
    сумма двух заказов расходится с той, что видит площадка. В минорных
    единицах этого не бывает по устройству.

    Returns:
        None
    """
    assert Money(10, "RUB") + Money(20, "RUB") == Money(30, "RUB")


def test_currencies_do_not_mix() -> None:
    """Проверяет главное правило типа.

    Курс - величина, меняющаяся ежеминутно и здесь неизвестная. Сложить рубли с
    долларами значило бы выдать выдуманное число за сумму.

    Returns:
        None
    """
    with pytest.raises(CurrencyMismatchError):
        Money(100, "RUB") + Money(100, "USD")
    with pytest.raises(CurrencyMismatchError):
        Money(100, "RUB") - Money(100, "USD")


def test_comparison_is_no_safer_than_addition() -> None:
    """Проверяет, что сравнение между валютами тоже отвергается.

    «Дешевле» между валютами требует курса ровно так же, как сумма.

    Returns:
        None
    """
    with pytest.raises(CurrencyMismatchError):
        assert Money(100, "RUB") < Money(100, "USD")


def test_the_symbol_is_not_a_code() -> None:
    """Проверяет, что символ валюты не принимается за код.

    Один и тот же знак носят несколько валют. Принять символ за код значило бы
    приписать чужую валюту чужому заказу молча.

    Returns:
        None
    """
    for wrong in ("₽", "руб", "rub", "RUBLE", ""):
        with pytest.raises(ValidationError):
            Money(100, wrong)


def test_a_fractional_multiplier_is_refused() -> None:
    """Проверяет, что доля требует правила округления, которого нет.

    Округлять вверх, вниз либо к ближайшему - три разных ответа на один вопрос,
    и выбрать за вызывающего нельзя.

    Returns:
        None
    """
    with pytest.raises(ValidationError):
        Money(100, "RUB") * 1.5


def test_same_currency_different_scale_is_refused() -> None:
    """Проверяет, что копейки не складываются с сотыми копейки.

    Разойдётся на два порядка, и разойдётся молча.

    Returns:
        None
    """
    with pytest.raises(ValidationError):
        Money(100, "RUB", 2) + Money(100, "RUB", 4)


def test_canonical_form_matches_the_declared_shape() -> None:
    """Проверяет, что сумма сериализуется объявленной формой.

    Ровно то, что проверяет вектор money/canonical: три ключа, целая сумма,
    порядок по кодовым точкам.

    Returns:
        None
    """
    rendered = canonical_dumps({"price": Money(123410, "RUB", 2)})

    assert rendered == '{"price":{"amount_minor":123410,"currency":"RUB","scale":2}}'
    assert json.loads(rendered)["price"]["amount_minor"] == 123410


def test_negative_amounts_are_allowed() -> None:
    """Проверяет, что возвраты и корректировки выразимы.

    Returns:
        None
    """
    assert str(Money(-5000, "RUB", 2)) == "-50.00 RUB"
