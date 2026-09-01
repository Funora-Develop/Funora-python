"""Чтение второго рынка площадки: /chips/{id}/.

ЧЕМ ЧИП ОТЛИЧАЕТСЯ ОТ ЛОТА. Лот - штучное предложение с описанием: аккаунт,
ключ, услуга. Чип - предложение ПО КОЛИЧЕСТВУ: игровая валюта, ресурсы,
внутриигровые единицы. Продаётся не «вот эта вещь», а «столько-то по такой
цене за единицу».

Разница видна одним узлом и одним пропуском:

  .tc-amount ЕСТЬ здесь и нет у лотов - количество с единицей измерения;
  .tc-desc НЕТ здесь и есть у лотов - описания у чипа не бывает.

ОТСУТСТВИЕ ОПИСАНИЯ ЗАПИСАНО ПОЛОЖИТЕЛЬНО, в spec/extraction/chips.yaml. Без
этого разбор искал бы описание и объявлял бы строку повреждённой у всех ста
семнадцати - то есть весь рынок был бы прочитан неполно.

ЦЕНА ЗДЕСЬ ЗА ЕДИНИЦУ, а не за предложение целиком. Общей суммы на странице нет
вовсе: её считает покупатель, выбрав количество. Сравнивать цены чипов между
собой можно, а складывать в стоимость покупки - нельзя, и модель этого не
предлагает.

Наблюдено 31.08.2026 гостем: раздел 1, сто семнадцать строк.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from urllib.parse import parse_qsl, urlsplit

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from .errors import IncompleteResultError, ProtocolChangedError
from .extraction import ATTRIBUTES, QUERY_PARAMS, SELECTORS

__all__ = ["ChipsOffer", "ChipsPage", "parse_chips"]

_ROWS: Final[str] = SELECTORS["chips.rows"]
_SELLER_LINK: Final[str] = SELECTORS["chips.fields.seller_link"]
_SELLER_NAME: Final[str] = SELECTORS["chips.fields.seller_name"]
_SERVER: Final[str] = SELECTORS["chips.fields.server_text"]
_AMOUNT: Final[str] = SELECTORS["chips.fields.amount_text"]
_AMOUNT_UNIT: Final[str] = SELECTORS["chips.fields.amount_unit_text"]
_PRICE: Final[str] = SELECTORS["chips.fields.price_text"]

#: Числовой носитель количества. Атрибут data-s у той же ячейки.
#:
#: ЧТО ОН ЗНАЧИТ - ВЫВЕДЕНО АРИФМЕТИКОЙ, а не предположено. Подпись скелета
#: хранит длину значения; сверив длины атрибута и текста по всем строкам,
#: получаем без исключений: разница равна числу разделителей тысяч, и число
#: это ровно такое, каким ему положено быть при такой длине.
#:
#: Значит атрибут - то же самое число, что и в тексте, но БЕЗ разделителей.
_AMOUNT_VALUE: Final[str] = SELECTORS["chips.numeric_carriers.amount"]

#: Числовой носитель у ячейки цены.
#:
#: У ЦЕНЫ ТА ЖЕ АРИФМЕТИКА НЕ СХОДИТСЯ: атрибут две-три цифры при тексте в
#: пять-шесть знаков, и разделителей тысяч в цене за единицу быть не может.
#: Что это за число, не установлено, и потому имя поля цены не обещает.
_PRICE_SORT: Final[str] = SELECTORS["chips.numeric_carriers.price"]
_CURRENCY: Final[str] = SELECTORS["chips.fields.currency_symbol_text"]

_OFFER_HREF: Final[str] = ATTRIBUTES["chips.rows.attributes.offer_href"]
_SELLER_HREF: Final[str] = ATTRIBUTES["chips.fields.seller_link.attributes.seller_href"]
_SERVER_ID: Final[str] = ATTRIBUTES["chips.rows.attributes.server_id"]
_ONLINE: Final[str] = ATTRIBUTES["chips.markers.online.attribute"]
_OFFER_ID_PARAM: Final[str] = QUERY_PARAMS["chips.rows.attributes.offer_id"]


@dataclass(frozen=True, slots=True)
class ChipsOffer:
    """Одно предложение на рынке чипов.

    Attributes:
        offer_id (Observed[str]): Идентификатор предложения из строки запроса.
        offer_href (Observed[str]): Ссылка на предложение целиком.
        seller_href (Observed[str]): Ссылка на профиль продавца.
        seller_name_text (Observed[str]): Отображаемое имя продавца.
        seller_online (bool): Наблюдался ли признак присутствия.
        server_text (Observed[str]): Название сервера, как показано.
        server_id (Observed[str]): Идентификатор сервера из атрибута.
        amount_text (Observed[str]): Количество вместе с единицей измерения.
        amount_unit_text (Observed[str]): Единица измерения количества.
        amount (Observed[int]): Количество ЧИСЛОМ.

            Читается из атрибута data-s той же ячейки, и связь его с текстом
            ВЫВЕДЕНА АРИФМЕТИКОЙ: длина текста минус длина атрибута равна числу
            разделителей тысяч на всех строках снимка без исключений.

            Ради этого поля страница и перечитывалась: перемножить количество на
            цену за единицу - то, за чем покупатель приходит на рынок по
            количеству.
        price_sort (Observed[str]): Числовой носитель у ячейки цены, КАК ЕСТЬ.

            ЦЕНОЙ НЕ НАЗВАН НАРОЧНО. Та же арифметика на нём не сходится:
            атрибут две-три цифры при тексте в пять-шесть, а разделителей тысяч
            в цене за единицу быть не может.

            Что это за число - цена в иных единицах, ключ сортировки,
            округление - не установлено. Назвать его ценой значило бы приписать
            смысл ДЕНЬГАМ, не проверив.
        price_text (Observed[str]): Цена ЗА ЕДИНИЦУ, без знака валюты.
        currency_symbol_text (Observed[str]): Знак валюты.
        row_index (int): Место строки, считая с нуля.
    """

    offer_id: Observed[str]
    offer_href: Observed[str]
    seller_href: Observed[str]
    seller_name_text: Observed[str]
    seller_online: bool
    server_text: Observed[str]
    server_id: Observed[str]
    amount_text: Observed[str]
    amount_unit_text: Observed[str]
    amount: Observed[int]
    price_sort: Observed[str]
    price_text: Observed[str]
    currency_symbol_text: Observed[str]
    row_index: int


@dataclass(frozen=True, slots=True)
class ChipsPage:
    """Публичный список предложений одного раздела чипов.

    Attributes:
        completeness (Completeness): Полнота чтения.
        reason (str | None): Причина неполноты.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько строк нашлось.
        rows_accepted (int): Сколько собрано.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    completeness: Completeness
    reason: str | None
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    defects: tuple[Defect, ...] = ()
    _offers: tuple[ChipsOffer, ...] = field(default=(), repr=False)

    def offers(self, *, accept_incomplete: bool = False) -> tuple[ChipsOffer, ...]:
        """Возвращает собранные предложения.

        Args:
            accept_incomplete (bool): Признание готовности работать с неполным
                результатом.

        Returns:
            tuple[ChipsOffer, ...]: Предложения в порядке появления.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"список чипов прочитан не полностью ({self.completeness}, причина: "
                f"{self.reason}), собрано {self.rows_accepted} из {self.rows_total}. "
                "Передайте accept_incomplete=True, если готовы работать с неполными "
                "данными"
            )
        return self._offers


def _text(node: Node | None, name: str) -> Observed[str]:
    """Читает текст узла как наблюдение.

    Аргументы:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины ненаблюдения.

    Возвращает:
        Observed[str]: Текст либо причина.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    text = (node.text() or "").strip()
    return Observed.present(text) if text else Observed.missing(f"empty:{name}")


