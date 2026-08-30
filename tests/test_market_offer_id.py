"""Проверки того, что публичный список отдаёт идентификатор предложения.

ЗАЧЕМ ОТДЕЛЬНЫЙ НАБОР. В реестре неисполненного про market.offers стояло:
«идентификатор чужого предложения лежит в строке запроса ссылки и атрибутом не
выносится», и вывод - «в проекте его нет ниоткуда».

Первая половина верна, вторая нет. Строка запроса лежит В АТРИБУТЕ href, и не
выносило её НАШЕ СОБСТВЕННОЕ правило маскировки. С формата v9 имя параметра
сохраняется, и идентификатор виден.

Эти проверки стоят затем, чтобы утверждение не вернулось. Они держатся за
снимок, а не за прозу: снимок либо несёт имя параметра, либо нет.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from selectolax.parser import HTMLParser

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

#: Публичный список, снятый форматом v9 и БЕЗ сессии.
GUEST: Final[str] = "market-offers.trimmed.guest.ru"

#: Он же, снятый форматом v8 под сессией. Стоит рядом нарочно.
LOGGED: Final[str] = "market-offers.trimmed.logged.ru"


def _page(name: str) -> str:
    """Читает снимок.

    Аргументы:
        name (str): имя фикстуры.

    Возвращает:
        str: разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_every_row_carries_an_offer_id_parameter() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: у каждой строки в ссылке есть параметр id.

    Возвращает:
        None
    """
    rows = HTMLParser(_page(GUEST)).css("a.tc-item")
    assert rows, "в снимке нет ни одной строки предложения"

    without = [one for one in rows if "id=" not in ((one.attributes or {}).get("href") or "")]
    assert not without, f"строк без параметра id: {len(without)} из {len(rows)}"


def test_the_identifier_itself_is_never_in_the_snapshot() -> None:
    """Требует, чтобы значение параметра оставалось замаскированным.

    Имя выбирает площадка, значение - человек. Первое видно, второе никогда:
    восьмизначное число здесь - чужой лот, и снимок лежит в открытом
    репозитории.

    Возвращает:
        None
    """
    page = _page(GUEST)
    for href in re.findall(r'href="([^"]*lots/offer[^"]*)"', page):
        value = href.partition("id=")[2]
        assert re.fullmatch(r"\{q\d+\}", value), f"значение параметра раскрыто: {href}"


def test_identifiers_are_distinguishable_between_rows() -> None:
    """Требует, чтобы разные предложения были различимы.

    Схлопнись они в одну подпись - и всякая проверка сравнения снимков рынка
    проходила бы впустую, выглядя пройденной. На это в проекте наступали дважды
    в другом месте.

    Возвращает:
        None
    """
    page = _page(GUEST)
    values = re.findall(r'href="[^"]*lots/offer\?id=(\{q\d+\})"', page)
    assert len(values) >= 20, f"строк с идентификатором всего {len(values)}"
    assert len(set(values)) == len(values), "два предложения получили один номер"


def test_the_older_snapshot_shows_why_it_had_to_be_retaken() -> None:
    """Требует, чтобы прежний снимок ЯВНО не нёс имени параметра.

    Проверка держит доказательство того, что дело было в формате, а не в
    площадке: та же страница, тот же селектор, разница одна - версия формата.

    Возвращает:
        None
    """
    older = _page(LOGGED)
    assert "lots/offer?" in older, "прежний снимок не содержит ссылок предложений"
    assert "lots/offer?id=" not in older, (
        "прежний снимок несёт имя параметра - значит переснимать было незачем, "
        "и объяснение в описании происхождения неверно"
    )


def test_every_row_carries_a_currency_unit() -> None:
    """Требует, чтобы знак валюты был на месте у каждой строки.

    В реестре неисполненного стояло «валюта не наблюдается вовсе». Это неверно
    и противоречило соседнему файлу: spec/extraction/market.yaml объявляет
    селектор .tc-price .unit наблюдённым.

    Наблюдается ЗНАК. Кода валюты на странице по-прежнему нет, и Money из этого
    не собрать - но причина у операции ровно одна, а не три.

    Возвращает:
        None
    """
    rows = HTMLParser(_page(GUEST)).css("a.tc-item")
    without = [one for one in rows if one.css_first(".tc-price .unit") is None]
    assert not without, f"строк без знака валюты: {len(without)} из {len(rows)}"


def test_the_currency_sign_itself_is_masked_in_the_snapshot() -> None:
    """Требует, чтобы сам знак в снимке был подписью, а не знаком.

    Это и есть причина, по которой соответствие знака коду по снимку не
    проверить: знак - текст, а текст в скелете заменён мерой.

    Возвращает:
        None
    """
    page = _page(GUEST)
    units = re.findall(r'<span class="unit">\s*([^<\s][^<]*?)\s*</span>', page)
    assert units, "в снимке нет ни одного узла знака валюты"
    for one in units:
        assert re.fullmatch(r"T\d+:[adcpso]+(#\d+)?", one), f"знак валюты раскрыт: {one!r}"
