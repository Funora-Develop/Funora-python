"""Чтение собственных лотов продавца со страницы раздела.

ЗАЧЕМ ОНА, КОГДА ЕСТЬ ВИТРИНА. Витрина на профиле показывает те же предложения и
даже больше полей - количество и признак автовыдачи, - но не даёт ГЛАВНОГО:
идентификатора предложения. На витрине он лежит в строке запроса ссылки, а строку
запроса скелет заменяет одной подписью; наблюдать его там нельзя по устройству
формата.

Здесь он лежит атрибутом. Наблюдено: двадцать строк, двадцать РАЗЛИЧНЫХ значений
data-offer; на витрине - сто пятьдесят восемь строк и ни одного такого атрибута.

Идентификатор нужен всем четырём операциям записи над лотами: включение,
выключение, правка цены, поднятие. Каждая адресует лот по нему.

ПРИЗНАКА «ЛОТ ПОКАЗЫВАЕТСЯ В ВЫДАЧЕ» ЗДЕСЬ НЕТ, и это стоит сказать громко. Узел
.tc-visible-inside по имени похож на него, но им не является, и опровергается это
счётом, а не рассуждением: тот же класс есть на ПУБЛИЧНОЙ витрине - в сорока
строках из ста пятидесяти восьми, ровно там, где есть колонка сервера, и ни в
одной из ста восемнадцати без неё.

Наличие узла определяется НАБОРОМ КОЛОНОК таблицы, а не состоянием лота.
Состояние лота не может зависеть от того, есть ли в таблице колонка сервера.

Все двадцать строк структурно одинаковы - каждый класс внутри строки встречается
ровно двадцать раз, - и различающего признака нет ни одного. Поэтому модель Lot,
требующая is_active обязательным, со страницы НЕ СОБИРАЕТСЯ, и операция
возвращает своё: то, что вправду читается.

ЯЧЕЙКИ ЧИТАЮТСЯ ВНУТРИ СТРОКИ. Классы .tc-server, .tc-desc и .tc-price
встречаются на странице по двадцать одному разу: двадцать строк плюс ШАПКА
таблицы с теми же классами. Счёт по ячейке разошёлся бы с числом лотов на
единицу, и разошёлся бы молча.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import ATTRIBUTES, SELECTORS

__all__ = ["OwnLot", "OwnLotsPage", "parse_own_lots"]

_ROW: Final[str] = SELECTORS["lots.rows"]

#: Класс, которым помечена ВЫКЛЮЧЕННАЯ строка.
#:
#: Литералом, а не из порождённой таблицы, и это не небрежность: генератор
#: классы-признаки не собирает вовсе - ровно так же литералом стоит offer-promo
#: в разборе рынка. Объявление живёт в spec/extraction/lots.yaml, раздел
#: visibility.
#:
#: Собрать их в таблицу стоит, и это отдельная работа: признаков таких в
#: проекте уже три, и все три - литералы.
_OFF_CLASS: Final[str] = "warning"
_SERVER: Final[str] = SELECTORS["lots.fields.server_text"]
_DESCRIPTION: Final[str] = SELECTORS["lots.fields.description_text"]
_PRICE_CELL: Final[str] = SELECTORS["lots.fields.price_cell"]
_PRICE_TEXT: Final[str] = SELECTORS["lots.fields.price_text"]
_CURRENCY: Final[str] = SELECTORS["lots.fields.currency_symbol_text"]
_RAISE: Final[str] = SELECTORS["lots.controls.raise_button"]

_OFFER_ID: Final[str] = ATTRIBUTES["lots.rows.attributes.offer_id"]
_OFFER_HREF: Final[str] = ATTRIBUTES["lots.rows.attributes.offer_href"]
_SORT_VALUE: Final[str] = ATTRIBUTES["lots.fields.price_cell.attributes.sort_value"]
_GAME_ID: Final[str] = ATTRIBUTES["lots.controls.raise_button.attributes.game_id"]
_NODE_ID: Final[str] = ATTRIBUTES["lots.controls.raise_button.attributes.node_id"]


@dataclass(frozen=True, slots=True)
class OwnLot:
    """Одно собственное предложение продавца.

    ПОЛЯ is_active ЗДЕСЬ НЕТ НАМЕРЕННО. Признака, по которому его определить,
    на странице не наблюдалось ни одного, а выдумать его значило бы сказать
    продавцу «лот скрыт» о показанном либо наоборот.

    Attributes:
        offer_id (Observed[str]): Идентификатор предложения. Ради него страница
            и читается: витрина его не даёт.
        offer_href (Observed[str]): Ссылка на правку предложения.
        server_text (Observed[str]): Название сервера, как показано.
        description_text (Observed[str]): Описание, как показано.
        price_text (Observed[str]): Цена без знака валюты, как показана.
        currency_symbol_text (Observed[str]): Знак валюты.
        sort_value (Observed[str]): Значение сортировки из атрибута ячейки цены.
        is_active (bool): Показывается ли лот в выдаче.

            ЧИТАЕТСЯ НАЛИЧИЕМ КЛАССА warning у строки, и появилось это поле
            31.08.2026 - исправлением нашей же ошибки. Три недели модель
            утверждала, что признака на странице нет вовсе; строки одинаковы,
            пока все лоты включены, а у владельца все и были включены.
        row_index (int): Место строки на странице, считая с нуля.
    """

    offer_id: Observed[str]
    offer_href: Observed[str]
    server_text: Observed[str]
    description_text: Observed[str]
    price_text: Observed[str]
    currency_symbol_text: Observed[str]
    sort_value: Observed[str]
    is_active: bool
    row_index: int


@dataclass(frozen=True, slots=True)
class OwnLotsPage:
    """Собственные лоты продавца в одном разделе.

    Attributes:
        completeness (Completeness): Полнота чтения.
        reason (str | None): Причина неполноты.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько строк нашлось.
        rows_accepted (int): Сколько собрано.
        raise_game_id (Observed[str]): Игра из кнопки поднятия.
        raise_node_id (Observed[str]): Раздел из кнопки поднятия.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    completeness: Completeness
    reason: str | None
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    raise_game_id: Observed[str]
    raise_node_id: Observed[str]
    defects: tuple[Defect, ...] = ()
    _lots: tuple[OwnLot, ...] = field(default=(), repr=False)

    def lots(self, *, accept_incomplete: bool = False) -> tuple[OwnLot, ...]:
        """Возвращает собранные лоты.

        Args:
            accept_incomplete (bool): Признание готовности работать с неполным
                результатом.

        Returns:
            tuple[OwnLot, ...]: Лоты в порядке появления.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана. Пропущенный лот здесь опаснее обычного: продавец
                решит, что предложения нет, и заведёт второе.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"лоты прочитаны не полностью ({self.completeness}, причина: {self.reason}), "
                f"собрано {self.rows_accepted} из {self.rows_total}. Передайте "
                "accept_incomplete=True, если готовы работать с неполными данными"
            )
        return self._lots


def _text(node: Node | None, name: str) -> Observed[str]:
    """Читает текст узла как наблюдение.

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
    """Читает СОБСТВЕННЫЙ текст узла, без текста вложенных.

    Цена лежит в узле вместе со знаком валюты, и общий текст склеил бы их в одно
    значение. Разделить их потом нечем: знак валюты у разных валют разный длины.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    own = " ".join(
        part.text(deep=False).strip() for part in [node] if part.text(deep=False) is not None
    ).strip()
    own = " ".join(own.split())
    return Observed.present(own) if own else Observed.empty("")


def _attribute(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Читает атрибут узла как наблюдение.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя атрибута.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"carrier_missing:{field_name}")
    attributes = node.attributes or {}
    if name not in attributes:
        return Observed.missing(f"attribute_absent:{field_name}")
    value = (attributes.get(name) or "").strip()
    return Observed.present(value) if value else Observed.empty("")


def _row(node: Node, index: int) -> tuple[OwnLot, list[Defect]]:
    """Собирает одно предложение из строки таблицы.

    Все ячейки берутся ВНУТРИ строки: те же классы есть у шапки таблицы, и поиск
    по документу дал бы на одну ячейку больше, чем строк.

    Args:
        node (Node): Узел строки.
        index (int): Место строки.

    Returns:
        tuple[OwnLot, list[Defect]]: Предложение и перечень повреждений.
    """
    defects: list[Defect] = []
    offer_id = _attribute(node, _OFFER_ID, "offer_id")
    if not offer_id.is_observed:
        # Идентификатор - то единственное, ради чего страница и читается.
        # Строка без него бесполезна для операций записи, и молчать об этом
        # нельзя: вызывающий принял бы пробел за лот без идентификатора.
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="offer_id_missing",
                detail=(
                    f"у строки нет атрибута {_OFFER_ID}. Именно ради него страница и "
                    "читается: витрина идентификатора не даёт"
                ),
                row_index=index,
                field_name="offer_id",
            )
        )

    price_cell = node.css_first(_PRICE_CELL)
    return (
        OwnLot(
            offer_id=offer_id,
            offer_href=_attribute(node, _OFFER_HREF, "offer_href"),
            server_text=_text(node.css_first(_SERVER), "server_text"),
            description_text=_text(node.css_first(_DESCRIPTION), "description_text"),
            price_text=_own_text(node.css_first(_PRICE_TEXT), "price_text"),
            currency_symbol_text=_text(node.css_first(_CURRENCY), "currency_symbol_text"),
            sort_value=_attribute(price_cell, _SORT_VALUE, "sort_value"),
            # Читается НАЛИЧИЕМ класса: включённая строка его не несёт вовсе.
            # Тот же идиом, что у флажка в форме правки, и по той же причине -
            # отсутствие здесь значимо.
            is_active=_OFF_CLASS not in ((node.attributes or {}).get("class") or "").split(),
            row_index=index,
        ),
        defects,
    )


def parse_own_lots(html: str, *, observed_at: datetime) -> OwnLotsPage:
    """Разбирает страницу собственных лотов раздела.

    Args:
        html (str): Тело страницы.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        OwnLotsPage: Лоты раздела и доводы кнопки поднятия.

    Raises:
        ProtocolChangedError: Если на странице нет ни одной строки И нет кнопки
            поднятия. Пустой список вернуть нельзя: он неотличим от смены
            разметки, а разница решает, заводить ли лот заново.
    """
    tree = HTMLParser(html)
    rows = tree.css(_ROW)
    raise_button = tree.css_first(_RAISE)

    if not rows and raise_button is None:
        raise ProtocolChangedError(
            f"на странице нет ни строк лотов ({_ROW}), ни кнопки поднятия ({_RAISE}). "
            "Пустой список вернуть нельзя: он неотличим от смены разметки, а разница "
            "решает, заводить ли лот заново"
        )

    lots: list[OwnLot] = []
    defects: list[Defect] = []
    for index, node in enumerate(rows):
        lot, row_defects = _row(node, index)
        lots.append(lot)
        defects += row_defects

    damaged = any(one.severity is Severity.ROW for one in defects)
    return OwnLotsPage(
        completeness=Completeness.PARTIAL if damaged else Completeness.COMPLETE,
        reason="rows_damaged" if damaged else None,
        observed_at=observed_at,
        rows_total=len(rows),
        rows_accepted=len(lots),
        raise_game_id=_attribute(raise_button, _GAME_ID, "raise_game_id"),
        raise_node_id=_attribute(raise_button, _NODE_ID, "raise_node_id"),
        defects=tuple(defects),
        _lots=tuple(lots),
    )
