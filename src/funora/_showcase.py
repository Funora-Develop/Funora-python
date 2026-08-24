"""Разбор витрины продавца с его профиля.

Витрина - ПУБЛИЧНЫЙ вид предложений: то, что видит покупатель. Она не то же, что
страница управления лотами: ни признака включённости, ни правки цены, ни кнопки
поднятия на ней нет, и потому запись отличается по типу от Lot.

Четыре решения объясняют устройство.

Раздел витрины ищется по .offer-tc-container, а не по .offer. Класс offer на
профиле носят ЧЕТЫРНАДЦАТЬ узлов, а разделов тринадцать: четырнадцатый - блок
отзывов, переиспользующий тот же класс. Разбор, взявший .offer за раздел, нашёл
бы раздел без единого предложения и без заголовка.

Адрес предложения отдаётся ЦЕЛИКОМ, непрозрачной строкой. Идентификатор лежит в
строке запроса, а формат скелета заменяет её одной подписью: разобрать параметр
по снимку нельзя, и проверить такой разбор тоже нечем. Правило, которое ни одна
проверка не покрывает, уже однажды обошлось дорого - на разборе суммы со
страницы заказа.

Поля ищутся ВНУТРИ строки. Заголовок каждой таблицы несёт ячейку цены с тем же
классом: по документу их сто семьдесят одна на сто пятьдесят восемь строк.

Чтение объявляется неполным всегда. Признака усечения на странице нет ни одного
- но у семи разделов из тринадцати ровно по двадцать строк, и это положительный
довод в пользу обрезания, а не отсутствие довода против.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import SELECTORS

__all__ = ["ShowcaseOffer", "ShowcasePage", "ShowcaseSection", "parse_showcase"]

_SECTION: Final[str] = SELECTORS["showcase.sections.container"]
_TITLE: Final[str] = SELECTORS["showcase.sections.title"]
_MANAGE: Final[str] = SELECTORS["showcase.sections.manage_link"]

_ROW: Final[str] = SELECTORS["showcase.offers.row"]
_DESCRIPTION: Final[str] = SELECTORS["showcase.offers.fields.description_text"]
_PRICE_CELL: Final[str] = SELECTORS["showcase.offers.fields.price_cell"]
_PRICE_TEXT: Final[str] = SELECTORS["showcase.offers.fields.price_text"]
_SYMBOL: Final[str] = SELECTORS["showcase.offers.fields.currency_symbol_text"]

_AMOUNT: Final[str] = SELECTORS["showcase.offers.optional_columns.amount"]
_SERVER: Final[str] = SELECTORS["showcase.offers.optional_columns.server"]
_AUTO_DELIVERY: Final[str] = SELECTORS["showcase.offers.optional_columns.auto_delivery"]

#: Сколько строк в разделе наводит на мысль об обрезании.
#:
#: Наблюдено: у семи разделов из тринадцати ровно столько. Семь совпадений на
#: одну случайность не похожи - но предел выведен из одного снимка одного
#: продавца, и доказательством не является.
_SUSPICIOUS_ROWS: Final[int] = 20


@dataclass(frozen=True, slots=True)
class ShowcaseOffer:
    """Одно предложение на витрине.

    Не Lot и намеренно отличается от него типом. Lot требует признака
    включённости и цены с кодом валюты; витрина не даёт ни того ни другого -
    она показывает то, что видит покупатель.

    Attributes:
        offer_href (Observed[str]): Адрес предложения целиком, непрозрачной
            строкой. Идентификатор лежит в строке запроса и отдельно не
            разбирается.
        description_text (Observed[str]): Описание предложения.
        price_text (Observed[str]): Цена без знака валюты, как показана.
        currency_symbol_text (Observed[str]): Знак валюты.
        sort_value (Observed[str]): Значение сортировки из атрибута ячейки цены.
        amount_text (Observed[str]): Остаток, если раздел его показывает.
        server_text (Observed[str]): Сервер, если раздел его показывает.
        auto_delivery (Observed[bool]): Признак автоматической выдачи.
        row_index (int): Место строки в разделе, считая с нуля.
    """

    offer_href: Observed[str]
    description_text: Observed[str]
    price_text: Observed[str]
    currency_symbol_text: Observed[str]
    sort_value: Observed[str]
    amount_text: Observed[str]
    server_text: Observed[str]
    auto_delivery: Observed[bool]
    row_index: int


@dataclass(frozen=True, slots=True)
class ShowcaseSection:
    """Один раздел витрины.

    Attributes:
        title_text (Observed[str]): Название раздела.
        category_href (Observed[str]): Адрес раздела.
        manage_href (Observed[str]): Адрес страницы управления лотами раздела.
        offers (tuple[ShowcaseOffer, ...]): Предложения раздела.
        position (int): Место раздела на странице, считая с нуля.
    """

    title_text: Observed[str]
    category_href: Observed[str]
    manage_href: Observed[str]
    offers: tuple[ShowcaseOffer, ...]
    position: int


@dataclass(frozen=True, slots=True)
class ShowcasePage:
    """Результат чтения витрины.

    Attributes:
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        sections_total (int): Сколько разделов нашлось, штук.
        offers_total (int): Сколько предложений нашлось, штук.
        capped_sections (int): Сколько разделов показали ровно двадцать строк.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    completeness: Completeness
    reason: str
    observed_at: datetime
    sections_total: int
    offers_total: int
    capped_sections: int
    defects: tuple[Defect, ...] = ()
    _sections: tuple[ShowcaseSection, ...] = field(repr=False, default=())

    def sections(self, *, accept_incomplete: bool = False) -> tuple[ShowcaseSection, ...]:
        """Возвращает разделы витрины с их предложениями.

        Args:
            accept_incomplete (bool): Признание того, что результат может быть
                неполным. Без него неполный результат не выдаётся.

        Returns:
            tuple[ShowcaseSection, ...]: Разделы в порядке появления.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана. Витрина не объявляется полной ни разу, и признание
                требуется всегда - это неудобно нарочно.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"результат неполон ({self.completeness}, причина: {self.reason}), "
                f"разделов {self.sections_total}, предложений {self.offers_total}, "
                f"разделов ровно с {_SUSPICIOUS_ROWS} строками {self.capped_sections}. "
                "Передайте accept_incomplete=True, если готовы работать с неполными данными"
            )
        return self._sections


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

    Знак валюты лежит отдельным узлом внутри ячейки цены, и текст целиком
    склеил бы их.

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


def _offer(row: Node, index: int) -> ShowcaseOffer:
    """Собирает одно предложение.

    Args:
        row (Node): Узел строки.
        index (int): Место строки в разделе, считая с нуля.

    Returns:
        ShowcaseOffer: Предложение.
    """
    price_cell = row.css_first(_PRICE_CELL)
    return ShowcaseOffer(
        offer_href=_attribute(row, "href", "offer_href"),
        description_text=_text(row.css_first(_DESCRIPTION), "description_text"),
        price_text=_own_text(row.css_first(_PRICE_TEXT), "price_text"),
        currency_symbol_text=_text(row.css_first(_SYMBOL), "currency_symbol_text"),
        sort_value=_attribute(price_cell, "data-s", "sort_value"),
        # Колонки остатка и сервера есть НЕ ВО ВСЕХ разделах, и это наблюдено.
        # Отсутствие узла здесь - факт о разделе, а не о нашем незнании, но
        # различить эти два случая по снимку нечем: узла нет в обоих.
        amount_text=_text(row.css_first(_AMOUNT), "amount_text"),
        server_text=_text(row.css_first(_SERVER), "server_text"),
        auto_delivery=Observed.present(row.css_first(_AUTO_DELIVERY) is not None),
        row_index=index,
    )


def _section(container: Node, index: int) -> tuple[ShowcaseSection, list[Defect]]:
    """Собирает один раздел витрины.

    Заголовок раздела лежит НЕ внутри контейнера строк, а рядом с ним - в общей
    обёртке. Ищется он поэтому у родителя, и промах здесь не заметить: разбор
    вернул бы раздел без названия и без ссылки.

    Args:
        container (Node): Контейнер строк раздела.
        index (int): Место раздела на странице, считая с нуля.

    Returns:
        tuple[ShowcaseSection, list[Defect]]: Раздел и перечень повреждений.
    """
    outer = container.parent
    title = outer.css_first(_TITLE) if outer is not None else None
    manage = outer.css_first(_MANAGE) if outer is not None else None

    defects: list[Defect] = []
    if title is None:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="section_title_missing",
                detail=f"раздел {index}: заголовка ({_TITLE}) рядом с контейнером строк нет",
            )
        )

    rows = container.css(_ROW)
    return (
        ShowcaseSection(
            title_text=_text(title, "title_text"),
            category_href=_attribute(title, "href", "category_href"),
            manage_href=_attribute(manage, "href", "manage_href"),
            offers=tuple(_offer(row, at) for at, row in enumerate(rows)),
            position=index,
        ),
        defects,
    )


def parse_showcase(html: str, observed_at: datetime) -> ShowcasePage:
    """Разбирает витрину продавца со страницы его профиля.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        ShowcasePage: Разделы витрины с их предложениями.

    Raises:
        ProtocolChangedError: Если контейнеров разделов нет ни одного: пустую
            витрину вернуть нельзя, она неотличима от смены разметки.
    """
    tree = HTMLParser(html)
    containers = tree.css(_SECTION)

    if not containers:
        raise ProtocolChangedError(
            f"на профиле нет ни одного контейнера раздела витрины ({_SECTION}). "
            "Пустую витрину вернуть нельзя: она неотличима от смены разметки"
        )

    defects: list[Defect] = []
    sections: list[ShowcaseSection] = []
    for index, container in enumerate(containers):
        section, section_defects = _section(container, index)
        defects.extend(section_defects)
        sections.append(section)

    offers_total = sum(len(one.offers) for one in sections)
    rows_by_document = len(tree.css(_ROW))
    if offers_total != rows_by_document:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="offers_outside_sections",
                detail=(
                    f"предложений внутри разделов {offers_total}, а по документу "
                    f"{rows_by_document}: часть строк оказалась вне контейнеров"
                ),
            )
        )

    capped = sum(1 for one in sections if len(one.offers) == _SUSPICIOUS_ROWS)

    if any(one.severity is Severity.PAGE for one in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    elif capped:
        # Положительный довод в пользу обрезания: столько разделов показали
        # ровно предельное число строк.
        completeness, reason = Completeness.PARTIAL, "sections_look_capped"
    else:
        # Признака усечения на странице нет ни одного - и это НЕ основание
        # объявить витрину полной. Отсутствие признака говорит о том, кто искал.
        completeness, reason = Completeness.PARTIAL, "showcase_cap_unobserved"

    return ShowcasePage(
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        sections_total=len(sections),
        offers_total=offers_total,
        capped_sections=capped,
        defects=tuple(defects),
        _sections=tuple(sections),
    )
