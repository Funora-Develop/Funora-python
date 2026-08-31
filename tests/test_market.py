"""Проверки чтения публичного списка предложений раздела.

СНИМОК ЗДЕСЬ ОБРЕЗАННЫЙ, и это сказано громко. Настоящий - семь с половиной
мегабайт и три тысячи строк; в репозитории лежат двадцать пять, по нескольку
каждой РАЗЛИЧНОЙ формы строки.

Обрезка годится, чтобы проверять разбор, и не годится, чтобы утверждать о
площадке. Проверки, которым нужны числа, читают само наблюдение из observations/
и пропускаются без него.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from selectolax.parser import HTMLParser

from funora._market import MarketPage, parse_market
from funora._result import Completeness, Severity
from funora.errors import IncompleteResultError, ProtocolChangedError

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
FIXTURES: Final[Path] = ROOT / "tests" / "fixtures" / "pages"
OBSERVATIONS: Final[Path] = ROOT / "observations"

#: Обрезанный снимок ФОРМАТА v9: двадцать четыре строки четырёх форм.
#:
#: Переведено сюда 31.08.2026 с market-offers.trimmed.logged.ru. Причина не в
#: свежести: на снимке v8 строка запроса заменена одной подписью, и
#: идентификатор предложения из неё не читается. Разбор объявляет это
#: повреждением строки - и объявляет верно, потому что на живой странице
#: идентификатор есть у всех строк до единой.
#:
#: То есть прежний снимок перестал быть образцом ПОЛНОГО чтения. Образцом
#: разметки он остался, и на нём по-прежнему держится проверка про то, зачем
#: понадобился новый.
TRIMMED: Final[str] = "market-offers.trimmed.guest.ru"

#: Он же, снятый форматом v8. Идентификатора не несёт по устройству формата.
OLDER: Final[str] = "market-offers.trimmed.logged.ru"

#: Само наблюдение. Может отсутствовать: в репозиторий оно не кладётся.
#:
#: Снятое форматом v8 не годится: разбор объявит каждую строку повреждённой,
#: и числа, ради которых наблюдение и читается, придут вперемешку с шумом.
FULL: Final[Path] = OBSERVATIONS / "lots_n.guest.ru.skeleton.txt"

WHEN: Final[datetime] = datetime(2026, 8, 30, tzinfo=UTC)


def _older() -> str:
    """Читает снимок формата v8.

    Нужен ровно двум проверкам - про поднятые строки. На снимке v9,
    снятом 31.08.2026, поднятых предложений не оказалось ни одного: раздел был
    без них в ту минуту.

    Отсутствие поднятых - свойство МОМЕНТА, а не разметки, и потому проверки
    про них держатся за снимок, где они есть. Идентификатора предложения этот
    снимок не несёт, и неполноту его чтения приходится признавать.

    Возвращает:
        str: разметка снимка v8.
    """
    return (FIXTURES / f"{OLDER}.skeleton.txt").read_text(encoding="utf-8")


def _page() -> str:
    """Читает обрезанный снимок.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{TRIMMED}.skeleton.txt").read_text(encoding="utf-8")


def _parsed(html: str | None = None) -> MarketPage:
    """Разбирает список предложений.

    Аргументы:
        html (str | None): разметка либо None для снимка.

    Возвращает:
        MarketPage: разобранная страница.
    """
    return parse_market(html if html is not None else _page(), observed_at=WHEN)


def test_the_seller_comes_from_the_profile_link_not_from_data_user() -> None:
    """Требует читать продавца из ссылки на профиль, а НЕ из data-user.

    Атрибут data-user есть у строки тогда и только тогда, когда предложение
    поднято. Разбор по нему отдал бы продавца у двух процентов списка и выглядел
    бы работающим: поля заполняются, ошибок нет, а поднятые предложения
    показываются первыми и попадают в глаза первыми.

    Возвращает:
        None
    """
    tree = HTMLParser(_older())
    rows = tree.css("a.tc-item")

    with_attribute = [one for one in rows if "data-user" in (one.attributes or {})]
    assert 0 < len(with_attribute) < len(rows), (
        f"на снимке {len(with_attribute)} строк с data-user из {len(rows)}: "
        "проверка требует, чтобы были и те и другие"
    )

    page = _parsed()
    assert all(one.seller_href.is_observed for one in page.offers()), (
        "продавец прочитан не у всех строк - читается не тот носитель"
    )


