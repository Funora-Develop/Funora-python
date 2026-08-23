"""Проверяет сборку суммы заказа в money.

Знак валюты лежит в отдельном узле ячейки цены - это наблюдено на живой
странице, - а код берётся по таблице, наблюдённой переключателем площадки.

Проверки идут на синтетической разметке, а не на фикстурах, и это не лень.
Скелет заменяет текст подписью, и знак валюты в снимке выглядит как ``T1:o``:
на фикстуре ветка «знак узнан» не исполняется НИ РАЗУ. Проверка, написанная
только на снимках, показывала бы зелёное при любой таблице.

Обратное тоже верно и тоже проверено: на настоящем снимке разбор обязан
молчать, а не объявлять каждую строку повреждённой.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from funora._money import Money
from funora._orders import _money, parse_orders_page
from funora.extraction import AMBIGUOUS_CURRENCY_SYMBOLS, CURRENCY_BY_SYMBOL

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Каталог со снимками страниц.
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"


def _row(amount: str, symbol: str | None) -> object:
    """Собирает строку заказа с заданной ценой.

    Args:
        amount (str): Текст суммы, как он стоит в ячейке.
        symbol (str | None): Знак валюты в отдельном узле либо None, если узла
            знака в разметке нет вовсе.

    Returns:
        object: Узел строки.
    """
    unit = "" if symbol is None else f'<span class="unit">{symbol}</span>'
    html = f'<a class="tc-item"><div class="tc-price">{amount} {unit}</div></a>'
    node = HTMLParser(html).css_first("a.tc-item")
    assert node is not None
    return node


def _read(amount: str, symbol: str | None) -> tuple[object, str | None]:
    """Прогоняет разбор цены на синтетической строке.

    Args:
        amount (str): Текст суммы.
        symbol (str | None): Знак валюты.

    Returns:
        tuple[object, str | None]: Наблюдение и код повреждения.
    """
    row = _row(amount, symbol)
    return _money(row.css_first(".tc-price"), row)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("symbol", "code"),
    [("₽", "RUB"), ("$", "USD"), ("€", "EUR")],
)
def test_an_observed_symbol_becomes_its_code(symbol: str, code: str) -> None:
    """Проверяет три наблюдённых соответствия.

    Каждое снято переключением валюты на живой странице: в положении с
    data-cy этого кода все цены показаны этим знаком.

    Args:
        symbol (str): Знак валюты.
        code (str): Код по ISO 4217.

    Returns:
        None
    """
    price, defect = _read("4682.01", symbol)

    assert defect is None
    assert price.value == Money(468201, code, 2), price  # type: ignore[attr-defined]


def test_the_symbol_does_not_leak_into_the_amount() -> None:
    """Требует брать сумму СОБСТВЕННЫМ текстом ячейки.

    Текст ячейки целиком склеивает сумму со знаком: `.tc-price` даёт «4682.01
    знак». Разбор, читающий его целиком, получил бы не число - и, что хуже,
    получил бы число на площадке, где знак стоит не рядом.

    Returns:
        None
    """
    row = _row("4682.01", "₽")
    cell = row.css_first(".tc-price")  # type: ignore[attr-defined]
    whole = "".join((cell.text() or "").split())
    own = "".join((cell.text(deep=False) or "").split())

    assert whole != own, "знак не попадает в текст ячейки - проверка проверяет не то"
    price, _ = _money(cell, row)
    assert price.value == Money(468201, "RUB", 2)


@pytest.mark.parametrize(
    ("amount", "minor", "scale"),
    [("100", 100, 0), ("7.86", 786, 2), ("14782.88", 1478288, 2), ("0.0139", 139, 4)],
)
def test_the_scale_comes_from_the_text(amount: str, minor: int, scale: int) -> None:
    """Проверяет, что масштаб берётся из строки, а не из валюты.

    Он хранится в самой записи money именно затем, чтобы не выводиться: на
    странице продавца наблюдались цены с четырьмя знаками после точки.

    Args:
        amount (str): Текст суммы.
        minor (int): Ожидаемые минорные единицы.
        scale (int): Ожидаемый масштаб.

    Returns:
        None
    """
    price, defect = _read(amount, "₽")

    assert defect is None
    assert price.value == Money(minor, "RUB", scale), price  # type: ignore[attr-defined]


def test_a_comma_is_refused() -> None:
    """Запрещает принимать запятую.

    Запятая бывает и десятичным разделителем, и разделителем разрядов, и какой
    из двух перед нами - из строки не видно. Принять её значило бы однажды
    прочитать полтора как полторы тысячи.

    Returns:
        None
    """
    price, defect = _read("1,5", "₽")

    assert not price.is_observed, price  # type: ignore[attr-defined]
    assert defect == "amount_not_numeric"


def test_an_unmapped_symbol_is_quiet() -> None:
    """Проверяет, что незнакомый знак НЕ повреждение.

    Так выглядит валюта, которой в таблице ещё нет, а не поломка разбора. То же
    решение принято для носителя статуса: класс вне таблицы даёт ненаблюдённое
    значение и молчит.

    Returns:
        None
    """
    price, defect = _read("100", "¥")

    assert not price.is_observed, price  # type: ignore[attr-defined]
    assert defect is None, "незнакомая валюта объявлена поломкой - фикстуры станут неполными"


def test_an_ambiguous_symbol_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что объявленный неоднозначным знак даёт повреждение.

    Разница с предыдущим случаем содержательная. Отсутствие в таблице означает
    «знака не видели», неоднозначность - «видели, и он не решает». Второе нужно
    показать вслух: сумма есть, а валюта неизвестна.

    Сегодня таких знаков нет, поэтому знак подставляется проверкой. Иначе ветка
    не исполнялась бы ни разу и существовала бы на бумаге.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    monkeypatch.setattr("funora._orders.AMBIGUOUS_CURRENCY_SYMBOLS", frozenset({"$"}))
    price, defect = _read("100", "$")

    assert not price.is_observed, price  # type: ignore[attr-defined]
    assert defect == "currency_symbol_ambiguous"


def test_a_missing_symbol_node_is_quiet() -> None:
    """Проверяет, что отсутствие узла знака не повреждение.

    Селектор мог не найти ничего на странице иного вида, и объявлять это
    поломкой значило бы ронять разбор там, где просто нет цены.

    Returns:
        None
    """
    price, defect = _read("100", None)

    assert not price.is_observed, price  # type: ignore[attr-defined]
    assert defect is None


@pytest.mark.skipif(
    not (PAGES / "orders-trade.logged.ru.skeleton.txt").is_file(),
    reason="снимка списка заказов нет",
)
def test_a_skeleton_stays_complete() -> None:
    """Требует, чтобы на снимке разбор молчал.

    Скелет заменяет знак подписью, и объяви разбор незнакомый знак поломкой -
    каждая фикстура стала бы неполной страницей. Проверка держит это: она
    падала, пока незнакомый знак был повреждением.

    Returns:
        None
    """
    from datetime import UTC, datetime

    html = (PAGES / "orders-trade.logged.ru.skeleton.txt").read_text(encoding="utf-8")
    page = parse_orders_page(html, observed_at=datetime(2026, 8, 23, tzinfo=UTC))

    price_defects = [one for one in page.defects if one.field_name == "price"]
    assert not price_defects, f"снимок дал повреждения цены: {price_defects}"
    assert all(not one.price.is_observed for one in page.rows(accept_incomplete=True)), (
        "на скелете знак замаскирован, и сумма собраться не могла"
    )


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec" / "types.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_the_table_matches_the_contract() -> None:
    """Сверяет порождённую таблицу с объявленной.

    Порождение проверяется отдельно от того, что порождено: кодогенератор мог
    бы отобрать записи неверно, и обе стороны выглядели бы согласными.

    Returns:
        None
    """
    import yaml

    money = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "types.yaml").read_text(encoding="utf-8")
    )["types"]["money"]
    table = money["symbol_table"]

    declared = {
        symbol: entry["currency"] for symbol, entry in table.items() if not entry.get("ambiguous")
    }
    unclear = {symbol for symbol, entry in table.items() if entry.get("ambiguous")}

    assert declared == CURRENCY_BY_SYMBOL, f"порождено {CURRENCY_BY_SYMBOL}, объявлено {declared}"
    assert unclear == AMBIGUOUS_CURRENCY_SYMBOLS

    for symbol, entry in table.items():
        assert entry.get("evidence"), (
            f"у знака {symbol!r} нет ссылки на наблюдение: запись без неё "
            "неотличима от выдуманной"
        )


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec" / "models").is_dir(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_the_field_is_declared_in_the_model() -> None:
    """Требует, чтобы поле price стояло в схеме записи заказа.

    Иначе второй SDK не узнает о нём вовсе, а первый будет его возвращать.

    Returns:
        None
    """
    schema = json.loads(
        (Path(SPEC_DIR or ".") / "spec" / "models" / "order-list-entry.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert "price" in schema["properties"], "поле price не объявлено в схеме"
    assert "price" in schema["required"], "поле price объявлено необязательным"
    assert schema["properties"]["price"]["x-funora-observed-value"] == "money"
