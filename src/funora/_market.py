"""Чтение публичного списка предложений раздела.

То, что видит ПОКУПАТЕЛЬ, ищущий товар. Не витрина продавца и не страница
управления: здесь предложения многих продавцов в одном разделе, и потому здесь
есть колонка, которой нет больше нигде, - сам продавец.

ЛОВУШКА ЗДЕСЬ ОДНА И ДОРОГАЯ. У строки есть атрибут data-user, и по имени он
ровно то, что нужно. Он не то.

Атрибут есть у строки ТОГДА И ТОЛЬКО ТОГДА, когда предложение поднято.
Наблюдено точным совпадением в обе стороны: семьдесят строк с классом
offer-promo - и ровно у этих семидесяти есть data-user; две тысячи девятьсот
тридцать одна строка без класса - и ни у одной из них его нет.

Разбор, читающий продавца оттуда, отдал бы его у двух процентов предложений и
выглядел бы работающим: поля заполняются, ошибок нет, а поднятые предложения
показываются первыми и попадают в глаза первыми.

Продавец лежит в ссылке на профиль внутри строки. Она есть у всех трёх тысяч
одной строки, и различных значений четыреста семьдесят девять - против четырёх
различных data-user. Числа расходятся на два порядка, и это и есть
доказательство.

ЛЕНИВАЯ ЗАГРУЗКА - НЕ УСЕЧЕНИЕ. Две тысячи восемьсот одна строка несёт класс
lazyload-hidden, и все они присутствуют в теле ответа наравне с прочими. Класс
говорит о показе, а не о наличии: список прочитан весь одним ответом, догружать
нечего.
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

__all__ = ["MarketOffer", "MarketPage", "parse_market"]

_ROW: Final[str] = SELECTORS["market.rows"]
_SERVER: Final[str] = SELECTORS["market.fields.server_text"]
_DESCRIPTION: Final[str] = SELECTORS["market.fields.description_text"]
_SELLER_LINK: Final[str] = SELECTORS["market.fields.seller_link"]
_SELLER_NAME: Final[str] = SELECTORS["market.fields.seller_name"]
_PRICE_CELL: Final[str] = SELECTORS["market.fields.price_cell"]
_PRICE_TEXT: Final[str] = SELECTORS["market.fields.price_text"]
_CURRENCY: Final[str] = SELECTORS["market.fields.currency_symbol_text"]

_OFFER_HREF: Final[str] = ATTRIBUTES["market.rows.attributes.offer_href"]
_SERVER_ID: Final[str] = ATTRIBUTES["market.rows.attributes.server_id"]
_FILTER_TYPE: Final[str] = ATTRIBUTES["market.rows.attributes.filter_type"]
_SELLER_HREF: Final[str] = ATTRIBUTES["market.fields.seller_link.attributes.seller_href"]
_SORT_VALUE: Final[str] = ATTRIBUTES["market.fields.price_cell.attributes.sort_value"]
_ONLINE: Final[str] = ATTRIBUTES["market.markers.online.attribute"]

#: Классы, которыми площадка помечает поднятое предложение.
#:
#: Их два, и они не одно и то же: offer-promo наблюдён у семидесяти строк,
#: offer-promoted - у одной, и она же первая на странице. Что означает различие,
#: не установлено: наблюдалась одна страница.
_PROMO: Final[str] = "offer-promo"
_PROMOTED: Final[str] = "offer-promoted"


@dataclass(frozen=True, slots=True)
class MarketOffer:
    """Одно предложение в списке раздела.

    Attributes:
        offer_href (Observed[str]): Ссылка на предложение. Идентификатор в ней
            лежит в строке запроса, и скелет её маскирует: наблюдать его здесь
            нельзя.
        seller_href (Observed[str]): Ссылка на профиль продавца. НАСТОЯЩИЙ
            продавец строки - не атрибут data-user, которого у большинства строк
            нет вовсе.
        seller_name_text (Observed[str]): Отображаемое имя продавца.
        seller_online (bool): Наблюдался ли признак «в сети».
        server_text (Observed[str]): Название сервера, как показано.
        server_id (Observed[str]): Идентификатор сервера из атрибута.
        filter_type (Observed[str]): Значение фильтра строки.
        description_text (Observed[str]): Описание, как показано.
        price_text (Observed[str]): Цена без знака валюты.
        currency_symbol_text (Observed[str]): Знак валюты.
        sort_value (Observed[str]): Значение сортировки ячейки цены.
        promoted (bool): Помечено ли предложение поднятым.
        row_index (int): Место строки, считая с нуля.
    """

    offer_href: Observed[str]
    seller_href: Observed[str]
    seller_name_text: Observed[str]
    seller_online: bool
    server_text: Observed[str]
    server_id: Observed[str]
    filter_type: Observed[str]
    description_text: Observed[str]
    price_text: Observed[str]
    currency_symbol_text: Observed[str]
    sort_value: Observed[str]
    promoted: bool
    row_index: int


@dataclass(frozen=True, slots=True)
class MarketPage:
    """Публичный список предложений одного раздела.

    Attributes:
        completeness (Completeness): Полнота чтения.
        reason (str | None): Причина неполноты.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько строк нашлось.
        rows_accepted (int): Сколько собрано.
        rows_lazy (int): Сколько строк помечено ленивой загрузкой. Усечением это
            НЕ является: разметка отдана целиком, класс говорит о показе.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    completeness: Completeness
    reason: str | None
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_lazy: int
    defects: tuple[Defect, ...] = ()
    _offers: tuple[MarketOffer, ...] = field(default=(), repr=False)

    def offers(self, *, accept_incomplete: bool = False) -> tuple[MarketOffer, ...]:
        """Возвращает собранные предложения.

        Args:
            accept_incomplete (bool): Признание готовности работать с неполным
                результатом.

        Returns:
            tuple[MarketOffer, ...]: Предложения в порядке появления.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"список прочитан не полностью ({self.completeness}, причина: "
                f"{self.reason}), собрано {self.rows_accepted} из {self.rows_total}. "
                "Передайте accept_incomplete=True, если готовы работать с неполными "
                "данными"
            )
        return self._offers


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
    """Читает собственный текст узла, без текста вложенных.

    Args:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    own = " ".join((node.text(deep=False) or "").split())
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