def test_the_attribute_named_user_marks_promotion_not_the_seller() -> None:
    """Требует, чтобы совпадение data-user с поднятием было точным.

    Именно это совпадение и доказывает, что атрибут не о продавце: у поднятых он
    есть весь, у прочих нет вовсе.

    Возвращает:
        None
    """
    pairs = {(False, False): 0, (True, True): 0}
    other = 0
    for row in HTMLParser(_older()).css("a.tc-item"):
        promoted = "offer-promo" in ((row.attributes or {}).get("class") or "").split()
        has = "data-user" in (row.attributes or {})
        if (promoted, has) in pairs:
            pairs[(promoted, has)] += 1
        else:
            other += 1

    assert other == 0, f"{other} строк, где поднятие и data-user разошлись"
    assert pairs[(True, True)] and pairs[(False, False)], pairs


def test_lazy_rows_are_read_like_the_others() -> None:
    """Требует читать строки ленивой загрузки наравне с прочими.

    Класс говорит о ПОКАЗЕ, а не о наличии: разметка отдана целиком, догружать
    нечего. Разбор, пропускающий их, потерял бы девять десятых списка.

    Возвращает:
        None
    """
    page = _parsed()

    assert page.rows_lazy > 0, "на снимке нет ленивых строк - проверка пуста"
    assert page.rows_lazy < page.rows_total
    assert page.rows_accepted == page.rows_total, (
        f"собрано {page.rows_accepted} из {page.rows_total}: ленивые строки потеряны"
    )


def test_being_online_is_read_by_presence_not_by_value() -> None:
    """Требует читать признак «в сети» НАЛИЧИЕМ атрибута.

    Значение наблюдалось одно на всех строках, где атрибут есть, а отсутствие
    наблюдалось у восьмисот девятнадцати строк той же страницы. Читать значение
    было бы нечего.

    Возвращает:
        None
    """
    page = _parsed()
    online = [one for one in page.offers() if one.seller_online]
    offline = [one for one in page.offers() if not one.seller_online]

    assert online and offline, (
        f"на снимке {len(online)} в сети и {len(offline)} нет - нужны обе стороны"
    )

    rows = HTMLParser(_page()).css("a.tc-item")
    assert len(online) == len([one for one in rows if "data-online" in (one.attributes or {})])

    # А теперь то, что отличает НАЛИЧИЕ от значения: атрибут с пустым значением.
    # Читающий значение объявил бы такого продавца не в сети, а он в сети - иначе
    # атрибута не было бы вовсе. Значение при этом не наблюдалось никаким, кроме
    # одного, и правило потому и написано про наличие.
    html = _page()
    at = html.index('data-online="')
    end = html.index('"', at + len('data-online="'))
    emptied = html[: at + len('data-online="')] + html[end:]
    assert emptied != html, "значение не опустело"

    changed = _parsed(emptied)
    assert sum(1 for one in changed.offers() if one.seller_online) == len(online), (
        "признак «в сети» прочитан по значению, а не по наличию атрибута"
    )


def test_the_description_never_carries_the_server_name() -> None:
    """Требует читать вложенный узел описания, а не ячейку.

    Та же ловушка, что на странице своих лотов: внутри ячейки лежит обёртка с
    дублем колонки сервера, и полный текст ячейки равен имени сервера,
    приклеенному спереди к описанию.

    Возвращает:
        None
    """
    for offer in _parsed().offers():
        if not (offer.description_text.is_observed and offer.server_text.is_observed):
            continue
        assert not offer.description_text.value.startswith(offer.server_text.value), (
            f"в описание попало имя сервера: {offer.description_text.value[:40]!r}"
        )


