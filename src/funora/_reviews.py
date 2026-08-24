"""Разбор отзывов со страницы профиля продавца.

Модуль чистый по тем же причинам, что и разбор списка продаж: вход - разметка и
момент наблюдения, выход - страница записей, и повторить его на сохранённом
снимке спустя полгода обязано давать тот же результат.

Три решения отличают его от разбора продаж.

Оценка читается из ИМЕНИ КЛАССА, а не из текста. У узла оценки текста нет
вовсе: площадка рисует звёзды стилями, а число прячет в классе - div.rating5.
Перечисление закрыто по наблюдению, а не по догадке: площадка сама перечислила
все пять уровней в выпадающем списке фильтра отзывов.

Узлов оценки в строке ДВА, а не один - широкий макет и узкий. Значение в них
одно и то же, и разбор обязан их сверить. Взять первый попавшийся значило бы
повторить ошибку, на которой уже спотыкался разбор продаж: он брал первый
[data-href] строки, в снимке их было два, оба вели на одного человека, и ошибка
не была видна ровно до того дня, когда перестала бы.

Полнота здесь слабее, чем у продаж, и это объявлено. COMPLETE означает
«разобраны все строки, которые страница отдала», а не «прочитаны все отзывы
продавца». Сверить не с чем: управления постраничным выводом на наблюдённой
странице нет ни одного узла, а число отзывов в шапке показано локализованным
текстом в две строки. Разница внесена в реестр как reviews_page_totality и ждёт
снимка профиля с сотнями отзывов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._extract import attribute
from ._observed import Observed
from ._result import Completeness, Defect, Severity, collect_rows
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import SELECTORS

__all__ = ["Review", "ReviewsPage", "parse_reviews_page"]

#: Внешний контейнер таблицы отзывов.
_TABLE: Final[str] = SELECTORS["reviews.table"]

#: Контейнер строк. Тот же класс, что у списка продаж: площадка переиспользует
#: виджет dyn-table.
_ROWS_CONTAINER: Final[str] = SELECTORS["reviews.rows_container"]

#: Строка отзыва.
_ROW: Final[str] = SELECTORS["reviews.row"]

#: Обёртка ОДНОГО отзыва, а не списка - имя обманывает.
#:
#: Служит третьим счётом строк, не зависящим ни от класса строки, ни от прямых
#: потомков контейнера. Обёрток ровно столько же, сколько отзывов, и расхождение
#: означает, что разметка изменилась там, где два других счёта этого не видят.
_WRAPPER: Final[str] = SELECTORS["reviews.row.wrapper"]

#: Узел оценки. В строке их два - широкий макет и узкий.
_RATING: Final[str] = SELECTORS["reviews.fields.rating"]

_AUTHOR_NAME: Final[str] = SELECTORS["reviews.fields.author_name"]
_AUTHOR_HREF: Final[str] = SELECTORS["reviews.fields.author_href"]
_AUTHOR_PHOTO_HREF: Final[str] = SELECTORS["reviews.fields.author_photo_href"]
_ORDER_HREF: Final[str] = SELECTORS["reviews.fields.order_href"]
_TEXT: Final[str] = SELECTORS["reviews.fields.text"]
_DATE_TEXT: Final[str] = SELECTORS["reviews.fields.date_text"]
_DETAIL_TEXT: Final[str] = SELECTORS["reviews.fields.detail_text"]

#: Оценка в имени класса: rating5 значит пять звёзд.
#:
#: Перечисление закрыто нарочно. Открытое приняло бы rating7 или rating0 за
#: оценку, а закрытое объявит незнакомое ненаблюдённым - и это правильный ответ:
#: смена шкалы у площадки должна быть заметна, а не подхвачена молча.
_RATING_CLASS: Final[re.Pattern[str]] = re.compile(r"^rating([1-5])$")


@dataclass(frozen=True, slots=True)
class Review:
    """Отзыв, прочитанный с профиля продавца.

    Момента времени в записи нет, и это не упущение. Дата показана
    локализованной человеческой записью - тридцать один знак с буквами, цифрами
    и пунктуацией, - и разбирать её значило бы угадывать по одной локали из
    трёх. Строка, которую нельзя разобрать, честнее момента, угаданного неверно.

    Attributes:
        row_index (int): Место отзыва на странице, считая с нуля.
        rating (Observed[int]): Оценка в звёздах, целое от одного до пяти.
        author_name (Observed[str]): Отображаемое имя автора.
        author_href (Observed[str]): Адрес профиля автора.
        order_href (Observed[str]): Адрес заказа, к которому отзыв относится.
        text (Observed[str]): Текст отзыва.
        date_text (Observed[str]): Дата в том виде, в каком её показали.
        detail_text (Observed[str]): Подпись под отзывом: что купили и почём.
    """

    row_index: int
    rating: Observed[int]
    author_name: Observed[str]
    author_href: Observed[str]
    order_href: Observed[str]
    text: Observed[str]
    date_text: Observed[str]
    detail_text: Observed[str]


@dataclass(frozen=True, slots=True)
class ReviewsPage:
    """Результат чтения отзывов.

    Записи отдаются методом, а не полем, по той же причине, что и у списка
    продаж: открытый список делает неполноту незаметной.

    Attributes:
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько кандидатов в отзывы нашлось, штук.
        rows_accepted (int): Сколько отзывов собрано, штук.
        rows_rejected (int): Сколько кандидатов отброшено, штук.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    completeness: Completeness
    reason: str
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    defects: tuple[Defect, ...] = ()
    _entries: tuple[Review, ...] = field(repr=False, default=())

    def rows(self, *, accept_incomplete: bool = False) -> tuple[Review, ...]:
        """Возвращает собранные отзывы.

        Args:
            accept_incomplete (bool): Признание того, что результат может быть
                неполным. Без него неполный результат не выдаётся.

        Returns:
            tuple[Review, ...]: Отзывы в порядке появления на странице.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"результат неполон ({self.completeness}, причина: {self.reason}), "
                f"собрано {self.rows_accepted} из {self.rows_total}, "
                f"повреждений {len(self.defects)}. Передайте accept_incomplete=True, "
                "если готовы работать с неполными данными"
            )
        return self._entries

    def __len__(self) -> int:
        """Возвращает число собранных отзывов.

        Returns:
            int: Число собранных отзывов.
        """
        return len(self._entries)


def _text_of(node: Node | None, name: str) -> Observed[str]:
    """Извлекает текст узла как наблюдаемое значение.

    Args:
        node (Node | None): Узел либо None, если селектор не нашёл ничего.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение. Отсутствие узла и пустой текст различаются.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _rating(row: Node, index: int) -> tuple[Observed[int], list[Defect]]:
    """Читает оценку из имени класса и сверяет два узла между собой.

    Узлов оценки в строке два: один для широкого макета, другой для узкого.
    Значение в них одно и то же, и расхождение означает не выбор, а изменение
    разметки - объявлять оценку по первому попавшемуся значило бы отдать
    покупателю чужую звезду молча.

    Args:
        row (Node): Строка отзыва.
        index (int): Место строки на странице, для сообщения о повреждении.

    Returns:
        tuple[Observed[int], list[Defect]]: Оценка и перечень повреждений.
    """
    nodes = row.css(_RATING)
    if not nodes:
        return Observed.missing("selector_no_match:rating"), []

    seen: set[int] = set()
    unknown: list[str] = []
    for node in nodes:
        for name in (node.attributes.get("class") or "").split():
            match = _RATING_CLASS.match(name)
            if match is not None:
                seen.add(int(match.group(1)))
            else:
                unknown.append(name)

    if not seen:
        # Узел есть, а оценки в его классах нет. Это не «оценки не показали»:
        # это класс, которого перечисление не знает, и подхватить его молча
        # значило бы принять чужую шкалу за свою.
        return (
            Observed.missing("rating_class_not_recognised"),
            [
                Defect(
                    severity=Severity.ROW,
                    code="rating_class_not_recognised",
                    detail=(
                        f"строка {index}: у узлов оценки классы {sorted(set(unknown))}, "
                        "ни один не входит в наблюдённое перечисление rating1..rating5"
                    ),
                    field_name="rating",
                )
            ],
        )

    if len(seen) > 1:
        return (
            Observed.missing("rating_carriers_disagree"),
            [
                Defect(
                    severity=Severity.ROW,
                    code="rating_carriers_disagree",
                    detail=(
                        f"строка {index}: узлы оценки разошлись, прочитано {sorted(seen)}. "
                        "Взять любую из них значило бы выбрать наугад"
                    ),
                    field_name="rating",
                )
            ],
        )

    return Observed.present(seen.pop()), []


