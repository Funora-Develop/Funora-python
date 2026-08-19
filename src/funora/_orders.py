"""Разбор списка заказов со страницы /orders/trade.

Модуль чистый: он не ходит в сеть, не смотрит на часы и не ведёт состояния.
Вход - разметка и момент наблюдения, выход - страница записей. Такое разделение
не эстетическое: разбор нужно уметь повторить на сохранённом снимке спустя
полгода и получить тот же результат, иначе фикстуры бесполезны.

Три решения объясняют почти всё остальное.

Отказ одной строки не отменяет страницу. Соседние заказы к сломавшемуся
отношения не имеют, и терять их из-за него значит терять деньги там, где
достаточно было пометить одну запись негодной.

Ноль строк даёт неизвестную полноту, а не пустой успех. Снимка страницы без
заказов у проекта нет, поэтому отличить продавца без продаж от переименованного
класса строки нечем. Это сознательно неудобно и станет полнотой COMPLETE в тот
день, когда снимок появится, - расширением наблюдений, а не догадкой.

Присутствие контрагента читается по словарю классов, а не по отрицанию.
Площадка помечает оба состояния явно - online и offline, - и снимок, сделанный
в момент, когда трое покупателей были в сети, дал оба класса разом. Правило
«нет offline, значит online» осталось запрещённым: оно выглядело бы работающим
ровно до переименования класса, а неузнанный класс честнее объявить
ненаблюдённым.

Состояние заказа читается по двум независимым носителям сразу: цветовому классу
ячейки и модификатору строки. Оба структурные, оба локализации не подвержены, и
в наблюдении они совпали во всех восьми строках. Совпадение используется как
проверка, а не выбрасывается: переименуй площадка любой из носителей - и второй
не согласится, а разбор скажет об этом вслух вместо того, чтобы молча поменять
ответ. Ответ здесь - «оплачен ли заказ», то есть решение бота выдавать товар.

Наблюдались два состояния из скольких-то. Носитель, которого нет в таблице,
даёт ненаблюдённое значение, а не unknown: второе означало бы, что состояние
прочитано и не опознано, тогда как оно не прочитано вовсе.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Confidence, Observed
from ._result import Completeness, Defect, Severity, collect_rows
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import (
    PRESENCE_BY_CLASS,
    STATUS_BY_CELL_CLASS,
    STATUS_BY_ROW_CLASS,
    OrderStatus,
)

__all__ = [
    "Severity",
    "Completeness",
    "Defect",
    "OrderListEntry",
    "OrdersPage",
    "parse_orders_page",
]

#: Контейнер таблицы заказов.
_TABLE: Final[str] = ".orders-table"

#: Заголовок таблицы. Несёт те же классы ячеек, что и строки.
_HEADER: Final[str] = ".tc-header"

#: Контейнер строк. Второй, независимый от класса строки признак.
_ROWS_CONTAINER: Final[str] = ".dyn-table-body"

#: Хвостовой маркер документа.
_TAIL: Final[str] = ".wrapper-footer"

#: Селектор строки заказа.
_ROW: Final[str] = "a.tc-item"

#: Идентификатор заказа в адресе строки.
_ID_IN_HREF: Final[re.Pattern[str]] = re.compile(r"/orders/([^/?#]+)")


@dataclass(frozen=True, slots=True)
class OrderListEntry:
    """Сокращённая запись заказа, прочитанная из списка.

    Не Order и намеренно отличается от него типом. Полную запись из списка
    собрать нельзя: страница не даёт ни валюты, ни машиночитаемого времени,
    ни ролей сторон. Поля, которых там нет структурно, в этом типе отсутствуют,
    а не лежат в нём как None - прочитать их нельзя даже по ошибке.

    Attributes:
        order_id (str): Идентификатор заказа из адреса строки.
        href (str): Адрес страницы заказа.
        row_index (int): Порядковый номер строки на странице, с нуля.
        status (Observed[OrderStatus]): Состояние заказа. Читается по двум
            независимым структурным носителям сразу; расходятся - значение
            ненаблюдённое и повреждение уровня поля. Наблюдались два состояния
            из скольких-то, и носитель вне таблицы тоже даёт ненаблюдённое
            значение, а не unknown.
        status_carrier (Observed[str]): Цветовой класс ячейки статуса как он
            есть. Читается класс, а не текст: текст локализован. Поле остаётся
            и после того, как соответствие установлено, - по нему узнают, как
            выглядит состояние, которого в таблице ещё нет.
        order_number_text (Observed[str]): Видимый номер заказа, текст.
        description_text (Observed[str]): Описание заказа, текст.
        counterparty_name (Observed[str]): Имя контрагента, текст.
        counterparty_href (Observed[str]): Адрес профиля контрагента.
        counterparty_online (Observed[bool]): Признак присутствия контрагента.
        amount_text (Observed[str]): Сумма, текст. Числом не разбирается:
            валюты на странице нет.
        time_text (Observed[str]): Время заказа, текст. Точного момента
            страница не даёт вовсе.
    """

    order_id: str
    href: str
    row_index: int
    status: Observed[OrderStatus]
    status_carrier: Observed[str]
    order_number_text: Observed[str]
    description_text: Observed[str]
    counterparty_name: Observed[str]
    counterparty_href: Observed[str]
    counterparty_online: Observed[bool]
    amount_text: Observed[str]
    time_text: Observed[str]


@dataclass(frozen=True, slots=True)
class OrdersPage:
    """Результат чтения страницы списка заказов.

    Записи получают через :meth:`rows`, а не напрямую. Открытый список сделал бы
    неполноту незаметной: обойти его в цикле проще, чем спросить о полноте, и
    именно так теряются заказы.

    Attributes:
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения. Единственное время в записи,
            известное точно.
        rows_total (int): Сколько кандидатов в строки нашлось.
        rows_accepted (int): Сколько записей собрано.
        rows_rejected (int): Сколько строк отброшено.
        defects (tuple[Defect, ...]): Обнаруженные повреждения.
    """

    completeness: Completeness
    reason: str
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    defects: tuple[Defect, ...]
    _entries: tuple[OrderListEntry, ...] = field(repr=False, default=())

    def rows(self, *, accept_incomplete: bool = False) -> tuple[OrderListEntry, ...]:
        """Возвращает собранные записи.

        Args:
            accept_incomplete (bool): Признание того, что результат может быть
                неполным. Без него неполный результат не выдаётся.

        Returns:
            tuple[OrderListEntry, ...]: Записи в порядке появления на странице.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана. Молча отданный неполный список неотличим от
                полного, и обработчик примет решение по данным, которых нет.
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
        """Возвращает число собранных записей.

        Длина доступна без признания неполноты намеренно: узнать, сколько
        записей собрано, нужно как раз для того, чтобы решить, признавать ли её.

        Returns:
            int: Число собранных записей.
        """
        return len(self._entries)


