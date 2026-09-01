"""Проверки числовых носителей на строке рынка по количеству.

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ, А НЕ ПРЕДПОЛАГАЕТСЯ.

У строки два носителя количества: текст, где число разделено пробелами, и
атрибут data-s. Подпись скелета хранит ДЛИНУ и СОСТАВ значения, но не само
значение, - и всё же связь двух носителей из неё выводится.

Сверив длины по всем строкам снимка, получаем без единого исключения:

  длина текста минус длина атрибута = число разделителей тысяч,
  и число это ровно такое, каким ему положено быть при такой длине числа.

Один разделитель при четырёх-шести цифрах, два при семи-девяти, ни одного при
трёх и меньше. Значит атрибут - то же число, что и в тексте, без разделителей.

У ЦЕНЫ ТА ЖЕ АРИФМЕТИКА НЕ СХОДИТСЯ, и это вторая половина набора. Атрибут
две-три цифры при тексте в пять-шесть; разделителей тысяч в цене за единицу быть
не может. Что это за число - не установлено, и ценой оно не названо.

Наблюдено 31.08.2026: chips.trimmed.guest.ru.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from funora._chips import parse_chips
from funora._observed import Presence

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
SNAPSHOT: Final[Path] = ROOT / "tests/fixtures/pages/chips.trimmed.guest.ru.skeleton.txt"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)

#: Разбирает пару носителей одной строки: атрибут и текст рядом с ним.
_PAIR: Final[re.Pattern[str]] = re.compile(
    r'<div class="tc-amount" data-s="T(\d+):(\w+)#\d+">\s*T(\d+):(\w+)'
    r'.*?<div class="tc-price" data-s="T(\d+):(\w+)#\d+">\s*<div>\s*T(\d+):(\w+)',
    re.S,
)


def _pairs() -> list[tuple[int, int, int, int]]:
    """Достаёт длины носителей по всем строкам снимка.

    Возвращает:
        list[tuple[int, int, int, int]]: Длины количества и цены - атрибут и
        текст у каждого.
    """
    source = SNAPSHOT.read_text(encoding="utf-8")
    return [
        (int(a), int(at), int(p), int(pt))
        for a, _ak, at, _atk, p, _pk, pt, _ptk in _PAIR.findall(source)
    ]


def _separators_for(digits: int) -> int:
    """Сколько разделителей тысяч положено числу такой длины.

    Аргументы:
        digits (int): Сколько цифр в числе.

    Возвращает:
        int: Сколько разделителей.
    """
    return max(0, (digits - 1) // 3)


def test_the_amount_carrier_is_the_same_number_without_separators() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: связь носителей количества ДОКАЗЫВАЕТСЯ арифметикой.

    Значения в снимке замаскированы, и сверить числа напрямую нельзя. Но подпись
    хранит длину, а разница длин у двух носителей одного числа обязана равняться
    числу разделителей тысяч.

    Сходится на каждой строке - значит атрибут и текст несут одно число.
    Разойдись хоть одна - вывод неверен, и поле amount читать нельзя.

    Возвращает:
        None
    """
    rows = _pairs()
    assert len(rows) >= 10, f"строк разобрано {len(rows)} - проверка стала слабой"

    for index, (attr_len, text_len, _p, _pt) in enumerate(rows):
        expected = _separators_for(attr_len)
        assert text_len - attr_len == expected, (
            f"строка {index}: атрибут {attr_len} знаков, текст {text_len}, "
            f"разница {text_len - attr_len}, а разделителей положено {expected}. "
            "Связь носителей не доказана - поле amount читать нельзя"
        )


