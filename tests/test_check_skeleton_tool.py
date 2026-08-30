"""Проверки самой проверки снимков.

ЗАЧЕМ ПРОВЕРЯТЬ ПРОВЕРКУ. `tools/check_skeleton.py` - последняя преграда перед
тем, как снимок ляжет в фикстуры репозитория. Она отвечает на один вопрос: не
осталось ли в снимке чего-то, что читается как имя, сумма, адрес или переписка.

Пропустив раскрытое значение, она не сломает ни одной сборки и не уронит ни
одной проверки. Она просто промолчит, а следующий снимок уедет в открытый
репозиторий вместе с тем, что в нём осталось.

Шумящая она хуже отсутствующей ровно так же: список, полный ссылок подвала,
перестают читать - и настоящую утечку в нём не замечают.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_skeleton import _is_masked_query, _is_masked_url  # noqa: E402


@pytest.mark.parametrize(
    "query",
    [
        "id={q1}",
        "node={q12}",
        "id={q1}&sort={q2}",
        "raise",
        "id={q}",
        "{q}",
        "flag&id={q1}",
    ],
)
def test_a_masked_query_passes(query: str) -> None:
    """Требует пропускать обезличенную строку запроса.

    Проверка, шумящая на каждой ссылке, перестаёт читаться - и настоящую утечку
    в её выводе не замечают.

    Аргументы:
        query (str): обезличенная строка запроса.

    Возвращает:
        None
    """
    assert _is_masked_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "id=75289502",
        "node=281916231",
        "id={q1}&sort=price",
        "Иванов Иван={q1}",
        "id=",
        "id={q1}&открыто",
        "text=здравствуйте",
    ],
)
def test_an_open_value_is_caught(query: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА: раскрытое значение обязано быть замечено.

    Пропустив его, проверка не сломает ничего и промолчит, а снимок уедет в
    открытый репозиторий вместе с тем, что в нём осталось.

    Аргументы:
        query (str): строка запроса с раскрытым куском.

    Возвращает:
        None
    """
    assert not _is_masked_query(query)


@pytest.mark.parametrize(
    ("url", "safe"),
    [
        ("https://funpay.com/lots/offer?id={q1}", True),
        ("https://funpay.com/chat/?node={q2}", True),
        ("https://funpay.com/lots/{n1}/trade?raise", True),
        ("https://funpay.com/lots/offer?{q}", True),
        ("https://funpay.com/lots/offer?id=75289502", False),
        ("https://funpay.com/lots/1908/trade", False),
        ("https://funpay.com/x?a={q1}&b=открыто", False),
    ],
)
def test_the_whole_address_is_judged_by_both_halves(url: str, safe: bool) -> None:
    """Требует судить и путь, и строку запроса.

    Обезличенный путь с раскрытой строкой запроса - это раскрытый адрес, и
    наоборот.

    Аргументы:
        url (str): адрес из снимка.
        safe (bool): ожидается ли, что он обезличен.

    Возвращает:
        None
    """
    assert _is_masked_url(url) is safe