def _text(node: Node | None, name: str) -> Observed[str]:
    """Извлекает текст узла как наблюдаемое значение.

    Args:
        node (Node | None): Узел или None, если селектор не нашёл ничего.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение. Отсутствие узла и пустой текст различаются.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _classes(node: Node | None, *, without: str) -> frozenset[str]:
    """Возвращает классы узла без общего, по которому он найден.

    Args:
        node (Node | None): Узел либо None.
        without (str): Класс, который нужно отбросить: он одинаков у всех и
            ничего не различает.

    Returns:
        frozenset[str]: Оставшиеся классы. Пустое множество, если узла нет.
    """
    if node is None:
        return frozenset()
    raw = (node.attributes or {}).get("class") or ""
    return frozenset(raw.split()) - {without}


def _carrier(node: Node | None) -> Observed[str]:
    """Извлекает класс-носитель статуса.

    Читается именно класс, а не текст ячейки. Текст локализован, и составить по
    нему соответствие статусам нельзя: сменив язык аккаунта, площадка вернула бы
    другие значения для тех же состояний. Класс от языка не зависит - именно
    поэтому спецификация и называет носителем его.

    Args:
        node (Node | None): Узел ячейки статуса.

    Поле остаётся и после того, как соответствие носителей состояниям
    установлено. Наблюдались два состояния из скольких-то, и когда встретится
    третье, узнать, как оно выглядит, можно будет только отсюда: само поле
    статуса на нём окажется ненаблюдённым.

    Args:
        node (Node | None): Узел ячейки статуса.

    Returns:
        Observed[str]: Цветовой класс ячейки без общего класса tc-status.
    """
    if node is None:
        return Observed.missing("selector_no_match:status_carrier")
    classes = sorted(_classes(node, without="tc-status"))
    return Observed.present(" ".join(classes)) if classes else Observed.empty("")


def _presence(media: Node | None) -> Observed[bool]:
    """Читает признак присутствия контрагента.

    Словарь классов закрыт по наблюдению, но не по умолчанию. Площадка помечает
    оба состояния явно: ``online`` и ``offline``, - и снимок, сделанный в момент,
    когда трое покупателей были в сети, дал оба класса разом.

    Вывод по отрицанию при этом остаётся запрещённым. Правило «нет offline,
    значит online» выглядело бы работающим ровно до переименования класса:
    стань он ``is-offline``, и каждый контрагент молча оказался бы
    присутствующим - без повреждения, без исключения, без строки в журнале.
    Поэтому неузнанный класс даёт ненаблюдённое значение, а не догадку.

    Два маркера сразу - тоже неузнанное состояние: разметка, объявляющая
    пользователя одновременно в сети и не в сети, не даёт ответа, и выбирать
    один из двух было бы выдумкой.

    Args:
        media (Node | None): Узел карточки пользователя внутри ячейки
            контрагента либо None, если селектор не нашёл её.

    Returns:
        Observed[bool]: True либо False по наблюдённому классу; ненаблюдённое
        значение, если класс не узнан либо узнаны сразу два.
    """
    if media is None:
        return Observed.missing("selector_no_match:counterparty_online")
    hits = {name for name in _classes(media, without="media-user") if name in PRESENCE_BY_CLASS}
    if len(hits) != 1:
        return Observed.missing("presence_marker_not_recognised")
    return Observed.present(PRESENCE_BY_CLASS[hits.pop()], Confidence.OBSERVED)


def _status(row: Node) -> tuple[Observed[OrderStatus], str | None]:
    """Читает состояние заказа по двум независимым носителям.

    Носителя два, и оба структурные: цветовой класс ячейки статуса и модификатор
    самой строки. Читаются оба. В наблюдении они совпали во всех восьми строках,
    и это свойство используется как проверка, а не выбрасывается: переименуй
    площадка любой из них - и второй не согласится.

    Читать один носитель было бы дешевле и хуже. Смена вёрстки поменяла бы
    ответ молча, а ответ здесь - это «оплачен ли заказ», то есть решение бота
    выдавать товар.

    Носители несимметричны, и это стоит знать. Класс ячейки положителен в обе
    стороны: ``text-primary`` и ``text-success`` оба присутствуют в разметке.
    Модификатор строки положителен только для оплаченного (``info``), а
    закрытый узнаётся по его отсутствию. Само по себе такое чтение было бы
    догадкой - но как второй голос оно годится: расхождение здесь громкое, а не
    молчаливое.

    Args:
        row (Node): Узел строки заказа.

    Returns:
        tuple[Observed[OrderStatus], str | None]: Состояние и причина
        повреждения, если носители разошлись между собой.
    """
    cell = row.css_first(".tc-status")
    if cell is None:
        return Observed.missing("selector_no_match:status"), None

    by_cell = STATUS_BY_CELL_CLASS.get(" ".join(sorted(_classes(cell, without="tc-status"))))
    by_row = STATUS_BY_ROW_CLASS.get(" ".join(sorted(_classes(row, without="tc-item"))))

    if by_cell is None or by_row is None:
        # Состояние на странице есть, но в наблюдённой таблице его нет. Это не
        # unknown: unknown означал бы, что состояние прочитано и не опознано,
        # тогда как оно не прочитано вовсе. Носитель при этом сохраняется в
        # status_carrier - по нему и узнают, как выглядит новое состояние.
        return Observed.missing("status_carrier_not_mapped"), None

    if by_cell is not by_row:
        return Observed.missing("status_carriers_disagree"), "status_carriers_disagree"

    return Observed.present(by_cell, Confidence.OBSERVED), None


def _parse_row(row: Node, index: int) -> tuple[OrderListEntry | None, list[Defect]]:
    """Разбирает одну строку заказа.

    Все селекторы применяются внутри строки, а не по документу: заголовок
    таблицы несёт те же классы ячеек, и .tc-price по документу находит четыре
    элемента при трёх заказах.

    Args:
        row (Node): Узел строки.
        index (int): Порядковый номер строки на странице.

    Returns:
        tuple[OrderListEntry | None, list[Defect]]: Запись либо None, если
        строка непригодна, и перечень обнаруженных повреждений.
    """
    defects: list[Defect] = []

    href = (row.attributes.get("href") or "").strip()
    match = _ID_IN_HREF.search(href)
    if not href or match is None:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="order_id_not_extractable",
                detail="в адресе строки нет идентификатора заказа",
                row_index=index,
            )
        )
        return None, defects

    # Селектор ссылки на профиль ограничен ячейкой контрагента. Раньше брался
    # первый [data-href] строки; в снимке их два, и оба ведут на одного человека,
    # поэтому ошибки не было видно. Появись data-href в описании лота - и первым
    # оказался бы он, а контрагентом молча стал бы адрес товара.
    user_link = row.css_first(".tc-user [data-href]")
    media = row.css_first(".tc-user .media-user")
    online = _presence(media)
    status, disagreement = _status(row)
    if disagreement is not None:
        # Расхождение носителей - не мелочь и не косметика. Оно означает, что
        # разметка изменилась, и любой из двух ответов может быть неверным.
        # Выбрать один значило бы угадывать там, где угадывать нельзя: ответ
        # здесь - это «оплачен ли заказ».
        defects.append(
            Defect(
                severity=Severity.FIELD,
                code=disagreement,
                detail="цветовой класс ячейки и модификатор строки говорят о разном",
                row_index=index,
                field_name="status",
            )
        )

    entry = OrderListEntry(
        order_id=match.group(1),
        href=href,
        row_index=index,
        status=status,
        status_carrier=_carrier(row.css_first(".tc-status")),
        order_number_text=_text(row.css_first(".tc-order"), "order_number_text"),
        description_text=_text(row.css_first(".order-desc"), "description_text"),
        counterparty_name=_text(row.css_first(".tc-user .media-user-name"), "counterparty_name"),
        counterparty_href=(
            Observed.present((user_link.attributes.get("data-href") or "").strip())
            if user_link is not None
            else Observed.missing("selector_no_match:counterparty_href")
        ),
        counterparty_online=online,
        amount_text=_text(row.css_first(".tc-price"), "amount_text"),
        time_text=_text(row.css_first(".tc-date-time"), "time_text"),
    )

    for name in (
        "status_carrier",
        "order_number_text",
        "description_text",
        "counterparty_name",
        "counterparty_href",
        # counterparty_online сюда не входит по той же причине, что и статус:
        # его ненаблюдённость - решение о границе наблюдений, а не поломка
        # разметки. Считать её повреждением значило бы отмечать повреждением
        # каждого присутствующего контрагента.
        "amount_text",
        "time_text",
    ):
        if not getattr(entry, name).is_observed:
            defects.append(
                Defect(
                    severity=Severity.FIELD,
                    code="field_not_observed",
                    detail="селектор поля не нашёл узла там, где он ожидался",
                    row_index=index,
                    field_name=name,
                )
            )

    return entry, defects


#: Поля, отсутствие которых во всех строках означает поломку страницы, а не
#: сумму поломок полей. Статус сюда не входит: он ненаблюдаем по решению
#: спецификации, а не из-за разметки.
_PAGE_LEVEL_FIELDS: Final[tuple[str, ...]] = (
    "status_carrier",
    "order_number_text",
    "counterparty_name",
    "amount_text",
    "time_text",
)


def parse_orders_page(html: str, *, observed_at: datetime) -> OrdersPage:
    """Разбирает страницу списка заказов.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        OrdersPage: Записи вместе с полнотой и перечнем повреждений.

    Raises:
        ProtocolChangedError: Если разметка изменилась настолько, что читать
            нечего: нет таблицы, нет контейнера строк, либо кандидаты в строки
            были, а собрать не удалось ни одной.
    """
    tree = HTMLParser(html)
    defects: list[Defect] = []

    if tree.css_first(_TABLE) is None:
        raise ProtocolChangedError(
            f"на странице нет контейнера таблицы заказов ({_TABLE}). "
            "Пустой список вернуть нельзя: он неотличим от отсутствия заказов"
        )

    if tree.css_first(_ROWS_CONTAINER) is None:
        raise ProtocolChangedError(
            f"на странице нет контейнера строк ({_ROWS_CONTAINER}). "
            "Без него нельзя проверить, что строки найдены все"
        )

    if tree.css_first(_HEADER) is None:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="header_missing",
                detail="заголовок таблицы отсутствует, разметка изменилась",
            )
        )

    found = collect_rows(tree, _ROWS_CONTAINER, _ROW)
    defects.extend(found.defects)

    entries: list[OrderListEntry] = []
    for index, row in enumerate(found.rows):
        entry, row_defects = _parse_row(row, index)
        defects.extend(row_defects)
        if entry is not None:
            entries.append(entry)

    rows_total = max(len(found.rows), found.children, len(tree.css(_ROW)))
    rows_accepted = len(entries)
    rows_rejected = len(found.rows) - rows_accepted

    if rows_total and not rows_accepted:
        raise ProtocolChangedError(
            f"кандидатов в строки {rows_total}, собрать не удалось ни одной. "
            "Это изменение разметки, а не пустой список"
        )

    for name in _PAGE_LEVEL_FIELDS:
        if entries and all(not getattr(entry, name).is_observed for entry in entries):
            defects.append(
                Defect(
                    severity=Severity.PAGE,
                    code="field_missing_in_all_rows",
                    detail=f"поле {name} отсутствует во всех собранных строках",
                    field_name=name,
                )
            )

    if not rows_total:
        completeness, reason = Completeness.UNKNOWN, "empty_list_not_observed"
    elif not defects:
        completeness, reason = Completeness.COMPLETE, "all_rows_parsed"
    elif any(d.severity is Severity.PAGE for d in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    else:
        completeness, reason = Completeness.PARTIAL, "row_defects"

    return OrdersPage(
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        rows_total=rows_total,
        rows_accepted=rows_accepted,
        rows_rejected=rows_rejected,
        defects=tuple(defects),
        _entries=tuple(entries),
    )