def _author_href(row: Node, index: int) -> tuple[Observed[str], list[Defect]]:
    """Читает адрес профиля автора и сверяет два его носителя.

    Адресов в строке два: на имени и на аватаре. Ведут они в одно место, и
    расхождение означает изменение разметки. Проверка заведена по той же
    причине, по которой сверяются два узла оценки.

    Args:
        row (Node): Строка отзыва.
        index (int): Место строки на странице, для сообщения о повреждении.

    Returns:
        tuple[Observed[str], list[Defect]]: Адрес и перечень повреждений.
    """
    from_name = attribute(row.css_first(_AUTHOR_HREF), "href", "author_href")
    from_photo = attribute(row.css_first(_AUTHOR_PHOTO_HREF), "href", "author_photo_href")

    if not from_name.is_observed or not from_photo.is_observed:
        return from_name, []

    if from_name.value != from_photo.value:
        return (
            Observed.missing("author_href_mismatch"),
            [
                Defect(
                    severity=Severity.ROW,
                    code="author_href_mismatch",
                    detail=(
                        f"строка {index}: адрес на имени {from_name.value!r} и адрес "
                        f"на аватаре {from_photo.value!r} ведут в разные места"
                    ),
                    field_name="author_href",
                )
            ],
        )

    return from_name, []


