"""Сборка обращения к каналу обновлений.

Канал - это POST /runner/ с формой из трёх полей: защитного токена, подписки и
запроса. Тем же обращением площадка и опрашивается, и меняется: при опросе поле
request не несёт действия, при отправке в нём лежит объект с именем действия.

ЗДЕСЬ ТОЛЬКО ЧТЕНИЕ СТРАНИЦЫ, без сети и без сборки самого действия. Модуль
отвечает на один вопрос: что нужно взять со страницы, чтобы обращение вообще
можно было составить.

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ МОДУЛЬ. Всё нужное лежит на одной странице, но в шести
разных местах - в объекте настроек, в пяти атрибутах виджета и в атрибуте
активной строки списка. Собранное вместе, оно проверяется на снимке целиком;
разбросанное по операциям, оно проверялось бы по частям, и первая же операция
записи собрала бы запрос из того, чего на странице не оказалось.

ОТКУДА ЧТО БЕРЁТСЯ - НАБЛЮДЕНИЕ, А НЕ ДОГАДКА. Сборщик наблюдений сверяет
значения полей запроса со значениями атрибутов страницы в живой вкладке и
записывает имя совпавшего атрибута. Иначе установить это было нельзя: скелет
страницы и сетевая запись маскируют значения независимо, и одинаковые подписи
совпадают у любых двух значений той же длины и состава.

СТРАНИЦА НУЖНА С ОТКРЫТЫМ ДИАЛОГОМ. На списке диалогов у виджета класс
chat-not-selected, и из пяти атрибутов остаются два: ни имени диалога, ни его
метки там нет. Отправить, прочитав только список, нельзя, и молчать об этом
нельзя тоже - вызывающий принял бы пробел за отсутствие диалога.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from ._secret import Secret
from ._thread import Origin, Thread
from ._whoami import parse_app_data
from .extraction import ATTRIBUTES, SELECTORS
from .reconciliation import ReconcileVerdict
from .send_outcome import SendOutcome

__all__ = [
    "Anchor",
    "Reconciliation",
    "RunnerContext",
    "SendResult",
    "classify_send_response",
    "parse_runner_context",
    "reconcile",
    "take_anchor",
]

#: Имя вида объекта, которым канал отвечает об изменении в диалоге.
#:
#: Записано ДОСЛОВНО: сборщик наблюдений хранит значения полей type без
#: маскирования, потому что это протокольные знаки, а не то, что написал
#: человек.
_CHAT_NODE_TYPE: Final[str] = "chat_node"

#: Узел виджета переписки. Он же носитель имени диалога и меток подписки.
_WIDGET: Final[str] = SELECTORS["order.chat.widget"]

#: Строка списка диалогов.
_CONTACT: Final[str] = SELECTORS["chats.contact_list.item"]

#: Скрытый узел с меткой подписки на счётчики продаж.
_ORDERS_TAG_CARRIER: Final[str] = SELECTORS["updates.tags.carrier"]

#: Собственный идентификатор.
_OWN_ID: Final[str] = SELECTORS["session.identity.own_user_id"]

_NODE_NAME: Final[str] = ATTRIBUTES["order.chat.widget.attributes.node_name"]
_NODE_ID: Final[str] = ATTRIBUTES["order.chat.widget.attributes.node_id"]
_CHAT_TAG: Final[str] = ATTRIBUTES["order.chat.widget.attributes.tag"]
_BOOKMARKS_TAG: Final[str] = ATTRIBUTES["order.chat.widget.attributes.bookmarks_tag"]
_LAST_MESSAGE: Final[str] = ATTRIBUTES["chats.contact_list.attributes.last_message_position"]
_CONTACT_ID: Final[str] = ATTRIBUTES["chats.contact_list.attributes.node_id"]
_ORDERS_TAG: Final[str] = ATTRIBUTES["updates.tags.carrier.attributes.orders_tag"]
_MESSAGE_ITEM: Final[str] = SELECTORS["chats.message.item"]
_MESSAGE_ID: Final[str] = ATTRIBUTES["order.chat.messages.attributes.message_id"]
_OWN_PROFILE_LINK: Final[str] = SELECTORS["session.identity.own_profile_link"]


@dataclass(frozen=True, slots=True)
class RunnerContext:
    """Всё, что нужно со страницы для обращения к каналу.

    Attributes:
        csrf_token (Secret | None): Защитный токен. Обёрнут нарочно: значение
            видно только по явному вызову reveal и в вывод не попадает.
        node_name (Observed[str]): Составное имя диалога. Идёт в поле node.
        node_id (Observed[str]): Числовой идентификатор диалога.
        chat_tag (Observed[str]): Метка подписки на диалог.
        bookmarks_tag (Observed[str]): Метка подписки на закладки.
        orders_tag (Observed[str]): Метка подписки на счётчики продаж.
        own_user_id (Observed[str]): Собственный идентификатор.
        last_message (Observed[str]): Позиция последнего сообщения диалога.
        can_send (bool): Годится ли страница для отправки.
        defects (tuple[Defect, ...]): Замеченные повреждения.
    """

    csrf_token: Secret | None
    node_name: Observed[str]
    node_id: Observed[str]
    chat_tag: Observed[str]
    bookmarks_tag: Observed[str]
    orders_tag: Observed[str]
    own_user_id: Observed[str]
    last_message: Observed[str]
    can_send: bool
    defects: tuple[Defect, ...] = ()


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


def _active_contact(tree: HTMLParser, widget: Node | None) -> tuple[Node | None, list[Defect]]:
    """Находит строку списка, отвечающую открытому диалогу.

    Ищется НЕ по классу active, а по совпадению идентификатора со строкой
    виджета. Класс - оформление, и опираться на него значило бы читать чужое
    решение о подсветке; идентификатор же говорит о том, какой диалог открыт, по
    существу.

    Класс всё же используется - запасным путём, когда виджет идентификатора не
    несёт. Порядок именно такой: сперва равенство значений, потом признак вида.

    Args:
        tree (HTMLParser): Разобранная страница.
        widget (Node | None): Узел виджета либо None.

    Returns:
        tuple[Node | None, list[Defect]]: Строка и перечень повреждений.
    """
    rows = tree.css(_CONTACT)
    if not rows:
        return None, [
            Defect(
                severity=Severity.PAGE,
                code="contact_list_missing",
                detail=f"на странице нет строк списка диалогов ({_CONTACT})",
                field_name="last_message",
            )
        ]

    wanted = ((widget.attributes or {}).get(_NODE_ID) or "").strip() if widget is not None else ""
    if wanted:
        matched = [
            one for one in rows if ((one.attributes or {}).get(_CONTACT_ID) or "").strip() == wanted
        ]
        if len(matched) == 1:
            return matched[0], []
        if len(matched) > 1:
            return None, [
                Defect(
                    severity=Severity.PAGE,
                    code="contact_rows_ambiguous",
                    detail=(
                        f"строк с идентификатором открытого диалога найдено {len(matched)}. "
                        "Взять любую значило бы выбрать наугад"
                    ),
                    field_name="last_message",
                )
            ]

    marked = [
        one for one in rows if "active" in ((one.attributes or {}).get("class") or "").split()
    ]
    if len(marked) == 1:
        return marked[0], []

    return None, [
        Defect(
            severity=Severity.PAGE,
            code="active_contact_not_found",
            detail=(
                f"строка открытого диалога не нашлась: по идентификатору совпадений нет, "
                f"по признаку оформления найдено {len(marked)}"
            ),
            field_name="last_message",
        )
    ]


def parse_runner_context(html: str) -> RunnerContext:
    """Собирает со страницы всё нужное для обращения к каналу.

    НЕ ПАДАЕТ НИ НА ЧЁМ. Страница может не годиться для отправки - это ответ, а
    не происшествие, и вызывающему он нужен целиком: чего именно не хватило,
    решает, перечитывать ли другую страницу или разбираться с разметкой.

    Args:
        html (str): Тело страницы.

    Returns:
        RunnerContext: Прочитанное и признак пригодности страницы.
    """
    tree = HTMLParser(html)
    widget = tree.css_first(_WIDGET)
    settings = parse_app_data(html)

    defects: list[Defect] = list(settings.defects)

    if widget is None:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="chat_widget_missing",
                detail=f"на странице нет виджета переписки ({_WIDGET})",
                field_name="node_name",
            )
        )

    node_name = _attribute(widget, _NODE_NAME, "node_name")
    node_id = _attribute(widget, _NODE_ID, "node_id")
    chat_tag = _attribute(widget, _CHAT_TAG, "chat_tag")

    if widget is not None and not node_name.is_observed:
        # Признак объявляется ПОЛОЖИТЕЛЬНЫЙ - отсутствие имени диалога, - а не
        # наличие класса chat-not-selected: класс волен смениться, а без
        # значения запрос всё равно не собрать.
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="chat_not_selected",
                detail=(
                    f"у виджета нет имени диалога ({_NODE_NAME}). Так выглядит список "
                    "диалогов без открытого собеседника: отправить с него нельзя"
                ),
                field_name="node_name",
            )
        )

    row, row_defects = _active_contact(tree, widget)
    defects += row_defects

    return RunnerContext(
        csrf_token=settings.csrf_token,
        node_name=node_name,
        node_id=node_id,
        chat_tag=chat_tag,
        bookmarks_tag=_attribute(widget, _BOOKMARKS_TAG, "bookmarks_tag"),
        orders_tag=_attribute(tree.css_first(_ORDERS_TAG_CARRIER), _ORDERS_TAG, "orders_tag"),
        own_user_id=_attribute(tree.css_first(_OWN_ID), "data-user", "own_user_id"),
        last_message=_attribute(row, _LAST_MESSAGE, "last_message"),
        can_send=(
            settings.csrf_token is not None
            and node_name.is_observed
            and chat_tag.is_observed
            and _attribute(row, _LAST_MESSAGE, "last_message").is_observed
        ),
        defects=tuple(defects),
    )


@dataclass(frozen=True, slots=True)
class SendResult:
    """Чем окончилось обращение к каналу с действием.

    НЕ СООБЩЕНИЕ, А КВИТАНЦИЯ. Текста здесь нет и быть не может: в ответе лежит
    готовая разметка, значения которой не наблюдают намеренно - это чужая
    переписка. Подставить эхо своего ввода не то же самое: что площадка
    сохранила, неизвестно.

    Attributes:
        outcome (SendOutcome): Исход. Три значения, и третье - честное незнание.
        reason (str): Машиночитаемая причина решения из закрытого перечня.
        channel_message_id (Observed[int]): Идентификатор сообщения В ОТВЕТЕ
            КАНАЛА. Имя нарочно не message_id: в разметке идентификатор - строка,
            в канале число, и что это одно значение, не наблюдалось.
        node (Observed[str]): Имя диалога, подтверждённое площадкой.
        messages_in_answer (int): Сколько сообщений пришло в ответе.
    """

    outcome: SendOutcome
    reason: str
    channel_message_id: Observed[int]
    node: Observed[str]
    messages_in_answer: int

    @property
    def is_confirmed(self) -> bool:
        """Говорит, подтвердила ли площадка отправку.

        Свойство именованное, а не приведение к булеву: у квитанции три исхода,
        и молчаливое `if result` читало бы unconfirmed как успех - ровно ту
        ошибку, ради которой третий исход и заведён.

        Returns:
            bool: True только при исходе confirmed.
        """
        return self.outcome is SendOutcome.CONFIRMED


def _unconfirmed(reason: str) -> SendResult:
    """Собирает квитанцию без подтверждения.

    Args:
        reason (str): Машиночитаемая причина.

    Returns:
        SendResult: Квитанция с исходом unconfirmed.
    """
    return SendResult(
        outcome=SendOutcome.UNCONFIRMED,
        reason=reason,
        channel_message_id=Observed.missing(reason),
        node=Observed.missing(reason),
        messages_in_answer=0,
    )


def classify_send_response(body: str, *, sent_to: str) -> SendResult:
    """Устанавливает исход отправки по ответу канала.

    ПОРЯДОК ШАГОВ НОРМАТИВЕН и объявлен в spec/protocol/send-outcome.yaml. Две
    реализации, проверившие условия в разном порядке, разойдутся ровно на том
    ответе, ради которого правило написано.

    Подтверждение объявляется по ПОЛОЖИТЕЛЬНОМУ признаку - площадка вернула
    сообщение в том самом диалоге, - а не по отсутствию отказа. Отсутствие
    отказа означало бы «отправлено» о всяком ответе с кодом 200.

    Args:
        body (str): Тело ответа канала.
        sent_to (str): Имя диалога, в который отправляли. Сверяется с тем, что
            вернула площадка: канал отвечает и о чужих диалогах, потому что
            подписка едет в каждом запросе.

    Returns:
        SendResult: Исход, причина и прочитанное из ответа.
    """
    # Шаг 1. Тело разбирается как JSON.
    try:
        parsed: object = json.loads(body)
    except ValueError:
        return _unconfirmed("body_not_json")

    # Шаг 2. Разобранное - объект.
    if not isinstance(parsed, dict):
        return _unconfirmed("body_not_an_object")

    # Шаг 3. Поле response - объект.
    #
    # При запросе БЕЗ действия оно приходит булевым, и это наблюдено. Булево
    # здесь означает, что ответ пришёл на опрос, а не на действие: подтверждать
    # им отправку нечем.
    answer = parsed.get("response")
    if not isinstance(answer, dict):
        return _unconfirmed("response_not_an_object")

    # Шаг 4. Поле error пусто.
    #
    # Формы отказа никто не видел, и она здесь не нужна: довольно предиката.
    if answer.get("error") is not None:
        return SendResult(
            outcome=SendOutcome.REFUSED,
            reason="channel_reported_error",
            channel_message_id=Observed.missing("channel_reported_error"),
            node=Observed.missing("channel_reported_error"),
            messages_in_answer=0,
        )

    # Шаг 5. Среди объектов есть узел диалога.
    objects = parsed.get("objects")
    nodes = [
        one
        for one in (objects if isinstance(objects, list) else [])
        if isinstance(one, dict) and one.get("type") == _CHAT_NODE_TYPE
    ]
    if not nodes:
        return _unconfirmed("no_chat_node_in_answer")

    # Шаг 6. Узел диалога - тот самый.
    mine = None
    for one in nodes:
        data = one.get("data")
        if not isinstance(data, dict):
            continue
        node = data.get("node")
        name = node.get("name") if isinstance(node, dict) else None
        if isinstance(name, str) and name == sent_to:
            mine = one
            break
    if mine is None:
        return _unconfirmed("node_mismatch")

    # Шаг 7. Список сообщений непуст.
    data = mine.get("data")
    messages = data.get("messages") if isinstance(data, dict) else None
    written = [
        one for one in (messages if isinstance(messages, list) else []) if isinstance(one, dict)
    ]
    if not written:
        return _unconfirmed("empty_message_list")

    # Шаг 8. Подтверждено.
    last = written[-1].get("id")
    return SendResult(
        outcome=SendOutcome.CONFIRMED,
        reason="confirmed_by_channel",
        channel_message_id=(
            Observed.present(last)
            if isinstance(last, int) and not isinstance(last, bool)
            else Observed.missing("channel_message_id_not_a_number")
        ),
        node=Observed.present(sent_to),
        messages_in_answer=len(written),
    )


@dataclass(frozen=True, slots=True)
class Anchor:
    """Опора сверки, снятая ДО отправки.

    Берётся из предполётного чтения страницы диалога - того самого, из которого
    отправка и так берёт токен, имя диалога и позицию. Лишнего запроса нет.

    Attributes:
        own_href (str): Адрес собственного профиля. Пустая строка означает, что
            узла на странице не нашлось.
        known_ids (frozenset[str]): Идентификаторы сообщений, бывших ДО отправки.
        messages_seen (int): Сколько узлов сообщений насчитало предполётное
            чтение. Отличает ПУСТОЙ диалог от непрочитанной опоры: в первом
            случае всякое найденное потом своё сообщение вправду новое, во
            втором - неизвестно ничего.
    """

    own_href: str
    known_ids: frozenset[str]
    messages_seen: int


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Чем окончилась сверка.

    Attributes:
        verdict (ReconcileVerdict): Вердикт. Вердикта отрицания среди них НЕТ.
        reason (str): Машиночитаемая причина.
        found_id (Observed[str]): Идентификатор найденного своего сообщения.
        own_messages_seen (int): Сколько собственных сообщений нашлось всего.
            Ноль означает, что признак опознания себя не показал.
    """

    verdict: ReconcileVerdict
    reason: str
    found_id: Observed[str]
    own_messages_seen: int


