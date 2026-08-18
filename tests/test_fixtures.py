"""Регрессия классификатора на настоящих страницах площадки.

Фикстуры в tests/fixtures/pages - структурные скелеты реальных ответов, снятые
инструментом funora-observe. Сырого HTML в них нет по построению формата, но
разметка сохранена целиком, поэтому селекторы проверяются ровно те же, что
работают на живой странице.

Смысл этих тестов в том, что признаки в DEFAULT_SIGNATURES выведены из этих
самых снимков. Без регрессии ничто не мешает позже поправить признак так, что он
перестанет узнавать страницу, ради которой был написан, и отказ будет тихим.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from funora._classify import ResponseClass, classify
from funora._skeleton import _self_check

#: Каталог с фикстурами страниц.
PAGES = Path(__file__).parent / "fixtures" / "pages"

#: Ожидаемый вердикт для каждой фикстуры.
EXPECTED = {
    "orders-trade.logged.ru": ResponseClass.OK,
    "chat.logged.ru": ResponseClass.OK,
    "orders-trade.guest.ru": ResponseClass.LOGIN_REQUIRED,
}


def _read(name: str) -> str:
    """Читает скелет фикстуры.

    Args:
        name (str): Имя фикстуры без расширения.

    Returns:
        str: Содержимое скелета.
    """
    return (PAGES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_parses(name: str) -> None:
    """Проверяет, что фикстура разбирается и содержит узлы.

    Args:
        name (str): Имя фикстуры.
    """
    tree = HTMLParser(_read(name))
    assert tree.body is not None, "тело документа не разобралось"
    assert len(tree.css("*")) > 100, "дерево подозрительно мелкое"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_classification(name: str) -> None:
    """Проверяет вердикт классификатора на снимке настоящей страницы.

    Args:
        name (str): Имя фикстуры.
    """
    verdict = classify(
        status=200,
        final_url="https://funpay.com/orders/trade",
        html=_read(name),
        expected_host="funpay.com",
    )
    assert verdict.cls is EXPECTED[name]
    assert not verdict.provisional, (
        "вердикт на настоящей странице не должен опираться на непроверенный признак"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_contains_no_text(name: str) -> None:
    """Проверяет, что фикстура удовлетворяет формату скелета.

    Фикстуры лежат в открытом репозитории, поэтому проверка повторяется здесь, а
    не считается выполненной один раз при захвате.

    Args:
        name (str): Имя фикстуры.
    """
    _self_check(_read(name))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_has_provenance(name: str) -> None:
    """Проверяет наличие и состав описания происхождения.

    Args:
        name (str): Имя фикстуры.
    """
    data = json.loads((PAGES / f"{name}.provenance.json").read_text(encoding="utf-8"))
    for key in ("path", "captured_at", "http_status", "locale", "format"):
        assert key in data, f"в описании нет поля {key}"
    assert data["format"] == "structural-skeleton-v2"


def test_logged_and_guest_markers_do_not_overlap() -> None:
    """Проверяет, что признаки вошедшего и гостя взаимно исключают друг друга.

    Это условие важнее самих селекторов: если признак встречается на обеих
    страницах, он не различает состояния, и классификатор построен на песке.
    """
    logged = HTMLParser(_read("orders-trade.logged.ru"))
    guest = HTMLParser(_read("orders-trade.guest.ru"))

    only_logged = (".navbar-toggle-logged",)
    only_guest = (
        ".navbar-toggle-guest",
        ".menu-item-login",
        ".menu-item-register",
        ".content-account-login",
    )
    for sel in only_logged:
        assert logged.css_first(sel) is not None, f"{sel} пропал у вошедшего"
        assert guest.css_first(sel) is None, f"{sel} нашёлся у гостя"
    for sel in only_guest:
        assert guest.css_first(sel) is not None, f"{sel} пропал у гостя"
        assert logged.css_first(sel) is None, f"{sel} нашёлся у вошедшего"
