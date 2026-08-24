"""Разбор страницы баланса аккаунта.

Страница отдаёт две вещи, и вторую не искали вовсе.

БАЛАНСЫ - за ними и шли. Схема с 0.8.0 объявляла перечень, а не одно значение,
по переписи знаков валют; разметка это подтвердила - три узла значения, ровно
три.

ТАБЛИЦА ОПЕРАЦИЙ - двадцать пять строк на той же машинерии dyn-table, что список
продаж и отзывы. Идентификатор в атрибуте, состояние в имени класса строки,
сумма отделена от знака валюты. Операции для неё в контракте не было объявлено
вовсе.

Три решения объясняют остальное.

Знак валюты в балансе не объявляется знаком валюты. Узлы значения и разделителя
чередуются, начиная с разделителя: три разделителя на три значения, а
разделяющий знак дал бы два. Строение исключает прочтение «разделитель между
значениями» и не доказывает, что там именно валюта, - сам знак замаскирован.
Поэтому поле называется тем, что о нём известно: узел перед значением.

Поля строки ищутся ВНУТРИ строки, а не по документу. Заголовок таблицы несёт
ячейку цены с тем же классом, что и строки, и поиск по документу дал бы
двадцать шесть ячеек на двадцать пять строк.

Полнота решается кнопкой догрузки, и её класс наблюдён с обеих сторон: здесь
кнопка не спрятана при двадцати пяти строках, на профиле отзывов - спрятана при
шести из шести.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity, collect_rows
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import SELECTORS

__all__ = ["BalancePage", "Transaction", "parse_balance_page"]

_BALANCES_LIST: Final[str] = SELECTORS["account.balances.list"]
_BALANCE_VALUE: Final[str] = SELECTORS["account.balances.value"]
_BALANCE_DELIMITER: Final[str] = SELECTORS["account.balances.delimiter"]

_TABLE: Final[str] = SELECTORS["account.transactions.table"]
_ROWS_CONTAINER: Final[str] = SELECTORS["account.transactions.rows_container"]
_ROW: Final[str] = SELECTORS["account.transactions.row"]
_HEADER: Final[str] = SELECTORS["account.transactions.header_is_not_a_row"]

#: Носитель идентификатора и состояния - сама строка. Оба объявлены
#: отдельными правилами, и берутся они отсюда, а не из имени строки в коде:
#: разойдись спека с кодом - разойдётся молча.
_ID_CARRIER: Final[str] = SELECTORS["account.transactions.fields.transaction_id"]
_STATUS_CARRIER: Final[str] = SELECTORS["account.transactions.fields.status"]

_STATUS_TEXT: Final[str] = SELECTORS["account.transactions.fields.status_text"]
_TITLE: Final[str] = SELECTORS["account.transactions.fields.title_text"]
_DATE: Final[str] = SELECTORS["account.transactions.fields.date_text"]
_DATE_LEFT: Final[str] = SELECTORS["account.transactions.fields.date_left_text"]
_AMOUNT: Final[str] = SELECTORS["account.transactions.fields.amount_text"]
_SYMBOL: Final[str] = SELECTORS["account.transactions.fields.currency_symbol_text"]

_CONTINUE: Final[str] = SELECTORS["account.pagination.continue_button"]
_PAGE_FORM: Final[str] = SELECTORS["account.pagination.form"]

#: Приставка класса состояния операции.
#:
#: Наблюдено одно значение на двадцати пяти строках. Фильтр над таблицей
#: перечисляет четыре, но их имена - локализованный текст, и связать текст с
#: классом нечем.
_STATUS_PREFIX: Final[str] = "transaction-status-"


@dataclass(frozen=True, slots=True)
class Balance:
    """Один баланс аккаунта.

    Attributes:
        value_text (Observed[str]): Сумма, как показана.
        marker_text (Observed[str]): Узел перед суммой - вероятнее всего знак
            валюты, но утверждать это по снимку нельзя.
        position (int): Место в перечне, считая с нуля.
    """

    value_text: Observed[str]
    marker_text: Observed[str]
    position: int


@dataclass(frozen=True, slots=True)
class Transaction:
    """Одна операция по счёту.

    Attributes:
        transaction_id (Observed[str]): Идентификатор операции.
        status_class (Observed[str]): Состояние из имени класса строки.
        status_text (Observed[str]): Состояние словами, локализовано.
        title_text (Observed[str]): Описание операции, локализовано.
        date_text (Observed[str]): Дата, как показана.
        date_left_text (Observed[str]): Вторая метка времени рядом с первой.
        amount_text (Observed[str]): Сумма без знака валюты.
        currency_symbol_text (Observed[str]): Знак валюты.
        row_index (int): Место строки на странице, считая с нуля.
    """

    transaction_id: Observed[str]
    status_class: Observed[str]
    status_text: Observed[str]
    title_text: Observed[str]
    date_text: Observed[str]
    date_left_text: Observed[str]
    amount_text: Observed[str]
    currency_symbol_text: Observed[str]
    row_index: int


@dataclass(frozen=True, slots=True)
class BalancePage:
    """Результат чтения страницы баланса.

    Балансы отдаются полем, а операции - методом. Разница не в прихоти: перечень
    балансов страница показывает целиком, а операции догружаются, и открытый
    список делал бы неполноту незаметной.

    Attributes:
        balances (tuple[Balance, ...]): Балансы по валютам.
        completeness (Completeness): Полнота чтения ОПЕРАЦИЙ.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько кандидатов в операции нашлось, штук.
        rows_accepted (int): Сколько операций собрано, штук.
        rows_rejected (int): Сколько кандидатов отброшено, штук.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    balances: tuple[Balance, ...]
    completeness: Completeness
    reason: str
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    defects: tuple[Defect, ...] = ()
    _entries: tuple[Transaction, ...] = field(repr=False, default=())

    def transactions(self, *, accept_incomplete: bool = False) -> tuple[Transaction, ...]:
        """Возвращает собранные операции.

        Args:
            accept_incomplete (bool): Признание того, что результат может быть
                неполным. Без него неполный результат не выдаётся.

        Returns:
            tuple[Transaction, ...]: Операции в порядке появления на странице.

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


def _text(node: Node | None, name: str) -> Observed[str]:
    """Извлекает текст узла как наблюдение.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _own_text(node: Node | None, name: str) -> Observed[str]:
    """Извлекает СОБСТВЕННЫЙ текст узла, без вложенных.

    Знак валюты лежит отдельным узлом внутри ячейки суммы, и текст целиком
    склеил бы сумму со знаком - то же устройство, что у цены в списке продаж.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text(deep=False) or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _balances(tree: HTMLParser) -> tuple[tuple[Balance, ...], list[Defect]]:
    """Собирает балансы, сохраняя связь значения с идущим перед ним узлом.

    Узлы чередуются, начиная с разделителя: три разделителя на три значения.
    Разделяющий знак дал бы два на три, и строение исключает прочтение
    «разделитель между значениями» - он часть своего значения.

    Args:
        tree (HTMLParser): Разобранная страница.

    Returns:
        tuple[tuple[Balance, ...], list[Defect]]: Балансы и повреждения.
    """
    values = tree.css(_BALANCE_VALUE)
    markers = tree.css(_BALANCE_DELIMITER)

    if not values:
        return (), [
            Defect(
                severity=Severity.PAGE,
                code="balances_missing",
                detail=f"узлов баланса ({_BALANCE_VALUE}) на странице нет ни одного",
            )
        ]

    defects: list[Defect] = []
    if len(markers) != len(values):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="balance_markers_mismatch",
                detail=(
                    f"значений баланса {len(values)}, узлов перед ними {len(markers)}. "
                    "Связать значение с его знаком валюты нельзя: строение изменилось"
                ),
            )
        )

    out: list[Balance] = []
    for index, value in enumerate(values):
        marker = markers[index] if index < len(markers) else None
        out.append(
            Balance(
                value_text=_text(value, "balance_value"),
                marker_text=_text(marker, "balance_marker"),
                position=index,
            )
        )
    return tuple(out), defects


def _carrier(row: Node, selector: str) -> Node | None:
    """Возвращает узел-носитель поля внутри строки.

    Носителем идентификатора и состояния объявлена сама строка. Проверка
    делается явной, а не подразумевается: разойдись объявление с разбором -
    разойдётся молча.

    Args:
        row (Node): Узел строки.
        selector (str): Объявленный селектор носителя.

    Returns:
        Node | None: Сама строка, если селектор указывает на неё; иначе
        вложенный узел либо None.
    """
    if selector == _ROW:
        return row
    return row.css_first(selector)


def _row_of(node: Node, index: int) -> Transaction:
    """Собирает одну операцию.

    Поля ищутся ВНУТРИ строки, а не по документу: заголовок таблицы несёт ячейку
    цены с тем же классом, и поиск по документу дал бы на одну ячейку больше,
    чем строк.

    Args:
        node (Node): Узел строки.
        index (int): Место строки на странице, считая с нуля.

    Returns:
        Transaction: Операция.
    """
    carrier = _carrier(node, _STATUS_CARRIER)
    attributes = (carrier.attributes if carrier is not None else None) or {}
    names = set((attributes.get("class") or "").split())
    status = sorted(one for one in names if one.startswith(_STATUS_PREFIX))

    if len(status) == 1:
        status_class: Observed[str] = Observed.present(status[0])
    elif not status:
        status_class = Observed.missing("status_class_absent")
    else:
        status_class = Observed.missing("status_classes_disagree")

    price = node.css_first(_AMOUNT)
    return Transaction(
        transaction_id=_attribute(
            _carrier(node, _ID_CARRIER), "data-transaction", "transaction_id"
        ),
        status_class=status_class,
        status_text=_text(node.css_first(_STATUS_TEXT), "status_text"),
        title_text=_text(node.css_first(_TITLE), "title_text"),
        date_text=_text(node.css_first(_DATE), "date_text"),
        date_left_text=_text(node.css_first(_DATE_LEFT), "date_left_text"),
        amount_text=_own_text(price, "amount_text"),
        currency_symbol_text=_text(node.css_first(_SYMBOL), "currency_symbol_text"),
        row_index=index,
    )


def _attribute(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Читает атрибут, различая три исхода.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя атрибута.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{field_name}")
    attributes = node.attributes or {}
    if name not in attributes:
        return Observed.missing(f"attribute_absent:{field_name}")
    value = (attributes.get(name) or "").strip()
    return Observed.present(value) if value else Observed.empty("")


def _totality(tree: HTMLParser) -> tuple[Completeness, str]:
    """Решает, все ли операции показаны, по кнопке догрузки.

    Класс hidden у этой кнопки наблюдён с ОБЕИХ сторон, и вторая сторона - эта
    самая страница: здесь кнопка не спрятана при двадцати пяти строках и
    продолжении, а на профиле отзывов спрятана при шести из шести.

    Args:
        tree (HTMLParser): Разобранная страница.

    Returns:
        tuple[Completeness, str]: Полнота и машиночитаемая причина.
    """
    button = tree.css_first(_CONTINUE)
    if button is None:
        # Кнопки нет вовсе. Объявлять по её отсутствию полноту значило бы
        # вывести знание из ненаходки.
        return Completeness.PARTIAL, "pagination_control_missing"

    if "hidden" not in ((button.attributes or {}).get("class") or "").split():
        return Completeness.PARTIAL, "more_rows_available"

    return Completeness.COMPLETE, "all_rows_parsed"


def parse_balance_page(html: str, observed_at: datetime) -> BalancePage:
    """Разбирает страницу баланса: балансы и операции по счёту.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        BalancePage: Балансы и операции вместе с полнотой и повреждениями.

    Raises:
        ProtocolChangedError: Если нет перечня балансов либо контейнера строк:
            без них читать нечего, а пустой ответ неотличим от пустого счёта.
    """
    tree = HTMLParser(html)

    if tree.css_first(_BALANCES_LIST) is None:
        raise ProtocolChangedError(
            f"на странице баланса нет перечня балансов ({_BALANCES_LIST}). "
            "Пустой ответ вернуть нельзя: он неотличим от счёта без валют"
        )

    if tree.css_first(_TABLE) is None:
        raise ProtocolChangedError(
            f"на странице баланса нет таблицы операций ({_TABLE}). "
            "Без неё нельзя отличить счёт без операций от смены разметки"
        )

    if tree.css_first(_ROWS_CONTAINER) is None:
        raise ProtocolChangedError(
            f"на странице баланса нет контейнера строк ({_ROWS_CONTAINER}). "
            "Без него нельзя проверить, что строки найдены все"
        )

    balances, defects = _balances(tree)

    if tree.css_first(_HEADER) is None:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="header_missing",
                detail="заголовок таблицы операций отсутствует, разметка изменилась",
            )
        )

    if tree.css_first(_PAGE_FORM) is None:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="pagination_form_missing",
                detail=(
                    f"формы догрузки ({_PAGE_FORM}) на странице нет. Она стоит за "
                    "таблицей на всех наблюдённых снимках этого виджета"
                ),
            )
        )

    found = collect_rows(tree, _ROWS_CONTAINER, _ROW)
    defects.extend(found.defects)

    entries = tuple(_row_of(node, index) for index, node in enumerate(found.rows))
    rows_total = max(len(found.rows), found.children, len(tree.css(_ROW)))

    if rows_total and not entries:
        raise ProtocolChangedError(
            f"кандидатов в операции {rows_total}, собрать не удалось ни одной. "
            "Это изменение разметки, а не пустой счёт"
        )

    ids = [one.transaction_id.or_none() for one in entries if one.transaction_id.is_observed]
    if len(set(ids)) != len(ids):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="duplicate_identifiers",
                detail=(
                    f"операций {len(ids)}, различимых идентификаторов {len(set(ids))}: "
                    "часть записей схлопнется у всякого, кто сложит их в словарь"
                ),
                field_name="transaction_id",
            )
        )

    if not rows_total:
        # Счёта без единой операции никто не видел, и отличить его от
        # переименованного класса строки нечем.
        completeness, reason = Completeness.UNKNOWN, "empty_list_not_observed"
    elif any(one.severity is Severity.PAGE for one in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    else:
        completeness, reason = _totality(tree)

    return BalancePage(
        balances=balances,
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        rows_total=rows_total,
        rows_accepted=len(entries),
        rows_rejected=len(found.rows) - len(entries),
        defects=tuple(defects),
        _entries=entries,
    )
