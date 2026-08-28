"""Проверки разбора каталога.

Каждая ловушка страницы проверяется отдельно и явно. Ловушек семь, и каждая
даёт молчаливо неверный ответ - при полноте и нуле повреждений. Такое не ловится
проверкой «прочиталось столько-то»: неверный разбор тоже что-то читает.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from selectolax.parser import HTMLParser

from funora._catalog import parse_catalog
from funora._result import Completeness, Severity
from funora.errors import IncompleteResultError, ProtocolChangedError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок корня площадки. 842 карточки, 864 игры с вариантами, 4252 раздела.
ROOT: Final[str] = "root.logged.ru"

#: Момент наблюдения. Постоянен нарочно: разбор обязан быть повторяемым.
WHEN: Final[datetime] = datetime(2026, 8, 24, tzinfo=UTC)


def _page(name: str) -> str:
    """Читает снимок страницы.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_the_whole_catalog_is_read() -> None:
    """Требует прочесть каталог целиком и объявить полноту по указателю.

    Возвращает:
        None
    """
    page = parse_catalog(_page(ROOT), WHEN)

    assert page.completeness is Completeness.COMPLETE, (
        f"полнота {page.completeness}, причина {page.reason}"
    )
    assert page.reason == "alphabet_index_agrees"
    assert (page.cards_total, page.games_total, page.sections_total, page.letter_groups) == (
        842,
        864,
        4252,
        38,
    )
    assert not page.defects, [one.code for one in page.defects]


def test_the_favourites_block_does_not_double_the_games() -> None:
    """Требует читать только основной список, а не оба.

    Ловушка первая. Списков на странице два, и все восемь карточек избранного
    повторяют карточки основного - пересечение восемь из восьми. Обход по
    документу удвоил бы восемь игр, молча.

    Возвращает:
        None
    """
    tree = HTMLParser(_page(ROOT))
    by_document = len(tree.css(".promo-game-item"))
    in_catalog = len(tree.css(".promo-games-all .promo-game-item"))
    assert by_document == in_catalog + 8, (
        f"по документу {by_document}, в каталоге {in_catalog} - ловушка сменилась"
    )

    page = parse_catalog(_page(ROOT), WHEN)
    assert page.cards_total == in_catalog, (
        f"карточек {page.cards_total}: похоже, читаются оба списка"
    )

    ids = [one.game_id.value for one in page.games() if one.game_id.is_observed]
    assert len(set(ids)) == len(ids), "идентификаторы игр повторяются - списки сложились"


def test_a_favourite_game_missing_from_the_catalog_is_a_page_defect() -> None:
    """Требует заметить, если избранное перестанет повторять каталог.

    Избранное не читается на том основании, что новых сведений оно не даёт.
    Основание наблюдено, а не предположено, - и потому проверяется: перестань
    оно быть верным, разбор обязан сказать об этом, а не тихо терять игры.

    Возвращает:
        None
    """
    original = _page(ROOT)
    tree = HTMLParser(original)
    fav = tree.css_first(".promo-games-fav .game-title")
    known = (fav.attributes or {}).get("data-id")
    assert known, "у карточки избранного нет идентификатора"

    # Идентификатор карточки избранного подменяется на небывалый: игра остаётся
    # только в избранном и в каталоге больше не встречается.
    at = original.index("promo-games-fav")
    end = original.index("promo-games-all", at)
    head, block, tail = original[:at], original[at:end], original[end:]
    broken = head + block.replace(f'data-id="{known}"', 'data-id="НЕБЫВАЛЫЙ"') + tail
    assert broken != original, "подмена идентификатора не сработала"

    page = parse_catalog(broken, WHEN)
    assert "favourites_add_games" in {one.code for one in page.defects}
    assert page.reason == "page_defects"
    assert any(one.severity is Severity.PAGE for one in page.defects)


def test_hidden_variants_are_kept_and_marked() -> None:
    """Требует сохранять скрытые варианты игры, а не отбрасывать их.

    Ловушка вторая. Заголовков 872 при 842 карточках: четырнадцать карточек
    несут по нескольку, и лишние двадцать два помечены классом hidden.

    Это настоящие варианты - у каждого свой идентификатор и свой список
    разделов. Фильтр :not(.hidden) потерял бы их все.

    Возвращает:
        None
    """
    page = parse_catalog(_page(ROOT), WHEN)
    games = page.games()

    hidden = [one for one in games if not one.is_shown]
    assert len(hidden) == 22, f"скрытых вариантов {len(hidden)}"
    assert page.games_total == page.cards_total + len(hidden), (
        "число вариантов не сходится с числом карточек и скрытых"
    )

    # У каждого скрытого варианта - свой идентификатор и свои разделы.
    for one in hidden:
        assert one.game_id.is_observed, "у скрытого варианта нет идентификатора"
        assert one.sections, f"вариант {one.game_id.value!r} остался без разделов"


