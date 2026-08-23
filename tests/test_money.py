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
import os
from pathlib import Path

import pytest

from funora import Money
from funora._canonical import canonical_dumps
from funora.errors import CurrencyMismatchError, ValidationError

#: Рабочая копия спецификации. Без неё сверка отображения пропускается.
_SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")


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


@pytest.mark.skipif(
    not _SPEC_DIR or not (Path(_SPEC_DIR) / "spec" / "types.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_the_language_mapping_matches_the_implementation() -> None:
    """Сверяет объявленное отображение денег с тем, что реализовано.

    Контракт объявляет, каким типом каждый из шести языков хранит сумму и в
    каком типе над ней считает. Сверять было нечем, и строка Python успела
    разойтись: объявляла «int + Decimal в helper», а Decimal не встречается в
    реализации ни разу - обещан был помощник, которого нет.

    Вред такого расхождения не в самой сумме. Читающий строку своего языка
    считает её проверенной хоть кем-то и повторяет её в публичном типе денег
    своего SDK. Тип денег в публичном интерфейсе не переделаешь потом молча.

    Строки пяти остальных языков здесь не сверяются: реализаций этих языков не
    существует, и сверять их не с чем. Они записаны в
    spec/conformance/not-implemented.yaml как money_mapping_for_absent_languages.

    Returns:
        None
    """
    import typing

    import yaml

    types = yaml.safe_load(
        (Path(_SPEC_DIR or ".") / "spec" / "types.yaml").read_text(encoding="utf-8")
    )
    declared = types["types"]["money"]["language_mapping"]["python"]
    assert isinstance(declared, dict), (
        "строка Python объявлена прозой. Сверить прозу с реализацией нельзя, а "
        "именно прозой она и разошлась"
    )

    stored = typing.get_type_hints(Money)["amount_minor"].__name__
    assert declared["storage"] == stored, (
        f"объявлено хранение в {declared['storage']!r}, а поле amount_minor "
        f"объявлено как {stored!r}. Читающий повторит объявленное в публичном "
        "типе денег своего SDK, и переделать его потом молча уже нельзя"
    )

    total = Money(1000, "RUB", 2) + Money(2345, "RUB", 2)
    used = type(total.amount_minor).__name__
    assert declared["arithmetic"] == used, (
        f"объявлена арифметика в {declared['arithmetic']!r}, а сложение даёт "
        f"{used!r}. Два SDK, считающих деньги разными типами, разойдутся на "
        "округлении - и разойдутся молча, потому что на малых суммах совпадут"
    )

    if "Decimal" in str(declared.values()):
        source = Path(__file__).resolve().parent.parent / "src" / "funora"
        found = any("Decimal" in one.read_text(encoding="utf-8") for one in source.rglob("*.py"))
        assert found, (
            "объявление называет Decimal, а в исходниках реализации он не "
            "встречается ни разу. Объявлен помощник, которого нет"
        )
