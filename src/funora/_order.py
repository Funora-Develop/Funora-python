"""Разбор страницы одного заказа.

Модуль чистый: вход - разметка и момент наблюдения, выход - запись. Разбор
обязан повторяться на сохранённом снимке спустя полгода и давать то же самое.

ГЛАВНОЕ ПРО ЭТУ СТРАНИЦУ, и оно объясняет почти всё остальное. Восемь её
параметров подписаны локализованными метками в узлах без класса, и различить
поля между собой разметка не даёт. Два из восьми совпадают ПОБАЙТОВО вместе с
обёрткой и цепочкой предков - шестьсот двадцать четыре байта против шестисот
двадцати четырёх, - и отличаются только положением.

Отсюда устройство: именованными полями отдаётся то, у чего есть свой якорь -
атрибут, класс, ссылка; остальное отдаётся перечнем «метка и значение» как есть.
Разбор, назвавший восьмой параметр по его месту, соврал бы в тот день, когда
площадка переставит колонки, - и соврал бы молча.

Три решения сверх этого.

Номер заказа берётся из АТРИБУТА виджета отзыва, а не из текста заголовка.
Заголовок его тоже несёт, но голым текстовым узлом до тега переноса: читать
оттуда значило бы резать строку по вёрстке.

Состояние читается по словарю цветовых классов, снятому со списка продаж, и
помечается ВЫВЕДЕННЫМ. Что словарь верен и для этой страницы - рассуждение,
пусть и правдоподобное: класс тот же, оформление у площадки одно. Пометка
уверенности существует ровно для таких случаев.

Отсутствие отзыва читается ПОЛОЖИТЕЛЬНО: атрибут оценки присутствует и пуст.
Это свидетельство, а не неудача поиска, и потому пустой отзыв даёт наблюдение
«пусто», а не «не наблюдали».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Confidence, Observed
from ._result import Defect, Severity
from .errors import ProtocolChangedError
from .extraction import PRESENCE_BY_CLASS, SELECTORS, STATUS_BY_CELL_CLASS, OrderStatus

__all__ = ["parse_review_block", "OrderParam", "OrderView", "parse_order_page"]

_ORDER_NUMBER_CARRIER: Final[str] = SELECTORS["order.identity.order_number"]
_STATUS_CARRIER: Final[str] = SELECTORS["order.identity.status_carrier"]
_REFUND_FORM: Final[str] = SELECTORS["order.identity.refund_available"]
_CATEGORY_ITEM: Final[str] = SELECTORS["order.identity.category_link"]
_AMOUNT_BLOCK: Final[str] = SELECTORS["order.identity.amount_block"]
_COUNTERPARTY_LINK: Final[str] = SELECTORS["order.counterparty.link"]
_COUNTERPARTY_FLAGS: Final[str] = SELECTORS["order.counterparty.account_flags"]
_REVIEW_CONTAINER: Final[str] = SELECTORS["order.review.container"]
_REVIEW_AUTHOR: Final[str] = SELECTORS["order.review.author"]
_CHAT_WIDGET: Final[str] = SELECTORS["order.chat.widget"]
_MESSAGE: Final[str] = SELECTORS["order.chat.messages"]

#: Ссылка на страницу переписки целиком. Читается как второй, независимый
#: от виджета носитель того же диалога: разойдись они - разметка изменилась.
_FULL_CHAT: Final[str] = SELECTORS["order.chat.full_chat_link"]
_PARAM_LIST: Final[str] = SELECTORS["order.params.container"]
_PARAM_ITEM: Final[str] = SELECTORS["order.params.item"]
_PARAM_LABEL: Final[str] = SELECTORS["order.params.label"]

#: Ссылка на раздел внутри параметра. Селектор параметра находит его по НАЛИЧИЮ
#: такой ссылки, а прочесть её надо отдельно.
_CATEGORY_HREF: Final[str] = SELECTORS["order.identity.category_link.attributes.category_href"]

#: Токен блокировки учётной записи.
#:
#: Наблюдён впервые на странице заказа. Словарь присутствия, выведенный по
#: списку продаж, знает два токена - online и offline, - и этого в нём нет.
_BANNED: Final[str] = "banned"

#: Число суммы и знак валюты внутри блока суммы.
#:
#: Адресуются ТЕГАМИ, а не классами. Классы там утилитарные - отступ и
#: начертание, - и редизайн вправе их переставить, ничего не меняя по существу.
#: Теги же несут роли: число в span, знак в strong.
_AMOUNT_NUMBER: Final[str] = "span"
_AMOUNT_SYMBOL: Final[str] = "strong"


@dataclass(frozen=True, slots=True)
class OrderParam:
    """Один параметр заказа, как показала страница.

    Имени у параметра нет нарочно. Различить восемь параметров можно было бы
    только по тексту метки, а текст локализован: правило, читающее «Дата»,
    умрёт на английской локали молча.

    Attributes:
        label_text (str): Метка параметра, как показана.
        value_text (str): Значение параметра, как показано.
    """

    label_text: str
    value_text: str


@dataclass(frozen=True, slots=True)
class OrderView:
    """Заказ в том виде, в каком его отдаёт страница.

    Не Order и намеренно отличается от него типом. Полную запись со страницы
    собрать нельзя: сторон она не разделяет, кода валюты не даёт.

    Attributes:
        order_number (Observed[str]): Номер заказа из атрибута.
        status (Observed[OrderStatus]): Состояние заказа, выведенное по словарю.
        status_class (Observed[str]): Имя класса, из которого прочитано состояние.
        amount_text (Observed[str]): Сумма, как показана.
        currency_symbol_text (Observed[str]): Знак валюты.
        category_href (Observed[str]): Адрес раздела заказа.
        counterparty_name (Observed[str]): Имя контрагента.
        counterparty_href (Observed[str]): Адрес профиля контрагента.
        counterparty_online (Observed[bool]): В сети ли контрагент.
        counterparty_banned (Observed[bool]): Заблокирован ли его аккаунт.
        chat_node_id (Observed[str]): Идентификатор диалога по заказу.
        refund_available (Observed[bool]): Показана ли форма возврата.
        review_rating (Observed[int]): Оценка в отзыве, если он есть.
        review_author_id (Observed[str]): Идентификатор автора отзыва.
        messages_shown (int): Сколько сообщений показала страница.
        observed_at (datetime): Момент наблюдения.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    order_number: Observed[str]
    status: Observed[OrderStatus]
    status_class: Observed[str]
    amount_text: Observed[str]
    currency_symbol_text: Observed[str]
    category_href: Observed[str]
    counterparty_name: Observed[str]
    counterparty_href: Observed[str]
    counterparty_online: Observed[bool]
    counterparty_banned: Observed[bool]
    chat_node_id: Observed[str]
    refund_available: Observed[bool]
    review_rating: Observed[int]
    review_author_id: Observed[str]
    messages_shown: int
    observed_at: datetime
    defects: tuple[Defect, ...] = ()
    _params: tuple[OrderParam, ...] = field(repr=False, default=())

    def params(self) -> tuple[OrderParam, ...]:
        """Возвращает параметры заказа как есть, в порядке появления.

        Признания неполноты здесь не требуется, в отличие от списков. Перечень
        не обещает полноты и обещать её не может: он ровно то, что показала
        страница, и вызывающий это видит.

        Returns:
            tuple[OrderParam, ...]: Пары «метка и значение».
        """
        return self._params


