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

Статус заказа не читается вовсе. Соответствия классов статусам не наблюдалось,
и spec/extraction/orders.yaml прямо требует выдавать поле ненаблюдённым, а не
значением unknown: второе означало бы, что статус прочитан и не опознан, тогда
как он не прочитан. Практическое следствие называется прямо - сегодня список
заказов не отвечает на вопрос, оплачен ли заказ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Confidence, Observed
from .errors import IncompleteResultError, ProtocolChangedError

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


class Severity(StrEnum):
    """Уровень, на котором обнаружено повреждение разбора.

    Уровни различают, что именно потеряно, потому что решения по ним разные.
    """

    #: Пострадала страница целиком. Записям доверять нельзя.
    PAGE = "page"

    #: Пострадала одна строка. Она отброшена, остальные целы.
    ROW = "row"

    #: Пострадало одно поле одной строки. Строка сохранена.
    FIELD = "field"


class Completeness(StrEnum):
    """Полнота прочитанного списка.

    Словарь взят из спецификации и не расширяется реализацией: значение уходит
    в решение вызывающего, и лишнее значение здесь означает, что шесть SDK
    ответят на один вопрос по-разному.
    """

    #: Все строки разобраны, повреждений нет.
    COMPLETE = "complete"

    #: Часть данных потеряна. Что именно - в перечне повреждений.
    PARTIAL = "partial"

    #: Полноту установить не удалось.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Defect:
    """Повреждение, обнаруженное при разборе.

    Attributes:
        severity (Severity): Уровень, на котором обнаружено.
        code (str): Машиночитаемый код, например ``row_selector_undercount``.
        detail (str): Пояснение для человека. Содержимого страницы не содержит.
        row_index (int | None): Номер строки при severity ROW и FIELD.
        field_name (str | None): Имя поля при severity FIELD.
    """

    severity: Severity
    code: str
    detail: str
    row_index: int | None = None
    field_name: str | None = None


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
        status (Observed[str]): Статус заказа. Сегодня всегда ненаблюдаем:
            соответствия классов статусам не наблюдалось.
        status_carrier (Observed[str]): Класс-носитель статуса, как он есть в
            разметке. Нужен для того, чтобы соответствие однажды составить.
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
    status: Observed[str]
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

    user_link = row.css_first("[data-href]")
    media = row.css_first(".media-user")
    if media is None:
        online: Observed[bool] = Observed.missing("selector_no_match:counterparty_online")
    else:
        classes = (media.attributes.get("class") or "").split()
        online = Observed.present("offline" not in classes, Confidence.INFERRED)

    entry = OrderListEntry(
        order_id=match.group(1),
        href=href,
        row_index=index,
        # Статус не читается принципиально: соответствия классов статусам не
        # наблюдалось. Выдать здесь unknown значило бы утверждать, что статус
        # прочитан и не опознан, тогда как он не прочитан вовсе.
        status=Observed.missing("status_mapping_not_observed"),
        status_carrier=_text(row.css_first(".tc-status"), "status_carrier"),
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
        "counterparty_online",
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

    container = tree.css_first(_ROWS_CONTAINER)
    if container is None:
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

    rows = container.css(_ROW)
    children = [node for node in container.iter() if node.tag != "-text"]

    # Два независимых счётчика. Переименование класса строки при живом
    # контейнере - самый вероятный вид изменения разметки, и без второго
    # счётчика оно даёт пустой список, неотличимый от «заказов нет».
    if len(rows) != len(children):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="row_selector_undercount",
                detail=(
                    f"селектор строки нашёл {len(rows)}, а прямых детей контейнера {len(children)}"
                ),
            )
        )

    entries: list[OrderListEntry] = []
    for index, row in enumerate(rows):
        entry, row_defects = _parse_row(row, index)
        defects.extend(row_defects)
        if entry is not None:
            entries.append(entry)

    rows_total = max(len(rows), len(children))
    rows_accepted = len(entries)
    rows_rejected = len(rows) - rows_accepted

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