def _normalized(href: str) -> str:
    """Приводит адрес профиля к сравнимому виду.

    Площадка отдаёт адреса и с завершающей косой чертой, и без неё. Сравнение
    как есть объявило бы разными один и тот же профиль.

    Args:
        href (str): Адрес.

    Returns:
        str: Адрес без завершающей косой черты и краевых пробелов.
    """
    return href.strip().rstrip("/")


def take_anchor(html: str) -> Anchor:
    """Снимает опору сверки со страницы ДО отправки.

    Args:
        html (str): Тело страницы диалога.

    Returns:
        Anchor: Адрес собственного профиля и множество прежних идентификаторов.
    """
    tree = HTMLParser(html)

    # Узлов адреса ДВА - настольное меню и мобильное, - и значение в них одно и
    # то же. Взять первый попавшийся значило бы повторить ошибку, на которой уже
    # спотыкались разбор списка продаж, разбор отзывов и чтение своего имени.
    #
    # Здесь строже, чем у имени: по адресу опознаётся СВОЁ сообщение, и ошибка
    # оканчивается вторым сообщением покупателю. Разошлись - адреса нет вовсе, и
    # сверка ответа не даст.
    links = {
        _normalized((one.attributes or {}).get("href") or "") for one in tree.css(_OWN_PROFILE_LINK)
    }
    links.discard("")
    own_href = links.pop() if len(links) == 1 else ""

    nodes = tree.css(_MESSAGE_ITEM)
    known: set[str] = set()
    for node in nodes:
        value = ((node.attributes or {}).get(_MESSAGE_ID) or "").strip()
        if value:
            known.add(value)
    return Anchor(
        own_href=own_href,
        known_ids=frozenset(known),
        messages_seen=len(nodes),
    )