def _classes(node: Node) -> set[str]:
    """Возвращает классы узла множеством.

    Разбор идёт по ТОКЕНАМ, а не по подстроке: подстрока «tc-server» входит и в
    «tc-server-inside», и счёт по ней разошёлся бы вдвое.

    Args:
        node (Node): Узел.

    Returns:
        set[str]: Классы.
    """
    return set(((node.attributes or {}).get("class") or "").split())


def _row(node: Node, index: int) -> tuple[MarketOffer, list[Defect]]:
    """Собирает одно предложение из строки.

    Продавец читается из ССЫЛКИ НА ПРОФИЛЬ, а не из атрибута data-user: тот есть
    только у поднятых строк, и разбор по нему отдал бы продавца у двух процентов
    списка.

    Args:
        node (Node): Узел строки.
        index (int): Место строки.

    Returns:
        tuple[MarketOffer, list[Defect]]: Предложение и перечень повреждений.
    """
    defects: list[Defect] = []
    seller_link = node.css_first(_SELLER_LINK)
    seller_href = _attribute(seller_link, _SELLER_HREF, "seller_href")
    if not seller_href.is_observed:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="seller_link_missing",
                detail=(
                    f"у строки нет ссылки на профиль продавца ({_SELLER_LINK}, атрибут "
                    f"{_SELLER_HREF}). Это единственный носитель продавца, годный для "
                    "всех строк: атрибут data-user есть только у поднятых"
                ),
                row_index=index,
                field_name="seller_href",
            )
        )

    price_cell = node.css_first(_PRICE_CELL)
    classes = _classes(node)
    return (
        MarketOffer(
            offer_href=_attribute(node, _OFFER_HREF, "offer_href"),
            seller_href=seller_href,
            seller_name_text=_text(node.css_first(_SELLER_NAME), "seller_name_text"),
            # Признак читается НАЛИЧИЕМ атрибута, а не значением: значение
            # наблюдалось одно на всех строках, где атрибут есть, а отсутствие
            # наблюдалось у восьмисот девятнадцати строк той же страницы.
            seller_online=_ONLINE in (node.attributes or {}),
            server_text=_text(node.css_first(_SERVER), "server_text"),
            server_id=_attribute(node, _SERVER_ID, "server_id"),
            filter_type=_attribute(node, _FILTER_TYPE, "filter_type"),
            description_text=_text(node.css_first(_DESCRIPTION), "description_text"),
            price_text=_own_text(node.css_first(_PRICE_TEXT), "price_text"),
            currency_symbol_text=_text(node.css_first(_CURRENCY), "currency_symbol_text"),
            sort_value=_attribute(price_cell, _SORT_VALUE, "sort_value"),
            promoted=bool(classes & {_PROMO, _PROMOTED}),
            row_index=index,
        ),
        defects,
    )


def parse_market(html: str, *, observed_at: datetime) -> MarketPage:
    """Разбирает публичный список предложений раздела.

    Args:
        html (str): Тело страницы.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        MarketPage: Предложения раздела.

    Raises:
        ProtocolChangedError: Если на странице нет ни одной строки. Пустой список
            вернуть нельзя: он неотличим от смены разметки, а разница решает,
            искать ли товар в другом разделе.
    """
    tree = HTMLParser(html)
    rows = tree.css(_ROW)
    if not rows:
        raise ProtocolChangedError(
            f"на странице нет ни одной строки предложения ({_ROW}). Пустой список "
            "вернуть нельзя: он неотличим от смены разметки, а разница решает, искать "
            "ли товар в другом разделе"
        )

    offers: list[MarketOffer] = []
    defects: list[Defect] = []
    lazy = 0
    for index, node in enumerate(rows):
        if "lazyload-hidden" in _classes(node):
            lazy += 1
        offer, row_defects = _row(node, index)
        offers.append(offer)
        defects += row_defects

    damaged = any(one.severity is Severity.ROW for one in defects)
    return MarketPage(
        completeness=Completeness.PARTIAL if damaged else Completeness.COMPLETE,
        reason="rows_damaged" if damaged else None,
        observed_at=observed_at,
        rows_total=len(rows),
        rows_accepted=len(offers),
        rows_lazy=lazy,
        defects=tuple(defects),
        _offers=tuple(offers),
    )