def _parse_row(row: Node, index: int) -> tuple[Review, list[Defect]]:
    """Собирает один отзыв.

    Отказа здесь нет ни одного: у отзыва нет поля, без которого запись
    бессмысленна. У записи продажи такое поле есть - идентификатор заказа, - и
    строка без него отбрасывается. Отзыв без адреса заказа остаётся отзывом:
    оценка и текст читаются и без него.

    Args:
        row (Node): Строка отзыва.
        index (int): Место строки на странице, считая с нуля.

    Returns:
        tuple[Review, list[Defect]]: Отзыв и перечень повреждений строки.
    """
    rating, defects = _rating(row, index)
    author_href, href_defects = _author_href(row, index)
    defects.extend(href_defects)

    return (
        Review(
            row_index=index,
            rating=rating,
            author_name=_text_of(row.css_first(_AUTHOR_NAME), "author_name"),
            author_href=author_href,
            order_href=attribute(row.css_first(_ORDER_HREF), "href", "order_href"),
            text=_text_of(row.css_first(_TEXT), "text"),
            date_text=_text_of(row.css_first(_DATE_TEXT), "date_text"),
            detail_text=_text_of(row.css_first(_DETAIL_TEXT), "detail_text"),
        ),
        defects,
    )


def parse_reviews_page(html: str, observed_at: datetime) -> ReviewsPage:
    """Разбирает страницу профиля и собирает отзывы.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        ReviewsPage: Отзывы вместе с полнотой и перечнем повреждений.

    Raises:
        ProtocolChangedError: Если разметка изменилась настолько, что читать
            нечего: нет таблицы либо нет контейнера строк.
    """
    tree = HTMLParser(html)
    defects: list[Defect] = []

    if tree.css_first(_TABLE) is None:
        raise ProtocolChangedError(
            f"на странице профиля нет контейнера таблицы отзывов ({_TABLE}). "
            "Пустой список вернуть нельзя: профиля без отзывов проект не видел, "
            "и отличить его от смены разметки нечем"
        )

    if tree.css_first(_ROWS_CONTAINER) is None:
        raise ProtocolChangedError(
            f"на странице профиля нет контейнера строк ({_ROWS_CONTAINER}). "
            "Без него нельзя проверить, что строки найдены все"
        )

    found = collect_rows(tree, _ROWS_CONTAINER, _ROW)
    defects.extend(found.defects)

    # Третий счёт. Обёрток столько же, сколько отзывов, и разойдись они - это
    # повреждение уровня страницы, а не повод молча вернуть меньше строк.
    wrappers = len(tree.css(_WRAPPER))
    if wrappers != len(found.rows):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="wrapper_count_mismatch",
                detail=(
                    f"обёрток отзыва {wrappers}, а строк найдено {len(found.rows)}. "
                    "Один из двух счётчиков видит не то, что другой"
                ),
            )
        )

    entries: list[Review] = []
    for index, row in enumerate(found.rows):
        entry, row_defects = _parse_row(row, index)
        defects.extend(row_defects)
        entries.append(entry)

    rows_total = max(len(found.rows), found.children, len(tree.css(_ROW)))
    rows_accepted = len(entries)
    rows_rejected = len(found.rows) - rows_accepted

    if rows_total and not rows_accepted:
        raise ProtocolChangedError(
            f"кандидатов в отзывы {rows_total}, собрать не удалось ни одного. "
            "Это изменение разметки, а не пустой список"
        )

    # Ноль отзывов даёт неизвестную полноту, а не пустой успех. У списка продаж
    # такой снимок есть - страница без продаж снята контрольной парой, - и там
    # пустота объявляется полным чтением по позитивному признаку. Здесь снимка
    # нет, и признака нет: отличить продавца без отзывов от переименованного
    # класса строки нечем.
    if not rows_total:
        completeness, reason = Completeness.UNKNOWN, "empty_list_not_observed"
    elif any(one.severity is Severity.PAGE for one in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    elif defects:
        completeness, reason = Completeness.PARTIAL, "row_defects"
    else:
        completeness, reason = Completeness.COMPLETE, "all_rows_parsed"

    return ReviewsPage(
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        rows_total=rows_total,
        rows_accepted=rows_accepted,
        rows_rejected=rows_rejected,
        defects=tuple(defects),
        _entries=tuple(entries),
    )
