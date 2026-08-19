"""Разбор списка диалогов со страницы /chat/.

Модуль чистый по тем же причинам, что и разбор заказов: результат обязан
повторяться на сохранённом снимке, иначе снимки бесполезны как способ заметить
изменение вёрстки.

Отличий от списка заказов три, и каждое стоит назвать.

Позиции непрозрачны. Атрибуты data-node-msg и data-user-msg наблюдаются как
строки и сравниваются только на равенство. Наблюдения показали, что это счётчик,
общий для площадки, а не для диалога, поэтому разность двух значений одного
диалога измеряет сообщения всей площадки, а не пропущенные здесь. Число,
полученное вычитанием, выглядит осмысленным и означает не то, что кажется, - это
хуже явной ошибки, и потому арифметика над ними запрещена.

Признак непрочитанного выведен, а не наблюдён. Расхождения пары при непрочитанном
диалоге мы не видели ни разу: во всех снимках счётчик непрочитанного скрыт и
позиции совпадают у всех сорока семи диалогов. Отсюда пометка inferred и
обязанность вызывающего считаться с тем, что вывод неверен.

Список ограничен и разметки дочитывания не имеет. Диалог, вытесненный за границу,
исчезает из наблюдения целиком, поэтому полнота списка объявляется честно, а не
считается полной по факту успешного разбора.
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

__all__ = ["ChatListEntry", "ChatsPage", "parse_chats_page"]

#: Внешний блок переписки. Присутствует и при выбранном диалоге, и без него.
_WIDGET: Final[str] = ".chat-contacts"

#: Непосредственный контейнер строк. Второй, независимый от класса строки признак.
_ROWS_CONTAINER: Final[str] = ".contact-list"

#: Селектор строки диалога.
_ROW: Final[str] = "a.contact-item"

#: Счётчик непрочитанного в шапке.
_UNREAD_BADGE: Final[str] = "span.badge-chat"

#: Идентификатор диалога в адресе строки.
_NODE_IN_HREF: Final[re.Pattern[str]] = re.compile(r"[?&]node=([^&#]+)")


@dataclass(frozen=True, slots=True)
class ChatListEntry:
    """Запись диалога из списка.

    Attributes:
        node_id (str): Идентификатор диалога.
        href (str): Адрес переписки.
        row_index (int): Порядковый номер строки, с нуля.
        last_message_position (Observed[str]): Позиция последнего сообщения в
            диалоге. Непрозрачна: сравнивать можно только на равенство.
        own_position (Observed[str]): Позиция, до которой аккаунт прочитал.
            Смысл поля выведен, а не наблюдён.
        unread (Observed[bool]): Признак непрочитанного. Выведен из расхождения
            позиций и потому помечен как inferred.
        counterparty_name (Observed[str]): Имя собеседника, текст.
        preview_text (Observed[str]): Начало последнего сообщения, текст.
        time_text (Observed[str]): Время последнего сообщения, текст. Точного
            момента страница не даёт.
    """

    node_id: str
    href: str
    row_index: int
    last_message_position: Observed[str]
    own_position: Observed[str]
    unread: Observed[bool]
    counterparty_name: Observed[str]
    preview_text: Observed[str]
    time_text: Observed[str]


@dataclass(frozen=True, slots=True)
class ChatsPage:
    """Результат чтения списка диалогов.

    Записи получают через :meth:`rows` по той же причине, что и в списке
    заказов: открытый список делает неполноту незаметной.

    Attributes:
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько кандидатов в строки нашлось.
        rows_accepted (int): Сколько записей собрано.
        rows_rejected (int): Сколько строк отброшено.
        unread_badge_visible (Observed[bool]): Виден ли счётчик непрочитанного.
            Наблюдается структурно и не зависит от трактовки позиций.
        defects (tuple[Defect, ...]): Обнаруженные повреждения.
    """

    completeness: Completeness
    reason: str
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    unread_badge_visible: Observed[bool]
    defects: tuple[Defect, ...]
    _entries: tuple[ChatListEntry, ...] = field(repr=False, default=())

    def rows(self, *, accept_incomplete: bool = False) -> tuple[ChatListEntry, ...]:
        """Возвращает собранные записи.

        Args:
            accept_incomplete (bool): Признание готовности работать с неполным
                результатом.

        Returns:
            tuple[ChatListEntry, ...]: Записи в порядке появления на странице.

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
        """Возвращает число собранных записей.

        Returns:
            int: Число собранных записей.
        """
        return len(self._entries)