def test_the_section_list_is_paired_by_id_and_not_by_adjacency() -> None:
    """Требует сопоставлять список разделов с заголовком по идентификатору.

    Ловушка третья. Порядок детей карточки - сначала ВСЕ заголовки, потом
    переключатель, потом ВСЕ списки. Соседство держится лишь в 836 случаях из
    872, и разбор, взявший соседа, приписал бы четырнадцати играм чужие разделы.

    Возвращает:
        None
    """
    tree = HTMLParser(_page(ROOT))
    assert len(tree.css(".game-title + ul.list-inline")) == 836, (
        "соседство изменилось - ловушка сменилась, и её надо описывать заново"
    )
    assert len(tree.css(".promo-game-item > .game-title")) == 872

    page = parse_catalog(_page(ROOT), WHEN)
    multi = [one for one in page.games() if one.variant_index > 0]
    assert multi, "многовариантных карточек не нашлось"

    # У каждого варианта список нашёлся - значит сопоставление шло не по соседу:
    # у вариантов после первого сосед справа это другой заголовок, а не список.
    for one in multi:
        assert one.sections, (
            f"вариант {one.game_id.value!r} остался без разделов: похоже, "
            "список ищется по соседству"
        )
        # Разделы варианта принадлежат ему, а не соседнему.
        assert one.sections[0].is_main, (
            f"первый раздел варианта {one.game_id.value!r} не совпал с его же адресом"
        )


def test_an_unpairable_variant_is_a_row_defect() -> None:
    """Требует громко заметить вариант, которому не нашлось списка разделов.

    Взять соседний нельзя: порядок детей карточки этого не обещает. Молчаливая
    подмена приписала бы игре чужие разделы.

    Возвращает:
        None
    """
    original = _page(ROOT)
    tree = HTMLParser(original)
    title = tree.css_first(".promo-games-all .promo-game-item > .game-title")
    known = (title.attributes or {}).get("data-id")

    # Идентификатор списка меняется, у заголовка остаётся прежним.
    at = original.index(f'<ul class="list-inline" data-id="{known}"')
    broken = original[:at] + original[at:].replace(f'data-id="{known}"', 'data-id="НЕПАРНЫЙ"', 1)
    assert broken != original, "подмена идентификатора списка не сработала"

    page = parse_catalog(broken, WHEN)
    assert "section_list_not_paired" in {one.code for one in page.defects}

    orphan = next(one for one in page.games(accept_incomplete=True) if not one.sections)
    assert orphan.game_id.value == known


def test_the_main_section_is_marked_and_not_dropped() -> None:
    """Требует помечать главный раздел, а не выбрасывать его.

    Ловушка четвёртая. Адрес заголовка игры совпадает с адресом ПЕРВОЙ ссылки её
    списка - 864 раза из 864. Наивный сбор «заголовок плюс все разделы» удвоил
    бы главный раздел каждой игры.

    Выброшенный, он унёс бы с собой сведение о том, какой раздел у игры главный.

    Возвращает:
        None
    """
    page = parse_catalog(_page(ROOT), WHEN)
    games = page.games()

    main = sum(1 for one in games for section in one.sections if section.is_main)
    assert main == len(games), (
        f"главных разделов {main} при {len(games)} играх: у каждой обязан быть ровно один"
    )

    for one in games:
        assert one.sections[0].is_main, (
            f"у игры {one.game_id.value!r} главный раздел не первый в списке"
        )
        assert sum(1 for section in one.sections if section.is_main) == 1, (
            f"у игры {one.game_id.value!r} главных разделов больше одного"
        )


def test_the_section_kind_is_read_from_the_address() -> None:
    """Требует читать вид раздела из адреса - структурного признака у него нет.

    Все пять тысяч ссылок укладываются в четыре структурных отпечатка, и три из
    четырёх несут оба вида вперемешку. У самого узла ссылки нет ни класса, ни
    data-атрибута.

    Возвращает:
        None
    """
    page = parse_catalog(_page(ROOT), WHEN)
    kinds: dict[str | None, int] = {}
    for one in page.games():
        for section in one.sections:
            kinds[section.kind.or_none()] = kinds.get(section.kind.or_none(), 0) + 1

    assert set(kinds) == {"lots", "chips"}, f"виды разделов: {kinds}"
    assert kinds["lots"] == 4064
    assert kinds["chips"] == 188

    # Идентификатор раздела читается из того же адреса и не пуст.
    for one in page.games():
        for section in one.sections:
            assert section.section_id.is_observed, (
                f"у раздела {section.href.or_none()} не прочитан идентификатор"
            )