def test_the_price_carrier_does_not_follow_the_same_rule() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: у цены та же арифметика НЕ сходится.

    Это не придирка. Сойдись она - цену читали бы числом так же, как количество,
    и это была бы прямая выгода. Она не сходится, и потому носитель цены
    отдаётся как есть, под именем, ничего не обещающим.

    Проверка держит именно расхождение: сойдись оно однажды - вывод надо
    пересмотреть, а не тихо начать читать цену.

    Возвращает:
        None
    """
    rows = _pairs()
    disagreements = [
        (attr, text) for _a, _at, attr, text in rows if text - attr != _separators_for(attr)
    ]

    assert len(disagreements) == len(rows), (
        f"у цены арифметика сошлась на {len(rows) - len(disagreements)} строках из "
        f"{len(rows)}. Если сошлась - вывод «связь не установлена» пора пересмотреть"
    )


def test_the_amount_is_read_as_a_number() -> None:
    """Требует, чтобы количество вправду читалось числом.

    Без неё две предыдущие проверки доказывали бы правило, которым никто не
    пользуется.

    Возвращает:
        None
    """
    # ЖИВЫЕ ЗНАЧЕНИЯ, А НЕ СНИМОК, и это не поблажка. В снимке значение
    # ЗАМАСКИРОВАНО подписью, цифр там нет вовсе, и разбор честно отказывается
    # его читать. Проверять число по снимку значило бы проверять маскировку.
    #
    # Снимок доказывает СВЯЗЬ носителей - это делают две проверки выше. Здесь
    # доказывается, что связью пользуются.
    live = (
        '<a class="tc-item" href="/chips/1/">'
        '<div class="tc-amount" data-s="123456">123 456<span class="unit">шт</span></div>'
        '<div class="tc-price" data-s="12"><div>0,0123<span class="unit">₽</span></div></div>'
        "</a>"
    )
    page = parse_chips(live, observed_at=WHEN)
    offers = page.offers(accept_incomplete=True)

    assert offers, "предложений не прочиталось ни одного"
    assert offers[0].amount.or_none() == 123456, "количество прочитано не числом"


def test_a_masked_snapshot_value_is_refused_and_not_guessed() -> None:
    """Обратная половина: в снимке значение замаскировано, и разбор это признаёт.

    Подпись скелета - не число. Прочитать её как число нельзя, и выдумать вместо
    неё ноль либо длину подписи было бы худшим из решений: количество ушло бы в
    умножение на цену.

    Возвращает:
        None
    """
    page = parse_chips(SNAPSHOT.read_text(encoding="utf-8"), observed_at=WHEN)
    offers = page.offers(accept_incomplete=True)

    assert offers, "предложений не прочиталось ни одного"
    assert all(one.amount.or_none() is None for one in offers), (
        "подпись скелета прочиталась как число - значит разбор что-то выдумал"
    )
    assert all("not_digits" in (one.amount.reason or "") for one in offers)


def test_the_price_carrier_is_kept_raw_and_is_not_called_a_price() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: носитель цены не назван ценой.

    Приписать числу смысл, которого мы не проверяли, - плохо всегда. Приписать
    его ДЕНЬГАМ - хуже: вызывающий умножит его на количество и покажет
    покупателю сумму, которой нет.

    Возвращает:
        None
    """
    from funora._chips import ChipsOffer

    fields = set(ChipsOffer.__dataclass_fields__)
    assert "price_sort" in fields, "носитель цены не читается вовсе"
    assert "price" not in fields, (
        "у предложения завелось поле price. Связь носителя с показанной ценой не "
        "установлена, и называть его ценой значит приписать смысл деньгам"
    )

    page = parse_chips(SNAPSHOT.read_text(encoding="utf-8"), observed_at=WHEN)
    one = page.offers(accept_incomplete=True)[0]
    assert isinstance(one.price_sort.or_none(), str), (
        "носитель цены истолкован, а не отдан как есть"
    )


@pytest.mark.parametrize(
    ("markup", "expected_presence"),
    [
        ('<div class="tc-amount" data-s="1234">x</div>', Presence.PRESENT),
        ('<div class="tc-amount" data-s="">x</div>', Presence.EMPTY),
        ('<div class="tc-amount" data-s="12 34">x</div>', Presence.NOT_OBSERVED),
        ('<div class="tc-amount" data-s="-5">x</div>', Presence.NOT_OBSERVED),
        ('<div class="tc-amount" data-s="1.5">x</div>', Presence.NOT_OBSERVED),
        ('<div class="tc-amount">x</div>', Presence.NOT_OBSERVED),
    ],
)
def test_a_carrier_that_is_not_digits_is_refused(markup: str, expected_presence: Presence) -> None:
    """Требует отказа на всём, что не цифры.

    Прочитать количество наполовину хуже, чем не прочитать: покупатель умножает
    его на цену, и «12» вместо «12 34» - ошибка в сто раз.

    Аргументы:
        markup (str): Разметка носителя.
        expected_presence (Presence): Ожидаемое положение.

    Возвращает:
        None
    """
    from selectolax.parser import HTMLParser

    from funora._chips import _digits

    node = HTMLParser(markup).css_first("div.tc-amount")
    assert _digits(node, "amount").presence is expected_presence


def test_a_missing_carrier_is_not_a_zero() -> None:
    """Требует, чтобы отсутствующий носитель не читался нулём.

    Ноль означал бы «продавец предлагает ноль единиц» - то есть предложение,
    которого нет. Ненаблюдённое означает «мы не прочитали», и различать их
    обязательно.

    Возвращает:
        None
    """
    from funora._chips import _digits

    result = _digits(None, "amount")
    assert result.or_none() is None
    assert result.presence is Presence.NOT_OBSERVED


def test_the_price_carrier_is_given_back_byte_for_byte() -> None:
    """Требует отдавать носитель цены ДОСЛОВНО.

    Проверка «это строка» слаба: её прошёл бы и разбор, обрезающий нули справа
    либо подставляющий запятую. Смысл значения не установлен, и всякая правка
    его - уже толкование.

    Здесь значения живые, и сверять есть с чем.

    Возвращает:
        None
    """
    live = (
        '<a class="tc-item" href="/chips/1/">'
        '<div class="tc-amount" data-s="100">100</div>'
        '<div class="tc-price" data-s="1200"><div>0,1200</div></div>'
        "</a>"
    )
    one = parse_chips(live, observed_at=WHEN).offers(accept_incomplete=True)[0]

    assert one.price_sort.or_none() == "1200", (
        "носитель цены изменился при чтении. Смысл его не установлен, и всякая "
        "правка - уже толкование"
    )