def _own_text(node: Node | None, name: str) -> Observed[str]:
    """Читает СОБСТВЕННЫЙ текст узла, без вложенных.

    Нужен там, где в ячейке лежит и значение, и его единица: общий текст
    склеивает их без разделителя, и разделить потом нечем.

    Аргументы:
        node (Node | None): Узел либо None.
        name (str): Имя поля для причины ненаблюдения.

    Возвращает:
        Observed[str]: Текст либо причина.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{name}")
    text = "".join(
        one.text(deep=False) for one in node.iter(include_text=True) if one.tag == "-text"
    ).strip()
    return Observed.present(text) if text else Observed.missing(f"empty:{name}")


def _attribute(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Читает атрибут узла как наблюдение.

    Аргументы:
        node (Node | None): Узел либо None.
        name (str): Имя атрибута.
        field_name (str): Имя поля для причины ненаблюдения.

    Возвращает:
        Observed[str]: Значение либо причина.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{field_name}")
    value = (node.attributes or {}).get(name)
    if value is None or not value.strip():
        return Observed.missing(f"attribute_missing:{name}")
    return Observed.present(value.strip())


def _query_param(href: Observed[str], name: str, field_name: str) -> Observed[str]:
    """Достаёт параметр строки запроса из наблюдённого адреса.

    Аргументы:
        href (Observed[str]): Адрес, каким его прочитали.
        name (str): Имя параметра.
        field_name (str): Имя поля для причины ненаблюдения.

    Возвращает:
        Observed[str]: Значение либо причина.
    """
    if not href.is_observed:
        return Observed.missing(f"carrier_missing:{field_name}")
    for key, value in parse_qsl(urlsplit(href.value).query, keep_blank_values=True):
        if key != name:
            continue
        return Observed.present(value) if value else Observed.missing(f"empty:{field_name}")
    return Observed.missing(f"no_query_param:{name}")


def _row(node: Node, index: int) -> tuple[ChipsOffer, list[Defect]]:
    """Собирает одно предложение из строки.

    Аргументы:
        node (Node): Узел строки.
        index (int): Место строки.

    Возвращает:
        tuple[ChipsOffer, list[Defect]]: Предложение и перечень повреждений.
    """
    defects: list[Defect] = []

    offer_href = _attribute(node, _OFFER_HREF, "offer_href")
    offer_id = _query_param(offer_href, _OFFER_ID_PARAM, "offer_id")
    if not offer_id.is_observed and offer_href.is_observed:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="offer_id_missing",
                detail=(
                    f"в ссылке строки нет параметра {_OFFER_ID_PARAM!r}. Это "
                    "единственный носитель идентификатора предложения"
                ),
                row_index=index,
                field_name="offer_id",
            )
        )

    seller_href = _attribute(node.css_first(_SELLER_LINK), _SELLER_HREF, "seller_href")
    if not seller_href.is_observed:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="seller_link_missing",
                detail=(
                    f"у строки нет ссылки на профиль продавца ({_SELLER_LINK}, "
                    f"атрибут {_SELLER_HREF})"
                ),
                row_index=index,
                field_name="seller_href",
            )
        )

    # Количество и цена читаются СОБСТВЕННЫМ текстом: единица измерения и знак
    # валюты лежат вложенными узлами, и общий текст склеил бы их с числом.
    return (
        ChipsOffer(
            offer_id=offer_id,
            offer_href=offer_href,
            seller_href=seller_href,
            seller_name_text=_text(node.css_first(_SELLER_NAME), "seller_name_text"),
            seller_online=_ONLINE in (node.attributes or {}),
            server_text=_text(node.css_first(_SERVER), "server_text"),
            server_id=_attribute(node, _SERVER_ID, "server_id"),
            amount_text=_own_text(node.css_first(_AMOUNT), "amount_text"),
            amount_unit_text=_text(node.css_first(_AMOUNT_UNIT), "amount_unit_text"),
            amount=_digits(node.css_first(_AMOUNT_VALUE), "amount"),
            price_sort=_raw_attribute(node.css_first(_PRICE_SORT), "price_sort"),
            price_text=_own_text(node.css_first(_PRICE), "price_text"),
            currency_symbol_text=_text(node.css_first(_CURRENCY), "currency_symbol_text"),
            row_index=index,
        ),
        defects,
    )


def _digits(node: Node | None, field_name: str) -> Observed[int]:
    """Читает целое из числового носителя строки.

    ОТКАЗЫВАЕТ НА ВСЁМ, ЧТО НЕ ЦИФРЫ. Значение здесь - количество единиц товара,
    и прочитать его наполовину хуже, чем не прочитать: покупатель умножает его
    на цену.

    Аргументы:
        node (Node | None): Узел-носитель либо None.
        field_name (str): Имя поля для причины отсутствия.

    Возвращает:
        Observed[int]: Число либо причина отсутствия.
    """
    if node is None:
        return Observed.missing(f"{field_name}_carrier_absent")
    attributes = node.attributes or {}
    # ПУСТОЙ АТРИБУТ И ОТСУТСТВУЮЩИЙ РАЗЛИЧАЮТСЯ ТОЛЬКО ПРИСУТСТВИЕМ КЛЮЧА:
    # разборщик отдаёт None у обоих. Первое - наблюдение «площадка оставила
    # пусто», второе - «поля нет вовсе».
    if "data-s" not in attributes:
        return Observed.missing(f"{field_name}_attribute_absent")
    raw = attributes["data-s"]
    value = (raw or "").strip()
    if not value:
        return Observed.empty(0)
    if not value.isdigit():
        # Подпись скелета попадает сюда же, и это верно: в снимке значение
        # ЗАМАСКИРОВАНО, и числа там нет. Разбор честно говорит, что не прочитал.
        return Observed.missing(f"{field_name}_not_digits")
    return Observed.present(int(value))


def _raw_attribute(node: Node | None, field_name: str) -> Observed[str]:
    """Читает значение носителя КАК ЕСТЬ, не толкуя его.

    Аргументы:
        node (Node | None): Узел-носитель либо None.
        field_name (str): Имя поля для причины отсутствия.

    Возвращает:
        Observed[str]: Значение либо причина отсутствия.
    """
    if node is None:
        return Observed.missing(f"{field_name}_carrier_absent")
    attributes = node.attributes or {}
    if "data-s" not in attributes:
        return Observed.missing(f"{field_name}_attribute_absent")
    value = (attributes["data-s"] or "").strip()
    return Observed.present(value) if value else Observed.empty("")


def parse_chips(html: str, *, observed_at: datetime) -> ChipsPage:
    """Разбирает публичный список предложений раздела чипов.

    Args:
        html (str): Разметка страницы.
        observed_at (datetime): Момент наблюдения.

    Returns:
        ChipsPage: Разобранная страница.

    Raises:
        ProtocolChangedError: Если ни одной строки не нашлось.
    """
    rows = HTMLParser(html).css(_ROWS)
    if not rows:
        raise ProtocolChangedError(
            f"на странице раздела чипов нет ни одной строки {_ROWS!r}. Пустой "
            "список неотличим от изменившейся разметки, и объявлять рынок пустым "
            "по такому чтению нельзя"
        )

    offers: list[ChipsOffer] = []
    defects: list[Defect] = []
    for index, node in enumerate(rows):
        offer, row_defects = _row(node, index)
        offers.append(offer)
        defects.extend(row_defects)

    complete = not defects
    return ChipsPage(
        completeness=Completeness.COMPLETE if complete else Completeness.PARTIAL,
        reason=None if complete else "rows_damaged",
        observed_at=observed_at,
        rows_total=len(rows),
        rows_accepted=len(offers),
        defects=tuple(defects),
        _offers=tuple(offers),
    )
