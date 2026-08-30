"""Проверки чужого профиля продавца.

ЗАЧЕМ ОТДЕЛЬНЫЙ НАБОР. Разбор отзывов до сих пор проверялся только на СВОЁМ
профиле, а у чужого нет целых полей: имени автора отзыва, ссылки на его
профиль, ссылки на его фото и ссылки на заказ.

Проверка на своём объявляла эти поля наблюдёнными - и была права ровно про свой
профиль. Операция reviews.get читает любой, и у чужого выходит иначе.

Снимок снят 30.08.2026. Страница видна любому посетителю без входа.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from selectolax.parser import HTMLParser

from funora._result import Completeness
from funora._reviews import parse_reviews_page
from funora._showcase import parse_showcase

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

FOREIGN: Final[str] = "user-foreign.logged.ru"
OWN: Final[str] = "user.logged.ru"

WHEN: Final[datetime] = datetime(2026, 8, 30, tzinfo=UTC)


def _page(name: str) -> str:
    """Читает снимок профиля.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: разметка скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_a_foreign_profile_shows_no_review_authors() -> None:
    """Требует, чтобы у чужих отзывов автора не было - и это НЕ поломка.

    Совпадение полное и в обе стороны: на своём профиле поля есть у всех
    отзывов, на чужом нет ни у одного. Так устроена площадка.

    Возвращает:
        None
    """
    foreign = parse_reviews_page(_page(FOREIGN), observed_at=WHEN)
    own = parse_reviews_page(_page(OWN), observed_at=WHEN)

    theirs = foreign.rows(accept_incomplete=True)
    ours = own.rows(accept_incomplete=True)

    assert theirs and ours, "один из снимков без отзывов - сравнивать нечего"

    assert not any(one.author_name.is_observed for one in theirs), (
        "на чужом профиле нашёлся автор отзыва: либо площадка изменилась, либо "
        "разбор ищет его по документу и подобрал имя из виджета переписки"
    )
    assert not any(one.order_href.is_observed for one in theirs)

    assert all(one.author_name.is_observed for one in ours), (
        "на СВОЁМ профиле автор пропал: проверка требует обеих сторон, иначе "
        "она ничего не различает"
    )
    assert all(one.order_href.is_observed for one in ours)


def test_the_only_name_on_a_foreign_page_is_not_a_reviewer() -> None:
    """ЗАКРЫВАЕТ ЛОВУШКУ, которую видно только на чужом профиле.

    Узел .media-user-name a на чужой странице ЕСТЬ - ровно один, и лежит он в
    шапке виджета переписки. Это имя САМОГО ПРОДАВЦА, а не автора отзыва.

    Разбор, ищущий автора по документу, приписал бы каждому отзыву имя того,
    кому отзыв написан. Ошибка правдоподобная: поле заполнено, отказа нет, имя
    настоящее.

    Возвращает:
        None
    """
    tree = HTMLParser(_page(FOREIGN))

    found = tree.css(".media-user-name a")
    assert len(found) == 1, (
        f"на чужой странице {len(found)} узлов имени: проверка держится на том, "
        "что он ровно один и лежит вне отзывов"
    )

    inside = [one for one in tree.css(".review-item") if one.css(".media-user-name a")]
    assert not inside, (
        f"{len(inside)} отзывов содержат узел имени: ловушка перестала быть "
        "ловушкой, и проверку надо переписать"
    )


def test_a_shown_continue_button_means_the_reviews_are_incomplete() -> None:
    """Впервые проверяет ПОЛОЖИТЕЛЬНУЮ ветку неполноты отзывов.

    Прежде кнопка догрузки встречалась только скрытой: ветка «показана - значит
    прочитано не всё» стояла написанной и ни разу не проверенной настоящей
    страницей.

    Возвращает:
        None
    """
    foreign = HTMLParser(_page(FOREIGN)).css_first("button.dyn-table-continue")
    own = HTMLParser(_page(OWN)).css_first("button.dyn-table-continue")

    assert foreign is not None and own is not None, "кнопка догрузки пропала со снимка"

    foreign_classes = ((foreign.attributes or {}).get("class") or "").split()
    own_classes = ((own.attributes or {}).get("class") or "").split()

    assert "hidden" not in foreign_classes, "на чужом профиле кнопка скрыта - ветка не проверена"
    assert "hidden" in own_classes, "на своём профиле кнопка показана - сторон снова одна"

    assert parse_reviews_page(_page(FOREIGN), observed_at=WHEN).completeness is (
        Completeness.PARTIAL
    )
    assert parse_reviews_page(_page(OWN), observed_at=WHEN).completeness is Completeness.COMPLETE


def test_the_showcase_shows_fewer_than_twenty_when_there_are_fewer() -> None:
    """Требует, чтобы двадцать не считалось размером страницы у всех разделов.

    На чужом профиле четыре раздела: двадцать, тринадцать, девять и девять.
    Разделы меньше двадцати показывают своё настоящее число - значит двадцать
    не универсальная порция, а предел, до которого доходят не все.

    ЧТО ЭТИМ НЕ ДОКАЗАНО: что раздел с двадцатью вправду обрезан. Для этого
    нужно знать, сколько у продавца предложений в том разделе на самом деле, а
    со страницы профиля это не видно.

    Возвращает:
        None
    """
    page = parse_showcase(_page(FOREIGN), observed_at=WHEN)
    sizes = [len(one.offers) for one in page.sections(accept_incomplete=True)]

    assert sizes == [20, 13, 9, 9], f"разделы на снимке: {sizes}"
    assert any(one < 20 for one in sizes), (
        "все разделы по двадцать - тогда снимок не различает предел и порцию"
    )
    assert page.capped_sections == 1, (
        f"обрезанными сочтены {page.capped_sections} разделов, а ровно двадцать "
        "строк только у одного"
    )


def test_a_foreign_profile_has_no_avatar_editor() -> None:
    """Закрепляет отличие, ради которого снимок и снимался.

    Редактор аватара есть только у владельца. Пока разбор проверялся на одном
    своём профиле, ни одно правило не могло опереться на его отсутствие - и
    заметить это было неоткуда.

    Возвращает:
        None
    """
    assert not HTMLParser(_page(FOREIGN)).css("a.js-edit-avatar")
    assert HTMLParser(_page(OWN)).css("a.js-edit-avatar")