def test_a_row_without_a_seller_link_is_a_defect() -> None:
    """Требует громко заметить строку без ссылки на продавца.

    Это единственный носитель продавца, годный для ВСЕХ строк.

    Возвращает:
        None
    """
    html = _page()
    spoiled = html.replace("data-href=", "data-was-href=", 1)
    assert spoiled != html, "ссылка не снялась"

    page = _parsed(spoiled)
    assert page.completeness is Completeness.PARTIAL
    assert "seller_link_missing" in {one.code for one in page.defects}
    assert any(one.severity is Severity.ROW for one in page.defects)

    with pytest.raises(IncompleteResultError):
        page.offers()


def test_an_empty_page_is_a_protocol_change() -> None:
    """Требует громкого отказа на странице без строк.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError, match="неотличим"):
        _parsed("<html><body></body></html>")


def test_the_trimmed_fixture_is_reproducible_from_the_observation() -> None:
    """Требует, чтобы обрезанный снимок ВОСПРОИЗВОДИЛСЯ из наблюдения.

    Производный файл, которого нельзя воспроизвести, ничем не лучше выдуманного.
    Проверка гоняет тот же инструмент по тому же наблюдению и сверяет вывод
    посимвольно.

    Пропускается без наблюдения: в репозиторий оно не кладётся - семь с половиной
    мегабайт.

    Возвращает:
        None
    """
    if not FULL.is_file():
        pytest.skip("наблюдения нет на диске: обрезку не с чем сверить")

    produced = ROOT / "tests" / "fixtures" / "pages" / "market-offers.check.skeleton.txt"
    try:
        run = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(ROOT / "tools" / "trim_skeleton.py"),
                str(FULL),
                "a.tc-item",
                str(produced),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert run.returncode == 0, run.stderr or run.stdout
        assert produced.read_text(encoding="utf-8") == _page(), (
            "обрезка не воспроизвелась: фикстура и наблюдение разошлись"
        )
    finally:
        produced.unlink(missing_ok=True)


def test_the_observation_carries_the_counts_the_contract_claims() -> None:
    """Сверяет числа контракта с самим наблюдением.

    Числа остались за наблюдением нарочно: обрезанный снимок о площадке не
    свидетельствует. Проверка пропускается без него.

    Возвращает:
        None
    """
    if not FULL.is_file():
        pytest.skip("наблюдения нет на диске: числа сверять не с чем")

    # ЧИСЛА БЕРУТСЯ ИЗ ОПИСАНИЯ ЗАХВАТА, а не пишутся здесь литералами.
    #
    # Прежде они стояли литералами - три тысячи одна строка, семьдесят
    # поднятых, - и держались за ОДНО наблюдение. Пересняли страницу, и
    # проверка стала сверять новый снимок со старыми числами: она падает не
    # потому, что разбор ошибся, а потому, что снимок другой.
    #
    # Описание захвата считает те же числа независимо от разбора - обходом
    # разметки в момент съёмки. Сверка с ним и есть настоящая проверка: два
    # счёта одного, сделанные разным кодом.
    import json

    provenance = json.loads(
        FULL.with_name(FULL.name.replace(".skeleton.txt", ".provenance.json")).read_text(
            encoding="utf-8"
        )
    )
    counts = provenance["original_counts"]

    page = parse_market(FULL.read_text(encoding="utf-8"), observed_at=WHEN)
    offers = page.offers(accept_incomplete=True)

    assert page.rows_total == counts["rows"], page.rows_total
    assert page.rows_lazy == counts["lazyload_hidden"], page.rows_lazy
    assert sum(1 for one in offers if one.promoted) == counts["offer_promo"]
    assert (
        len({one.seller_href.value for one in offers if one.seller_href.is_observed})
        == counts["distinct_seller_links"]
    )
    assert sum(1 for one in offers if one.offer_id.is_observed) == counts["rows_with_offer_id"], (
        "идентификатор прочитан не у всех строк, у которых он есть на странице"
    )
