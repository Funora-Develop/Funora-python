"""Проверки разбора отзывов.

Каждая проверка здесь двусторонняя: положительная половина требует, чтобы
разбор прочитал то, что в снимке есть, отрицательная - чтобы он НЕ прочитал
того, чего там нет. Односторонняя проверка ловит только один вид поломки, а
второй пропускает молча - и именно второй здесь дороже: разбор, объявивший
пустоту наблюдением, отдаёт продавцу выдуманную оценку.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from selectolax.parser import HTMLParser

from funora._result import Completeness, Severity
from funora._reviews import parse_reviews_page
from funora.errors import IncompleteResultError, ProtocolChangedError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок собственного профиля. Шесть отзывов, все пятизвёздочные.
PROFILE: Final[str] = "user.logged.ru"

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


def _without_reviews(html: str) -> str:
    """Убирает со страницы все отзывы, оставляя контейнер строк на месте.

    Резать по смещениям в тексте наивно нельзя: фильтр отзывов стоит в разметке
    ВЫШЕ таблицы, и поиск закрывающего тега от него попадает не туда. Здесь
    считается баланс открывающих и закрывающих тегов начиная с обёртки отзыва.

    Аргументы:
        html (str): содержимое снимка.

    Возвращает:
        str: та же страница без единого отзыва.
    """
    out = html
    while True:
        start = out.find('<div class="review-container">')
        if start < 0:
            return out
        depth = 0
        for match in re.finditer("<div[ >]|</div>", out[start:]):
            depth += 1 if match.group() != "</div>" else -1
            if depth == 0:
                out = out[:start] + out[start + match.end() :]
                break
        else:  # pragma: no cover - разметка снимка сбалансирована
            raise AssertionError("обёртка отзыва не закрыта")


def test_the_profile_gives_every_review_it_shows() -> None:
    """Требует, чтобы прочитались все шесть отзывов снимка.

    Возвращает:
        None
    """
    page = parse_reviews_page(_page(PROFILE), WHEN)

    assert page.completeness is Completeness.COMPLETE, (
        f"полнота {page.completeness}, причина {page.reason}, повреждений "
        f"{[one.code for one in page.defects]}"
    )
    assert page.reason == "all_rows_parsed"
    assert (page.rows_total, page.rows_accepted, page.rows_rejected) == (6, 6, 0)
    assert len(page.rows()) == 6


def test_every_field_of_every_review_is_read() -> None:
    """Требует, чтобы у каждого отзыва прочиталось каждое поле.

    Проверка перебирает поля по именам, а не по одному: поле, добавленное в
    запись и забытое в разборе, иначе осталось бы ненаблюдённым, а никакая
    проверка бы этого не сказала.

    Возвращает:
        None
    """
    fields = (
        "rating",
        "author_name",
        "author_href",
        "order_href",
        "text",
        "date_text",
        "detail_text",
    )
    for review in parse_reviews_page(_page(PROFILE), WHEN).rows():
        for name in fields:
            value = getattr(review, name)
            assert value.is_observed, (
                f"отзыв {review.row_index}: поле {name} не прочитано, причина "
                f"{value.reason!r}. Снимок его несёт у всех шести строк"
            )


def test_the_rating_is_a_number_from_one_to_five() -> None:
    """Требует, чтобы оценка была целым, а не строкой класса.

    Вызывающий сравнивает оценку с четырьмя. Отдай разбор строку - сравнение
    «10» с «4» дало бы меньше, и продавец с десятибалльной шкалой узнал бы об
    этом от покупателя.

    Возвращает:
        None
    """
    for review in parse_reviews_page(_page(PROFILE), WHEN).rows():
        value = review.rating.value
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"отзыв {review.row_index}: оценка пришла как {type(value).__name__}"
        )
        assert 1 <= value <= 5, f"отзыв {review.row_index}: оценка {value} вне шкалы"


def test_an_unknown_rating_class_is_not_observed_rather_than_guessed() -> None:
    """Требует отказаться от незнакомого класса оценки, а не подхватить его.

    Отрицательная половина правила о закрытом перечислении. Смена шкалы у
    площадки обязана быть заметна: подхваченная молча, она приписала бы отзыву
    оценку, которой площадка не ставила.

    Возвращает:
        None
    """
    broken = _page(PROFILE).replace('class="rating5"', 'class="rating9"')
    page = parse_reviews_page(broken, WHEN)

    assert page.completeness is Completeness.PARTIAL, (
        f"полнота {page.completeness}: незнакомая шкала прошла как полное чтение"
    )
    codes = {one.code for one in page.defects}
    assert "rating_class_not_recognised" in codes, f"повреждения: {sorted(codes)}"

    for review in page.rows(accept_incomplete=True):
        assert not review.rating.is_observed, (
            f"отзыв {review.row_index}: класс rating9 прочитан как оценка {review.rating.or_none()}"
        )
        assert review.rating.reason == "rating_class_not_recognised"


def test_rating_carriers_that_disagree_give_no_rating() -> None:
    """Требует отказаться от оценки, когда два её носителя разошлись.

    В строке два узла оценки - широкий макет и узкий. Взять первый попавшийся
    значило бы выбрать наугад ту звезду, по которой покупатель судит о продавце.

    Возвращает:
        None
    """
    # Меняется только узкий макет: у широкого класс остаётся прежним, и носители
    # расходятся ровно в одной строке из шести.
    broken = _page(PROFILE).replace(
        '<div class="review-item-rating visible-xs">\n'
        '                                        <div class="rating">\n'
        '                                          <div class="rating5"></div>',
        '<div class="review-item-rating visible-xs">\n'
        '                                        <div class="rating">\n'
        '                                          <div class="rating3"></div>',
        1,
    )
    assert broken != _page(PROFILE), "подмена узкого макета не сработала"

    page = parse_reviews_page(broken, WHEN)
    disagreed = [one for one in page.rows(accept_incomplete=True) if not one.rating.is_observed]
    assert len(disagreed) == 1, f"разошёлся один отзыв, а без оценки осталось {len(disagreed)}"
    assert disagreed[0].rating.reason == "rating_carriers_disagree"
    assert "rating_carriers_disagree" in {one.code for one in page.defects}


def test_two_author_links_that_disagree_give_no_link() -> None:
    """Требует отказаться от адреса автора, когда его носители разошлись.

    Та же болезнь, что и у оценки, и то же лечение. Разбор списка продаж уже
    спотыкался на ней: брал первый [data-href] строки, в снимке их было два, оба
    вели на одного человека, и ошибка не была видна.

    Возвращает:
        None
    """
    original = _page(PROFILE)
    broken = original.replace(
        '<div class="review-item-photo">\n'
        '                                        <a href="https://funpay.com/users/{n23}/">',
        '<div class="review-item-photo">\n'
        '                                        <a href="https://funpay.com/users/{n99}/">',
        1,
    )
    assert broken != original, "подмена адреса на аватаре не сработала"

    page = parse_reviews_page(broken, WHEN)
    first = page.rows(accept_incomplete=True)[0]
    assert not first.author_href.is_observed, (
        f"адреса разошлись, а разбор вернул {first.author_href.or_none()}"
    )
    assert first.author_href.reason == "author_href_mismatch"
    assert "author_href_mismatch" in {one.code for one in page.defects}


def test_a_page_without_the_table_is_a_protocol_change_not_an_empty_list() -> None:
    """Требует громкого отказа там, где таблицы отзывов нет вовсе.

    Профиля без отзывов проект не видел. Пока снимка нет, отсутствие таблицы
    неотличимо от переименования класса, и объявлять по нему пустой список
    значило бы сказать «отзывов нет» там, где верно «я не понял страницу».

    Возвращает:
        None
    """
    without_table = re.sub(r'class="dyn-table"', 'class="dyn-table-renamed"', _page(PROFILE))
    with pytest.raises(ProtocolChangedError, match="контейнера таблицы отзывов"):
        parse_reviews_page(without_table, WHEN)

    without_body = re.sub(
        r'class="dyn-table-body"', 'class="dyn-table-body-renamed"', _page(PROFILE)
    )
    with pytest.raises(ProtocolChangedError, match="контейнера строк"):
        parse_reviews_page(without_body, WHEN)


def test_rows_that_cannot_be_parsed_at_all_are_a_protocol_change() -> None:
    """Требует громкого отказа там, где кандидаты есть, а собрать нечего.

    Возвращает:
        None
    """
    renamed = _page(PROFILE).replace('class="review-item"', 'class="review-item-renamed"')
    with pytest.raises(ProtocolChangedError, match="контейнера строк|кандидатов в отзывы"):
        parse_reviews_page(renamed, WHEN)


def test_the_wrapper_count_is_an_independent_check() -> None:
    """Требует, чтобы расхождение числа обёрток было замечено.

    Третий счёт заведён затем, что первые два могут согласиться друг с другом
    внутри изменившейся разметки: селектор строки и прямые потомки контейнера
    считают одно и то же множество узлов.

    Возвращает:
        None
    """
    broken = _page(PROFILE).replace('class="review-container"', 'class="review-box"', 2)
    page = parse_reviews_page(broken, WHEN)

    codes = {one.code for one in page.defects}
    assert "wrapper_count_mismatch" in codes, f"повреждения: {sorted(codes)}"
    assert page.completeness is Completeness.PARTIAL
    assert page.reason == "page_defects"
    assert any(
        one.severity is Severity.PAGE and one.code == "wrapper_count_mismatch"
        for one in page.defects
    )


def test_an_incomplete_read_refuses_to_hand_out_rows_silently() -> None:
    """Требует признания неполноты прежде выдачи неполного списка.

    Возвращает:
        None
    """
    broken = _page(PROFILE).replace('class="rating5"', 'class="rating9"')
    page = parse_reviews_page(broken, WHEN)

    with pytest.raises(IncompleteResultError, match="результат неполон"):
        page.rows()

    assert len(page.rows(accept_incomplete=True)) == 6
    assert len(page) == 6, "длина доступна без признания неполноты"


def test_the_date_is_a_string_and_not_a_moment() -> None:
    """Требует, чтобы дата осталась строкой.

    Разбирать локализованную человеческую запись значило бы угадывать по одной
    локали из трёх. Проверка стоит здесь затем, что соблазн «а давайте распарсим»
    возвращается при каждом чтении этого модуля.

    Возвращает:
        None
    """
    for review in parse_reviews_page(_page(PROFILE), WHEN).rows():
        assert isinstance(review.date_text.value, str), (
            f"отзыв {review.row_index}: дата пришла как {type(review.date_text.value).__name__}"
        )


def test_an_empty_reviews_list_is_unknown_and_not_a_complete_read() -> None:
    """Требует неизвестной полноты там, где отзывов не нашлось ни одного.

    У списка продаж пустота даёт полное чтение - там есть снимок страницы без
    продаж и позитивный признак на ней. Здесь снимка нет, признака нет, и
    объявить полноту значило бы сказать «отзывов нет» там, где верно «я не
    понял страницу».

    Разница дорога тем же, чем была дорога у продаж: полное чтение снимает
    курсор наблюдения, и объявленная пустота поглотила бы первый отзыв.

    Возвращает:
        None
    """
    original = _page(PROFILE)
    emptied = _without_reviews(original)
    assert emptied != original, "вырезание отзывов не сработало"
    assert HTMLParser(emptied).css_first(".dyn-table-body") is not None, (
        "вырезание задело и сам контейнер строк: проверка мерила бы отсутствие "
        "контейнера, а не пустой список"
    )

    page = parse_reviews_page(emptied, WHEN)

    assert page.rows_total == 0, (
        f"строк осталось {page.rows_total}: вырезание отзывов не сработало, и "
        "проверка ничего не проверяет"
    )
    assert page.completeness is Completeness.UNKNOWN, (
        f"полнота {page.completeness}: пустота объявлена знанием, которого нет"
    )
    assert page.reason == "empty_list_not_observed"

    with pytest.raises(IncompleteResultError, match="результат неполон"):
        page.rows()