def _text(node: Node | None, name: str) -> Observed[str]:
    """Извлекает текст узла как наблюдаемое значение.

    Args:
        node (Node | None): Узел или None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _position(row: Node, attribute: str) -> Observed[str]:
    """Извлекает непрозрачную позицию из атрибута строки.

    Args:
        row (Node): Узел строки диалога.
        attribute (str): Имя атрибута.

    Returns:
        Observed[str]: Значение как строка. Ведущие нули сохраняются, ширина не
        проверяется: наблюдённые десять знаков - факт о сегодняшнем дне, а не
        правило, и переход площадки на одиннадцать не должен ломать разбор.
    """
    raw = (row.attributes or {}).get(attribute)
    if raw is None:
        return Observed.missing(f"attribute_absent:{attribute}")
    value = raw.strip()
    return Observed.present(value) if value else Observed.empty("")


def _parse_row(row: Node, index: int) -> tuple[ChatListEntry | None, list[Defect]]:
    """Разбирает одну строку диалога.

    Args:
        row (Node): Узел строки.
        index (int): Порядковый номер строки.

    Returns:
        tuple[ChatListEntry | None, list[Defect]]: Запись либо None, если строка
        непригодна, и перечень повреждений.
    """
    defects: list[Defect] = []

    node_id = ((row.attributes or {}).get("data-id") or "").strip()
    href = ((row.attributes or {}).get("href") or "").strip()
    if not node_id:
        match = _NODE_IN_HREF.search(href)
        node_id = match.group(1) if match else ""

    if not node_id:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="node_id_not_extractable",
                detail="у строки нет ни атрибута с идентификатором, ни адреса с ним",
                row_index=index,
            )
        )
        return None, defects

    last = _position(row, "data-node-msg")
    own = _position(row, "data-user-msg")

    if last.is_observed and own.is_observed:
        # Правило выведено, а не наблюдено: расхождения пары при непрочитанном
        # диалоге мы не видели ни разу. Пометка inferred обязывает вызывающего
        # считаться с тем, что вывод неверен.
        unread: Observed[bool] = Observed.present(last.value != own.value, Confidence.INFERRED)
    else:
        unread = Observed.missing("positions_incomplete")

    entry = ChatListEntry(
        node_id=node_id,
        href=href,
        row_index=index,
        last_message_position=last,
        own_position=own,
        unread=unread,
        counterparty_name=_text(row.css_first(".media-user-name"), "counterparty_name"),
        preview_text=_text(row.css_first(".contact-item-message"), "preview_text"),
        time_text=_text(row.css_first(".contact-item-time"), "time_text"),
    )

    for name in ("last_message_position", "own_position", "counterparty_name", "time_text"):
        if not getattr(entry, name).is_observed:
            defects.append(
                Defect(
                    severity=Severity.FIELD,
                    code="field_not_observed",
                    detail="поле строки не найдено там, где ожидалось",
                    row_index=index,
                    field_name=name,
                )
            )

    return entry, defects


#: Поля, отсутствие которых во всех строках означает поломку страницы.
_PAGE_LEVEL_FIELDS: Final[tuple[str, ...]] = (
    "last_message_position",
    "own_position",
    "counterparty_name",
    "time_text",
)


def parse_chats_page(html: str, *, observed_at: datetime) -> ChatsPage:
    """Разбирает страницу списка диалогов.

    Args:
        html (str): Тело страницы, уже признанное пригодным.
        observed_at (datetime): Момент наблюдения.

    Returns:
        ChatsPage: Записи вместе с полнотой и перечнем повреждений.

    Raises:
        ProtocolChangedError: Если разметка изменилась настолько, что читать
            нечего.
    """
    tree = HTMLParser(html)
    defects: list[Defect] = []

    if tree.css_first(_WIDGET) is None:
        raise ProtocolChangedError(
            f"на странице нет блока переписки ({_WIDGET}). Пустой список "
            "вернуть нельзя: он неотличим от отсутствия диалогов"
        )

    if tree.css_first(_ROWS_CONTAINER) is None:
        raise ProtocolChangedError(
            f"на странице нет контейнера строк ({_ROWS_CONTAINER}). Без него "
            "нельзя проверить, что строки найдены все"
        )

    found = collect_rows(tree, _ROWS_CONTAINER, _ROW)
    defects.extend(found.defects)

    entries: list[ChatListEntry] = []
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

    badge = tree.css_first(_UNREAD_BADGE)
    if badge is None:
        unread_badge: Observed[bool] = Observed.missing("selector_no_match:unread_badge")
    else:
        hidden = "hidden" in ((badge.attributes or {}).get("class") or "").split()
        unread_badge = Observed.present(not hidden)

    if not rows_total:
        completeness, reason = Completeness.UNKNOWN, "empty_list_not_observed"
    elif not defects:
        completeness, reason = Completeness.COMPLETE, "all_rows_parsed"
    elif any(d.severity is Severity.PAGE for d in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    else:
        completeness, reason = Completeness.PARTIAL, "row_defects"

    return ChatsPage(
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        rows_total=rows_total,
        rows_accepted=rows_accepted,
        rows_rejected=rows_rejected,
        unread_badge_visible=unread_badge,
        defects=tuple(defects),
        _entries=tuple(entries),
    )
