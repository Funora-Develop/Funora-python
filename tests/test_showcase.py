"""Проверки разбора витрины продавца."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from selectolax.parser import HTMLParser

from funora._result import Completeness, Severity
from funora._showcase import parse_showcase
from funora.errors import IncompleteResultError, ProtocolChangedError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок профиля. Тринадцать разделов витрины, сто пятьдесят восемь предложений.
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


def test_the_reviews_block_is_not_taken_for_a_showcase_section() -> None:
    """Требует не принимать блок отзывов за раздел витрины.

    Класс offer на профиле носят ЧЕТЫРНАДЦАТЬ узлов, а разделов витрины
    тринадцать: четырнадцатый - блок отзывов, переиспользующий тот же класс.

    Разбор, взявший .offer за раздел, нашёл бы раздел без единого предложения и
    без заголовка - и отдал бы его вызывающему как пустую категорию.

    Возвращает:
        None
    """
    tree = HTMLParser(_page(PROFILE))
    assert len(tree.css(".offer")) == 14, "число узлов с классом offer изменилось"
    assert len(tree.css(".offer-tc-container")) == 13, "число контейнеров изменилось"
    assert len(tree.css(".offer")[-1].css(".review-item")) == 6, (
        "четырнадцатый узел больше не блок отзывов - ловушка сменилась, и её надо описывать заново"
    )

    page = parse_showcase(_page(PROFILE), WHEN)
    assert page.sections_total == 13, f"разделов {page.sections_total}"
    for one in page.sections(accept_incomplete=True):
        assert one.offers, f"раздел {one.position} пуст - похоже, взят блок отзывов"
        assert one.title_text.is_observed, f"раздел {one.position} без заголовка"


def test_every_offer_field_that_the_page_shows_is_read() -> None:
    """Требует читать все поля предложения, которые страница показывает.

    Возвращает:
        None
    """
    page = parse_showcase(_page(PROFILE), WHEN)
    assert page.offers_total == 158, f"предложений {page.offers_total}"

    for section in page.sections(accept_incomplete=True):
        for offer in section.offers:
            for name in (
                "offer_href",
                "description_text",
                "price_text",
                "currency_symbol_text",
                "sort_value",
                "auto_delivery",
            ):
                value = getattr(offer, name)
                assert value.is_observed, (
                    f"раздел {section.position}, строка {offer.row_index}: поле {name} "
                    f"не прочитано, причина {value.reason!r}"
                )


def test_optional_columns_are_absent_in_some_sections_and_present_in_others() -> None:
    """Требует, чтобы разный состав колонок был наблюдением, а не поломкой.

    Колонки остатка и сервера есть НЕ ВО ВСЕХ разделах. Разбор, объявивший их
    обязательными, сыпал бы повреждениями на исправной странице.

    Возвращает:
        None
    """
    page = parse_showcase(_page(PROFILE), WHEN)
    offers = [one for section in page.sections(accept_incomplete=True) for one in section.offers]

    with_amount = [one for one in offers if one.amount_text.is_observed]
    without = [one for one in offers if not one.amount_text.is_observed]

    assert with_amount, "колонки остатка не нашлось ни у одного предложения"
    assert without, "колонка остатка нашлась у всех - разный состав колонок пропал"
    assert not page.defects, (
        f"повреждения {[one.code for one in page.defects]}: разный состав колонок принят за поломку"
    )


def test_the_offer_address_is_kept_whole_and_not_parsed() -> None:
    """Требует отдавать адрес предложения целиком, не разбирая строку запроса.

    Идентификатор лежит в строке запроса, а формат скелета заменяет её одной
    подписью: разобрать параметр по снимку нельзя, и проверить такой разбор тоже
    нечем. Правило, которое ни одна проверка не покрывает, уже однажды обошлось
    дорого - на разборе суммы со страницы заказа.

    Возвращает:
        None
    """
    page = parse_showcase(_page(PROFILE), WHEN)
    offers = [one for section in page.sections(accept_incomplete=True) for one in section.offers]

    addresses = [one.offer_href.value for one in offers]
    assert len(set(addresses)) == len(addresses), "адреса предложений повторяются"
    for address in addresses:
        assert address.startswith("https://funpay.com/lots/offer?"), address


def test_the_read_is_never_declared_complete() -> None:
    """Требует не объявлять витрину полной.

    Признака усечения на странице нет ни одного - но у семи разделов из
    тринадцати ровно по двадцать строк. Двадцать круглое число, и семь
    совпадений подряд на случайность не похожи: это положительный довод в пользу
    обрезания, а не отсутствие довода против.

    Возвращает:
        None
    """
    page = parse_showcase(_page(PROFILE), WHEN)

    assert page.completeness is Completeness.PARTIAL
    assert page.reason == "sections_look_capped", page.reason
    assert page.capped_sections == 7, f"разделов с двадцатью строками {page.capped_sections}"

    with pytest.raises(IncompleteResultError, match="результат неполон"):
        page.sections()


def test_without_capped_sections_the_reason_says_the_cap_is_unobserved() -> None:
    """Требует различать «похоже на обрезание» и «признака полноты нет».

    Причины разные нарочно: будущее наблюдение уточнит только первый случай, и
    по общей причине его было бы не найти.

    Страница здесь собрана малая, а не обрезана из снимка: обрезка снимка
    снимала бы строки из одного раздела, и разделов с предельным числом строк
    оставалось бы столько же. Проверка, не проверяющая ветку, хуже отсутствующей.

    Возвращает:
        None
    """
    tiny = (
        "<html><body><div class='offer'>"
        "<div class='offer-list-title'><h3>"
        "<a href='https://funpay.com/lots/1/'>раздел</a></h3></div>"
        "<div class='offer-list-title-button'>"
        "<a href='https://funpay.com/lots/1/trade'>правка</a></div>"
        "<div class='offer-tc-container'>"
        "<a class='tc-item' href='https://funpay.com/lots/offer?id=1'>"
        "<div class='tc-desc'><div class='tc-desc-text'>первое</div></div>"
        "<div class='tc-price' data-s='10'><div>10<span class='unit'>x</span></div></div>"
        "</a>"
        "<a class='tc-item' href='https://funpay.com/lots/offer?id=2'>"
        "<div class='tc-desc'><div class='tc-desc-text'>второе</div></div>"
        "<div class='tc-price' data-s='20'><div>20<span class='unit'>x</span></div></div>"
        "</a>"
        "</div></div></body></html>"
    )

    page = parse_showcase(tiny, WHEN)
    assert page.sections_total == 1
    assert page.offers_total == 2
    assert page.capped_sections == 0, "разделов с предельным числом строк быть не должно"
    assert page.completeness is Completeness.PARTIAL, (
        "малая витрина всё равно неполна: признака полноты у страницы нет ни одного"
    )
    assert page.reason == "showcase_cap_unobserved", page.reason


def test_offers_outside_sections_are_a_page_defect() -> None:
    """Требует заметить строки, оказавшиеся вне контейнеров разделов.

    Счёт внутри разделов и счёт по документу обязаны сойтись. Разойдись они -
    часть предложений выпала бы молча, при полноте и нуле повреждений.

    Возвращает:
        None
    """
    original = _page(PROFILE)
    broken = original.replace('class="offer-tc-container"', 'class="offer-tc-gone"', 1)
    assert broken != original, "подмена контейнера не сработала"

    page = parse_showcase(broken, WHEN)
    assert "offers_outside_sections" in {one.code for one in page.defects}
    assert page.reason == "page_defects"
    assert any(one.severity is Severity.PAGE for one in page.defects)


def test_a_section_without_a_title_is_a_row_defect() -> None:
    """Требует заметить раздел без заголовка.

    Заголовок лежит НЕ внутри контейнера строк, а рядом с ним. Промах в поиске
    здесь не заметить иначе: разбор вернул бы раздел без названия и без ссылки.

    Возвращает:
        None
    """
    broken = _page(PROFILE).replace('class="offer-list-title"', 'class="offer-list-gone"', 1)
    page = parse_showcase(broken, WHEN)

    assert "section_title_missing" in {one.code for one in page.defects}
    first = page.sections(accept_incomplete=True)[0]
    assert not first.title_text.is_observed
    assert not first.category_href.is_observed


def test_a_page_without_containers_is_a_protocol_change() -> None:
    """Требует громкого отказа там, где контейнеров разделов нет вовсе.

    Возвращает:
        None
    """
    broken = _page(PROFILE).replace("offer-tc-container", "offer-tc-renamed")
    with pytest.raises(ProtocolChangedError, match="контейнера раздела витрины"):
        parse_showcase(broken, WHEN)


def test_the_manage_link_carries_the_section_id() -> None:
    """Требует читать ссылку на управление лотами раздела.

    Другого места, где лежит номер раздела, на профиле нет: без этой ссылки
    страницу управления не открыть, а значит и вопрос об усечении витрины не
    закрыть.

    Возвращает:
        None
    """
    page = parse_showcase(_page(PROFILE), WHEN)
    for section in page.sections(accept_incomplete=True):
        assert section.manage_href.is_observed, f"раздел {section.position} без ссылки управления"
        assert section.manage_href.value.endswith("/trade"), section.manage_href.value


def test_the_price_keeps_the_currency_symbol_out_of_itself() -> None:
    """Требует читать цену собственным текстом узла, без знака валюты.

    Знак лежит отдельным узлом внутри, и текст целиком склеил бы их.

    Проверка появилась после мутации: подмена собственного текста на полный
    ПЕРЕЖИЛА набор. Прежние проверки смотрели только на то, прочитано ли поле, и
    ни одна не смотрела на значение.

    Возвращает:
        None
    """
    page = parse_showcase(_page(PROFILE), WHEN)
    offers = [one for section in page.sections(accept_incomplete=True) for one in section.offers]

    for one in offers:
        assert one.currency_symbol_text.value not in one.price_text.value, (
            f"знак валюты {one.currency_symbol_text.value!r} попал в цену {one.price_text.value!r}"
        )
        assert one.price_text.value, "цена пуста: собственный текст узла не нашёлся"


def test_auto_delivery_is_read_from_the_icon_and_not_from_its_absence() -> None:
    """Требует читать признак автовыдачи наличием значка, а не отсутствием.

    Проверка держит ОБЕ стороны: у четырёх предложений снимка значок есть, у ста
    пятидесяти четырёх нет. Проверка одной стороны прошла бы и при перевёрнутом
    правиле - мутация это и показала.

    Возвращает:
        None
    """
    tree = HTMLParser(_page(PROFILE))
    assert len(tree.css(".auto-dlv-icon")) == 4, (
        "число значков автовыдачи изменилось - проверка перестала держать обе стороны"
    )

    page = parse_showcase(_page(PROFILE), WHEN)
    offers = [one for section in page.sections(accept_incomplete=True) for one in section.offers]

    with_icon = [one for one in offers if one.auto_delivery.value is True]
    without = [one for one in offers if one.auto_delivery.value is False]

    assert len(with_icon) == 4, f"с автовыдачей {len(with_icon)}, а значков четыре"
    assert len(without) == len(offers) - 4, f"без автовыдачи {len(without)}"


def test_offer_fields_are_looked_for_inside_the_row() -> None:
    """Требует искать поля предложения внутри строки, а не у соседей.

    Проверка различительная: правится ОДНА строка, и меняться обязана тоже одна.
    Сверять непустоту мало - разбор, берущий описание у родителя, вернул бы
    непустое описание каждой строке, просто чужое.

    Возвращает:
        None
    """
    original = _page(PROFILE)
    page_before = parse_showcase(original, WHEN)
    rows_before = [
        one for section in page_before.sections(accept_incomplete=True) for one in section.offers
    ]
    second = rows_before[1].description_text.value
    assert second != rows_before[0].description_text.value, (
        "описания первых двух предложений совпали - подмена ниже ничего не покажет"
    )

    at = original.index(second)
    broken = original[:at] + "T4:cXXX" + original[at + len(second) :]
    assert broken != original, "подмена описания не сработала"

    page = parse_showcase(broken, WHEN)
    rows = [one for section in page.sections(accept_incomplete=True) for one in section.offers]

    assert rows[1].description_text.value == "T4:cXXX", (
        f"вторая строка не изменилась: {rows[1].description_text.value!r}. Разбор "
        "берёт описание не из своей строки"
    )
    for index in (0, 2, 3):
        assert rows[index].description_text.value == rows_before[index].description_text.value, (
            f"строка {index} изменилась вслед за второй. Разбор ищет поле у соседей"
        )