def test_an_unknown_address_shape_gives_no_kind() -> None:
    """Требует отказаться от вида раздела при незнакомой форме адреса.

    Наблюдены два вида. Незнакомая форма даёт ненаблюдённое значение, а не
    догадку: разбор, приписавший вид по остатку, соврал бы уверенно.

    Возвращает:
        None
    """
    original = _page(ROOT)
    # Подмена обязана попасть в ссылку РАЗДЕЛА внутри основного каталога.
    # Ни ссылка заголовка игры, ни указатель по буквам сюда не годятся: у
    # первой другой смысл, вторая разбором не читается вовсе.
    at = original.index("promo-games-all")
    at = original.index('<ul class="list-inline"', at)
    head, tail = original[:at], original[at:]
    broken = head + tail.replace('href="https://funpay.com/lots/', 'href="иное:', 1)
    assert broken != original, "подмена адреса раздела не сработала"

    page = parse_catalog(broken, WHEN)
    odd = [
        section
        for one in page.games(accept_incomplete=True)
        for section in one.sections
        if not section.kind.is_observed
    ]
    assert odd, "незнакомый адрес прошёл как известный вид"
    assert odd[0].kind.reason == "section_href_shape_unknown"
    assert not odd[0].section_id.is_observed


def test_letter_groups_are_not_mistaken_for_games() -> None:
    """Требует не считать буквенные разделители карточками игр.

    Они лежат в том же контейнере и прямыми детьми того же узла.

    Возвращает:
        None
    """
    page = parse_catalog(_page(ROOT), WHEN)
    assert page.letter_groups == 38
    assert page.cards_total == 842, "разделители попали в число карточек"

    for one in page.games():
        assert one.game_id.is_observed, "у карточки нет идентификатора - похоже, это разделитель"


def test_the_alphabet_index_decides_completeness() -> None:
    """Требует объявлять полноту по указателю, а не по ненаходке догрузки.

    Довод положительный: число ссылок в указателе равно числу групп на странице,
    то есть указатель не обещает ни одной группы, которой на странице нет.

    Возвращает:
        None
    """
    original = _page(ROOT)
    assert parse_catalog(original, WHEN).completeness is Completeness.COMPLETE

    # Указателя нет - полноты нет. Не потому, что что-то сломалось, а потому,
    # что довода в её пользу не осталось.
    without = original.replace('class="nav-abc"', 'class="nav-abc-renamed"', 1)
    page = parse_catalog(without, WHEN)
    assert page.completeness is Completeness.PARTIAL
    assert page.reason == "alphabet_index_missing"
    with pytest.raises(IncompleteResultError, match="результат неполон"):
        page.games()


def test_an_index_that_promises_more_than_the_page_shows_is_a_defect() -> None:
    """Требует заметить расхождение указателя со страницей.

    Указатель, обещающий группу, которой на странице нет, - это ровно тот
    случай, ради которого он и читается: страница показала не всё.

    Возвращает:
        None
    """
    original = _page(ROOT)
    at = original.index('class="promo-game-list-title"')
    end = original.index("</div>", at) + len("</div>")
    start = original.rindex("<div", 0, at)
    broken = original[:start] + original[end:]
    assert broken != original, "вырезание группы не сработало"

    page = parse_catalog(broken, WHEN)
    assert page.letter_groups == 37, f"групп осталось {page.letter_groups}"
    assert "alphabet_index_disagrees" in {one.code for one in page.defects}
    assert page.completeness is Completeness.PARTIAL
    assert page.reason == "alphabet_index_disagrees"


def test_a_page_without_the_catalog_is_a_protocol_change() -> None:
    """Требует громкого отказа там, где основного каталога нет вовсе.

    Возвращает:
        None
    """
    broken = _page(ROOT).replace("promo-games-all", "promo-games-renamed")
    with pytest.raises(ProtocolChangedError, match="основного каталога"):
        parse_catalog(broken, WHEN)


def test_a_catalog_without_cards_is_a_protocol_change() -> None:
    """Требует громкого отказа там, где каталог есть, а карточек нет.

    Пустой каталог вернуть нельзя: он неотличим от смены разметки.

    Возвращает:
        None
    """
    broken = _page(ROOT).replace("promo-game-item", "promo-game-renamed")
    with pytest.raises(ProtocolChangedError, match="ни одной карточки"):
        parse_catalog(broken, WHEN)