def reconcile(thread: Thread, anchor: Anchor) -> Reconciliation:
    """Ищет своё новое сообщение в перечитанной истории.

    ПОРЯДОК ШАГОВ НОРМАТИВЕН и объявлен в spec/protocol/reconciliation.yaml.

    НИЧЕГО НЕ ОТПРАВЛЯЕТ. Сверка отвечает на вопрос «что вышло из отправки» и
    ничего не предпринимает: решение о повторной отправке принимает вызывающий.

    ВЕРДИКТА ОТРИЦАНИЯ НЕТ. Отсутствие сообщения в истории - свидетельство
    отрицательное, и объявлять по нему «не отправлено» значило бы подтолкнуть
    вызывающего написать второй раз.

    Args:
        thread (Thread): Перечитанная история переписки.
        anchor (Anchor): Опора, снятая до отправки.

    Returns:
        Reconciliation: Вердикт, причина и найденное.
    """
    # Шаг 3. Полнота чтения. Половина истории, принятая за целую, объявила бы
    # неотправленным отправленное.
    if thread.completeness is not Completeness.COMPLETE:
        return Reconciliation(
            verdict=ReconcileVerdict.UNDETERMINED,
            reason="incomplete_read",
            found_id=Observed.missing("incomplete_read"),
            own_messages_seen=0,
        )

    # Отбор ТОЛЬКО человеческих сообщений - второй замок, и он назван вторым
    # нарочно. Первый стоит в разборе: сообщение с расходящимися признаками -
    # оповещение площадки, несущее ссылку на профиль, - получает неизвестное
    # происхождение, и чтение объявляется неполным. Сюда такое чтение не дойдёт.
    #
    # Замок оставлен потому, что правило разбора о полноте волен изменить кто
    # угодно, а цена ошибки здесь - второе сообщение покупателю.
    mine = [
        one
        for one in thread.messages()
        if one.origin is Origin.HUMAN
        and one.author_href.is_observed
        and _normalized(one.author_href.value) == anchor.own_href
        and anchor.own_href
    ]

    # Шаг 4. Признак себя показал себя. Без этого условия отсутствие искомого
    # означало бы что угодно - и что его нет, и что опознание не работает.
    if not mine:
        return Reconciliation(
            verdict=ReconcileVerdict.UNDETERMINED,
            reason="self_marker_not_demonstrated",
            found_id=Observed.missing("self_marker_not_demonstrated"),
            own_messages_seen=0,
        )

    # Опора обязана быть прочитанной. Пустое множество при непустой странице
    # означает, что идентификаторы не прочитались, - и тогда СТАРОЕ своё
    # сообщение выглядит новым, а вызывающий узнаёт «доставлено» о том, что не
    # уходило. Пустой диалог - другое дело: там всякое найденное вправду новое.
    if not anchor.known_ids and anchor.messages_seen > 0:
        return Reconciliation(
            verdict=ReconcileVerdict.UNDETERMINED,
            reason="anchor_not_read",
            found_id=Observed.missing("anchor_not_read"),
            own_messages_seen=len(mine),
        )

    # Шаг 5. Своё НОВОЕ сообщение.
    fresh = [
        one
        for one in mine
        if one.message_id.is_observed and one.message_id.value not in anchor.known_ids
    ]
    if fresh:
        return Reconciliation(
            verdict=ReconcileVerdict.DELIVERED,
            reason="found_in_history",
            found_id=Observed.present(fresh[-1].message_id.value),
            own_messages_seen=len(mine),
        )

    # Шаг 6. Не найдено при исправном признаке.
    return Reconciliation(
        verdict=ReconcileVerdict.ABSENT_FROM_HISTORY,
        reason="not_in_history",
        found_id=Observed.missing("not_in_history"),
        own_messages_seen=len(mine),
    )