def _attr(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Читает атрибут, различая три исхода.

    Пустое значение атрибута - это ФАКТ О СТРАНИЦЕ, а не о нашем незнании, и
    отдаётся как наблюдение «пусто». На этой странице от различия зависит
    чтение отзыва: пустой атрибут оценки означает «отзыва нет».

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


def _text(node: Node | None, field_name: str) -> Observed[str]:
    """Читает текст узла как наблюдение.

    Args:
        node (Node | None): Узел либо None.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{field_name}")
    value = " ".join((node.text() or "").split())
    return Observed.present(value) if value else Observed.empty("")


def _classes(node: Node | None) -> set[str]:
    """Возвращает имена классов узла множеством.

    Args:
        node (Node | None): Узел либо None.

    Returns:
        set[str]: Имена классов; пустое множество, если узла нет.
    """
    if node is None:
        return set()
    return set(((node.attributes or {}).get("class") or "").split())


def _status(tree: HTMLParser) -> tuple[Observed[OrderStatus], Observed[str], list[Defect]]:
    """Читает состояние заказа из носителя в заголовке.

    Узел адресуется ПОЛОЖЕНИЕМ в заголовке, а не своим классом: класс здесь -
    значение, и селектор по нему находил бы узел лишь пока заказ в этом самом
    состоянии.

    Значение помечается ВЫВЕДЕННЫМ. Словарь цветовых классов снят со списка
    продаж; что он верен и для страницы заказа - рассуждение, пусть и
    правдоподобное. Пометка уверенности существует ровно для таких случаев.

    Args:
        tree (HTMLParser): Разобранная страница.

    Returns:
        tuple[Observed[OrderStatus], Observed[str], list[Defect]]: Состояние,
        имя класса и перечень повреждений.
    """
    node = tree.css_first(_STATUS_CARRIER)
    if node is None:
        return (
            Observed.missing("selector_no_match:status"),
            Observed.missing("selector_no_match:status_class"),
            [
                Defect(
                    severity=Severity.PAGE,
                    code="status_carrier_missing",
                    detail=(
                        f"носителя состояния ({_STATUS_CARRIER}) на странице нет. "
                        "Состояние заказа - то, по чему бот решает выдавать товар"
                    ),
                    field_name="status",
                )
            ],
        )

    names = _classes(node)
    known = sorted(names & set(STATUS_BY_CELL_CLASS))
    raw = Observed.present(" ".join(sorted(names))) if names else Observed.empty("")

    if not known:
        return (
            Observed.missing("status_class_not_in_dictionary"),
            raw,
            [
                Defect(
                    severity=Severity.PAGE,
                    code="status_class_not_in_dictionary",
                    detail=(
                        f"классы носителя {sorted(names)} не входят в наблюдённый "
                        "словарь состояний. Наблюдено одно состояние на одном заказе, "
                        "и незнакомое честнее объявить непрочитанным"
                    ),
                    field_name="status",
                )
            ],
        )

    if len(known) > 1:
        return (
            Observed.missing("status_carriers_disagree"),
            raw,
            [
                Defect(
                    severity=Severity.PAGE,
                    code="status_carriers_disagree",
                    detail=(
                        f"носитель несёт сразу {known}: состояний два, а заказ один. "
                        "Взять любое значило бы выбрать наугад"
                    ),
                    field_name="status",
                )
            ],
        )

    return Observed.present(STATUS_BY_CELL_CLASS[known[0]], Confidence.INFERRED), raw, []


def _amount(tree: HTMLParser) -> tuple[Observed[str], Observed[str]]:
    """Разделяет сумму и знак валюты.

    Селектор берёт блок целиком - число и знак вместе, - а делит их код.
    Селектором это делалось бы через утилитарные классы отступа и начертания,
    которые редизайн вправе переставить, ничего не меняя по существу.

    Args:
        tree (HTMLParser): Разобранная страница.

    Returns:
        tuple[Observed[str], Observed[str]]: Сумма и знак валюты.
    """
    block = tree.css_first(_AMOUNT_BLOCK)
    if block is None:
        return (
            Observed.missing("selector_no_match:amount"),
            Observed.missing("selector_no_match:currency_symbol"),
        )

    value = tree.css_first(f"{_AMOUNT_BLOCK} > div")
    if value is None:
        return (
            Observed.missing("amount_block_has_no_value"),
            Observed.missing("amount_block_has_no_value"),
        )

    # Делится ПО УЗЛАМ, а не разбором текста. Первая редакция резала текст блока
    # образцом «цифры, потом остальное» - и не работала на снимке вовсе: скелет
    # заменяет значение подписью, и текст начинается с буквы. Разбор, который
    # нельзя проверить на фикстуре, не проверен ничем.
    numbers = value.css(_AMOUNT_NUMBER)
    symbols = value.css(_AMOUNT_SYMBOL)
    if len(numbers) != 1 or len(symbols) != 1:
        return (
            Observed.missing("amount_block_shape_changed"),
            Observed.missing("amount_block_shape_changed"),
        )

    return _text(numbers[0], "amount"), _text(symbols[0], "currency_symbol")


def _flag(names: set[str], token: str, dictionary: dict[str, bool] | None = None) -> Observed[bool]:
    """Читает признак по наблюдённому словарю классов, а не по отрицанию.

    Правило «нет offline, значит online» запрещено: оно выглядело бы работающим
    ровно до переименования класса, а неузнанный класс честнее объявить
    ненаблюдённым.

    Args:
        names (set[str]): Имена классов узла.
        token (str): Токен, наличие которого читается, если словарь не задан.
        dictionary (dict[str, bool] | None): Словарь «класс - значение».

    Returns:
        Observed[bool]: Наблюдение.
    """
    if not names:
        return Observed.missing("selector_no_match:account_flags")
    if dictionary is None:
        return Observed.present(token in names)
    hits = sorted(names & set(dictionary))
    if not hits:
        return Observed.missing("class_not_in_dictionary")
    if len(hits) > 1:
        return Observed.missing("classes_disagree")
    return Observed.present(dictionary[hits[0]])


def _review(tree: HTMLParser) -> tuple[Observed[int], Observed[str], list[Defect]]:
    """Читает оценку и автора отзыва по заказу.

    ОТСУТСТВИЕ ОТЗЫВА ЧИТАЕТСЯ ПОЛОЖИТЕЛЬНО: атрибут оценки присутствует и
    пуст. Это свидетельство, а не неудача поиска, и потому пустой отзыв даёт
    наблюдение «пусто», а не «не наблюдали».

    Args:
        tree (HTMLParser): Разобранная страница.

    Returns:
        tuple[Observed[int], Observed[str], list[Defect]]: Оценка, автор,
        перечень повреждений.
    """
    author = _attr(tree.css_first(_REVIEW_AUTHOR), "data-author", "review_author_id")
    raw = _attr(tree.css_first(_REVIEW_CONTAINER), "data-rating", "review_rating")

    if not raw.is_observed:
        return Observed.missing(raw.reason or "review_rating_unreadable"), author, []

    value = raw.value
    if not value:
        # Атрибут есть и пуст - отзыва нет. Наблюдение, а не незнание.
        return Observed.empty(0), author, []

    if not value.isdigit():
        return (
            Observed.missing("review_rating_not_a_number"),
            author,
            [
                Defect(
                    severity=Severity.PAGE,
                    code="review_rating_not_a_number",
                    detail=f"оценка отзыва пришла как {value!r}, а не числом",
                    field_name="review_rating",
                )
            ],
        )

    return Observed.present(int(value)), author, []


def parse_review_block(html: str) -> tuple[Observed[int], Observed[str], tuple[Defect, ...]]:
    """Читает отзыв из КУСКА разметки, а не из целой страницы.

    ЗАЧЕМ ОТДЕЛЬНЫЙ ВХОД. Ответ площадки на написание отзыва несёт
    перерисованный виджет - тот же самый, что лежит на странице заказа.
    Разобрать его вторым разбором значило бы завести две дороги к одной
    разметке, и разошлись бы они молча: селектор поправят в одной.

    Аргументы:
        html (str): Кусок разметки с виджетом отзыва.

    Возвращает:
        tuple[Observed[int], Observed[str], tuple[Defect, ...]]: Оценка, автор,
        перечень повреждений.
    """
    rating, author, defects = _review(HTMLParser(html))
    return rating, author, tuple(defects)


def parse_order_page(html: str, observed_at: datetime) -> OrderView:
    """Разбирает страницу одного заказа.

    Args:
        html (str): Тело страницы. Предполагается уже классифицированным как
            пригодное: состояние сессии здесь не проверяется.
        observed_at (datetime): Момент наблюдения. Передаётся снаружи, чтобы
            разбор оставался чистым и повторяемым на сохранённом снимке.

    Returns:
        OrderView: Заказ в том виде, в каком его отдала страница.

    Raises:
        ProtocolChangedError: Если перечня параметров на странице нет вовсе -
            без него читать нечего, и пустую запись возвращать нельзя: она
            неотличима от заказа без параметров.
    """
    tree = HTMLParser(html)
    defects: list[Defect] = []

    if tree.css_first(_PARAM_LIST) is None:
        raise ProtocolChangedError(
            f"на странице заказа нет перечня параметров ({_PARAM_LIST}). "
            "Пустую запись вернуть нельзя: она неотличима от заказа без параметров"
        )

    status, status_class, status_defects = _status(tree)
    defects.extend(status_defects)

    amount, symbol = _amount(tree)
    review_rating, review_author, review_defects = _review(tree)
    defects.extend(review_defects)

    flags = _classes(tree.css_first(_COUNTERPARTY_FLAGS))
    widget = tree.css_first(_CHAT_WIDGET)

    # Ссылка «открыть переписку целиком» - второй носитель того же диалога.
    # Пропади она при живом виджете, и наоборот - разметка изменилась, и молча
    # отдать идентификатор было бы хуже, чем сказать об этом.
    if (tree.css_first(_FULL_CHAT) is None) != (widget is None):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="chat_carriers_disagree",
                detail=(
                    f"виджет переписки ({_CHAT_WIDGET}) и ссылка на неё "
                    f"({_FULL_CHAT}) разошлись: один есть, другого нет"
                ),
                field_name="chat_node_id",
            )
        )

    # Ссылка на раздел ищется по всему перечню, а не внутри найденного
    # параметра: параметр и опознаётся-то по наличию такой ссылки, и второе
    # совпадение означало бы, что признак перестал различать.
    if tree.css_first(_CATEGORY_ITEM) is None:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="category_item_missing",
                detail=(f"параметра со ссылкой на раздел ({_CATEGORY_ITEM}) на странице нет"),
                field_name="category_href",
            )
        )

    category_links = tree.css(_CATEGORY_HREF)
    if len(category_links) > 1:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="category_link_ambiguous",
                detail=(
                    f"ссылок на раздел среди параметров {len(category_links)}, а признак "
                    "рассчитан на одну. Взять первую значило бы выбрать наугад"
                ),
                field_name="category_href",
            )
        )
        category = Observed[str].missing("category_link_ambiguous")
    else:
        category = _attr(category_links[0] if category_links else None, "href", "category_href")

    params: list[OrderParam] = []
    for item in tree.css(_PARAM_ITEM):
        label = item.css_first("h5")
        if label is None:
            continue
        label_text = " ".join((label.text() or "").split())
        # Текст значения берётся у ПАРАМЕТРА без метки, а не у первого вложенного
        # блока: у восьмого параметра внутри лежит окно возврата со своими
        # полями, и обход по вложенным утащил бы их в значение.
        whole = " ".join((item.text() or "").split())
        value_text = whole[len(label_text) :].strip() if whole.startswith(label_text) else whole
        params.append(OrderParam(label_text=label_text, value_text=value_text))

    if len(params) != len(tree.css(_PARAM_LABEL)):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="param_label_missing",
                detail=(
                    f"параметров с меткой {len(params)}, а меток на странице "
                    f"{len(tree.css(_PARAM_LABEL))}: часть параметров осталась без подписи"
                ),
            )
        )

    return OrderView(
        order_number=_attr(tree.css_first(_ORDER_NUMBER_CARRIER), "data-order", "order_number"),
        status=status,
        status_class=status_class,
        amount_text=amount,
        currency_symbol_text=symbol,
        category_href=category,
        counterparty_name=_text(tree.css_first(_COUNTERPARTY_LINK), "counterparty_name"),
        counterparty_href=_attr(tree.css_first(_COUNTERPARTY_LINK), "href", "counterparty_href"),
        counterparty_online=_flag(flags, "online", PRESENCE_BY_CLASS),
        counterparty_banned=_flag(flags, _BANNED),
        chat_node_id=_attr(widget, "data-id", "chat_node_id"),
        refund_available=Observed.present(tree.css_first(_REFUND_FORM) is not None),
        review_rating=review_rating,
        review_author_id=review_author,
        messages_shown=len(tree.css(_MESSAGE)),
        observed_at=observed_at,
        defects=tuple(defects),
        _params=tuple(params),
    )
