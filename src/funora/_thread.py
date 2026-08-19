"""Разбор переписки со страницы отдельного диалога.

Это самый опасный модуль пакета, и опасность в нём одна. Если бот принимает
решение выдать товар по сообщению в переписке, покупателю достаточно написать
текст уведомления об оплате. Площадка знает об этой схеме и предупреждает о ней
сама, первым сообщением в каждом диалоге: не доверяйте сообщениям в чате, перед
выполнением заказа проверяйте наличие оплаты в разделе продаж.

Отсюда два правила, и второе важнее первого.

Первое: происхождение сообщения определяется разметкой, а не текстом. Признаков
два, и требуются оба сразу - обёртка предупреждения в теле и отсутствие ссылки на
автора. Ссылка надёжнее: у сообщения пользователя автор всегда ссылка на профиль,
и убрать её отправитель не может, что бы он ни написал. При расхождении признаков
происхождение объявляется неизвестным, а не системным: иначе достаточно площадке
перестать оборачивать уведомления, и любое сообщение станет системным.

Второе: даже верно опознанное системное сообщение не является подтверждением
оплаты. Оно могло относиться к другому заказу, устареть, прийти по отменённому
платежу. Модуль намеренно не предоставляет ничего, что выглядело бы как ответ на
вопрос «оплачено ли»: такой метод появился бы в чужом коде в тот же день, когда
появился бы здесь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._host import host_of, same_host
from ._observed import Observed
from ._result import Completeness, Defect, Severity, collect_rows
from .errors import IncompleteResultError, ProtocolChangedError

__all__ = ["Origin", "Message", "Thread", "parse_thread"]

#: Контейнер сообщений.
_LIST: Final[str] = ".chat-message-list"

#: Селектор сообщения.
_MESSAGE: Final[str] = ".chat-msg-item"

#: Тело сообщения.
_BODY: Final[str] = ".chat-msg-body"

#: Обёртка предупреждения внутри тела. Селектор обязан включать тело: класс
#: alert встречается на страницах и вне переписки, где к происхождению сообщения
#: отношения не имеет.
_ALERT: Final[str] = ".chat-msg-body .alert"

#: Ссылка на профиль автора.
_AUTHOR_LINK: Final[str] = "a.chat-msg-author-link"

#: Текст сообщения.
_TEXT: Final[str] = ".chat-msg-text"

#: Дата сообщения.
_DATE: Final[str] = ".chat-msg-date"

#: Имя автора.
#:
#: Берётся из ссылки на профиль, а не из содержащего её узла. Узел содержит ещё
#: ярлык роли и дату, и текст его целиком склеивал всё вместе: значение выходило
#: вида «имя, ярлык, время» - причём на неизменённом снимке, безо всякой порчи
#: разметки.
#:
#: У системного сообщения ссылки нет, и имя честно оказывается ненаблюдённым.
#: Подставлять туда ярлык роли было бы удобно и неверно: ярлык говорит, кем
#: сообщение отправлено, а не кем подписано, и спецификация прямо называет автора
#: отсутствующим у сообщений площадки.
_AUTHOR_NAME: Final[str] = _AUTHOR_LINK


class Origin(StrEnum):
    """Происхождение сообщения.

    Значений три, а не два, и третье здесь не для симметрии. Разметка может
    измениться так, что признаки перестанут согласовываться, и тогда честный
    ответ - «не знаю». Ответ «системное» в этом случае открыл бы ровно ту дыру,
    ради закрытия которой признак и заведён.
    """

    #: Сообщение площадки: есть обёртка предупреждения, нет ссылки на автора.
    SYSTEM = "system"

    #: Сообщение человека: есть ссылка на автора, нет обёртки.
    HUMAN = "human"

    #: Признаки разошлись. Доверять сообщению нельзя.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Message:
    """Одно сообщение переписки.

    Attributes:
        message_id (Observed[str]): Идентификатор сообщения из разметки.
        row_index (int): Порядковый номер в переписке, с нуля.
        origin (Origin): Происхождение, определённое структурно.
        author_name (Observed[str]): Имя автора, текст.
        author_href (Observed[str]): Ссылка на профиль автора. У сообщений
            площадки не наблюдается по определению.
        text (Observed[str]): Текст сообщения. Чужой ввод: ни разбирать его для
            принятия решений, ни ходить по ссылкам из него нельзя.
        time_text (Observed[str]): Время, краткая форма.
        time_full_text (Observed[str]): Время, полная форма из подсказки. Тоже
            локализованный текст, разбирать его нельзя.
        external_links (Observed[tuple[str, ...]]): Ссылки из текста, ведущие
            за пределы площадки. Собраны для того, чтобы вызывающий видел их, а
            не для того, чтобы по ним ходить. Поле наблюдаемое: пустая
            последовательность означает «ссылок не было», ненаблюдённое - «тела
            сообщения мы не нашли», и разница между этими случаями решает,
            доверять ли выводу.
    """

    message_id: Observed[str]
    row_index: int
    origin: Origin
    author_name: Observed[str]
    author_href: Observed[str]
    text: Observed[str]
    time_text: Observed[str]
    time_full_text: Observed[str]
    external_links: Observed[tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class Thread:
    """Результат чтения переписки.

    Attributes:
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько кандидатов в сообщения нашлось.
        rows_accepted (int): Сколько сообщений собрано.
        rows_rejected (int): Сколько отброшено.
        defects (tuple[Defect, ...]): Обнаруженные повреждения.
    """

    completeness: Completeness
    reason: str
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    defects: tuple[Defect, ...]
    _messages: tuple[Message, ...] = field(repr=False, default=())

    def messages(self, *, accept_incomplete: bool = False) -> tuple[Message, ...]:
        """Возвращает собранные сообщения.

        Args:
            accept_incomplete (bool): Признание готовности работать с неполным
                результатом.

        Returns:
            tuple[Message, ...]: Сообщения в порядке появления.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана. В переписке это опаснее, чем в списке: пропущенное
                сообщение выглядит как ненаписанное.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"переписка прочитана не полностью ({self.completeness}, причина: "
                f"{self.reason}), собрано {self.rows_accepted} из {self.rows_total}. "
                "Передайте accept_incomplete=True, если готовы работать с неполными "
                "данными"
            )
        return self._messages

    def __len__(self) -> int:
        """Возвращает число собранных сообщений.

        Returns:
            int: Число собранных сообщений.
        """
        return len(self._messages)


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


def _origin(message: Node) -> Origin:
    """Определяет происхождение сообщения по разметке.

    Требуются оба признака сразу, и это правило с закрытым отказом. Достаточно
    было бы одного, если бы разметка не менялась; она меняется.

    Args:
        message (Node): Узел сообщения.

    Returns:
        Origin: Происхождение. UNKNOWN, если признаки разошлись.
    """
    has_alert = message.css_first(_ALERT) is not None
    has_author_link = message.css_first(_AUTHOR_LINK) is not None

    if has_alert and not has_author_link:
        return Origin.SYSTEM
    if has_author_link and not has_alert:
        return Origin.HUMAN
    return Origin.UNKNOWN


def _attribute(node: Node | None, name: str, field_name: str) -> Observed[str]:
    """Извлекает значение атрибута как наблюдаемое значение.

    Пустой атрибут даёт пустое значение, а не наблюдение. Тип Observed обещает,
    что PRESENT - это непустое значение, и собирать его в состоянии, которое он
    сам себе запрещает, значит отбирать у вызывающего единственный способ
    отличить «адрес есть» от «атрибут пуст».

    Args:
        node (Node | None): Узел либо None, если селектор не нашёл ничего.
        name (str): Имя атрибута.
        field_name (str): Имя поля для причины отсутствия.

    Returns:
        Observed[str]: Наблюдение. Отсутствие узла, отсутствие атрибута и пустое
        значение различаются.
    """
    if node is None:
        return Observed.missing(f"selector_no_match:{field_name}")
    value = ((node.attributes or {}).get(name) or "").strip()
    return Observed.present(value) if value else Observed.empty("")


def _external_links(message: Node, host: str) -> Observed[tuple[str, ...]]:
    """Собирает ссылки из текста, ведущие за пределы площадки.

    Ссылки собираются, чтобы вызывающий их видел. Ходить по ним нельзя: их пишет
    собеседник, и переход означал бы, что содержимое переписки управляет
    поведением клиента.

    Поле наблюдаемое, а не голой последовательностью. Разница здесь та же, что и
    у остальных полей: пустая последовательность означает «ссылок не было», а
    ненаблюдённое - «тела сообщения мы не нашли». Прежде эти два случая
    совпадали, и переименование класса тела давало ноль ссылок при полноте
    complete и нуле повреждений - то есть неотличимо от сообщения без ссылок.

    Args:
        message (Node): Узел сообщения.
        host (str): Хост площадки.

    Returns:
        Observed[tuple[str, ...]]: Адреса, ведущие на другие хосты, либо
        ненаблюдённое значение, если тело сообщения не найдено.
    """
    body = message.css_first(_TEXT)
    if body is None:
        return Observed.missing("selector_no_match:external_links")

    found: list[str] = []
    for link in body.css("a[href]"):
        href = ((link.attributes or {}).get("href") or "").strip()
        if not href:
            continue
        # Пустой хост - это не чужой хост. Относительная ссылка, якорь, mailto и
        # javascript хоста не имеют вовсе, и объявлять их внешними значило бы
        # выдавать за адрес другой площадки то, что адресом другой площадки не
        # является. Условие взято из _skeleton.mask_path, где оно с самого начала
        # написано верно: разошедшиеся копии одного правила - ровно то, из-за
        # чего заводился _host.py.
        if not host_of(href):
            continue
        # Сравнение подстрокой здесь стояло раньше и выглядело работающим.
        # Адрес funpay.com.evil.example содержит имя площадки и проходил такую
        # проверку - то есть ссылка на подставной сайт числилась своей и в
        # перечень внешних не попадала.
        if not same_host(href, host):
            found.append(href)
    return Observed.present(tuple(found)) if found else Observed.empty(())


def _parse_message(message: Node, index: int, host: str) -> tuple[Message, list[Defect]]:
    """Разбирает одно сообщение.

    Args:
        message (Node): Узел сообщения.
        index (int): Порядковый номер в переписке.
        host (str): Хост площадки, для отделения внешних ссылок.

    Returns:
        tuple[Message, list[Defect]]: Сообщение и перечень повреждений.
    """
    defects: list[Defect] = []
    origin = _origin(message)

    if origin is Origin.UNKNOWN:
        defects.append(
            Defect(
                severity=Severity.ROW,
                code="origin_indeterminate",
                detail=(
                    "признаки происхождения разошлись: обёртка предупреждения и "
                    "ссылка на автора присутствуют либо отсутствуют одновременно"
                ),
                row_index=index,
            )
        )

    raw_id = ((message.attributes or {}).get("id") or "").strip()
    author_link = message.css_first(_AUTHOR_LINK)

    entry = Message(
        message_id=(
            Observed.present(raw_id) if raw_id else Observed.missing("attribute_absent:id")
        ),
        row_index=index,
        origin=origin,
        author_name=_text(message.css_first(_AUTHOR_NAME), "author_name"),
        author_href=_attribute(author_link, "href", "author_href"),
        text=_text(message.css_first(_TEXT), "text"),
        time_text=_text(message.css_first(_DATE), "time_text"),
        time_full_text=_title(message.css_first(_DATE)),
        external_links=_external_links(message, host),
    )

    for name in _CHECKED_FIELDS:
        if not getattr(entry, name).is_observed:
            defects.append(
                Defect(
                    severity=Severity.FIELD,
                    code="field_not_observed",
                    detail="поле сообщения не найдено там, где ожидалось",
                    row_index=index,
                    field_name=name,
                )
            )

    return entry, defects


#: Поля, отсутствие которых у сообщения - повреждение уровня поля.
#:
#: Перечень узкий намеренно. Имя и время автора помечены в схеме как возможно
#: ненаблюдаемые - у системного сообщения автора нет вовсе, и в снимке шесть
#: таких из одиннадцати. Повреждение на каждое из них было бы ложной тревогой,
#: то есть шумом, за которым перестают следить.
_CHECKED_FIELDS: Final[tuple[str, ...]] = ("message_id", "text")

#: Поля, отсутствие которых во ВСЕХ сообщениях означает поломку разметки.
#:
#: Здесь перечень шире. Одно сообщение без автора - обычное дело, все сообщения
#: без автора - изменившаяся вёрстка, и разница между этими случаями видна
#: только по странице целиком. Прежде не было ни того перечня, ни этого:
#: переименование класса имени, даты или тела давало complete и ноль
#: повреждений, тогда как у соседних разборов та же порча даёт partial.
_PAGE_LEVEL_FIELDS: Final[tuple[str, ...]] = (
    "message_id",
    "text",
    "author_name",
    "time_text",
)


def _title(node: Node | None) -> Observed[str]:
    """Извлекает подсказку с полной формой времени.

    Args:
        node (Node | None): Узел даты или None.

    Returns:
        Observed[str]: Значение подсказки. На странице заказов подсказки нет
        вовсе, поэтому её отсутствие здесь - наблюдение, а не поломка.
    """
    if node is None:
        return Observed.missing("selector_no_match:time_full_text")
    raw = ((node.attributes or {}).get("title") or "").strip()
    return Observed.present(raw) if raw else Observed.missing("attribute_absent:title")


def _shape_defects(tree: HTMLParser) -> list[Defect]:
    """Проверяет, что дерево сообщений имеет наблюдённую форму.

    Проверка нужна против одной угрозы: текст сообщения, попавший в разметку как
    разметка. Закрой отправитель ровно столько элементов, сколько нужно, и
    следом открой поддельное сообщение с обёрткой предупреждения - разбор
    прочтёт его как сообщение площадки, а бот выдачи товара примет за
    уведомление об оплате.

    Что проверка закрывает и чего не закрывает, надо назвать точно, иначе она
    даёт ложное спокойствие.

    Закрывает всякую несбалансированную попытку. Закрыто меньше, чем нужно, -
    поддельное сообщение оказывается внутри настоящего. Больше - оказывается вне
    контейнера. Оба случая здесь и ловятся.

    НЕ закрывает попытку с точным числом. Закрыв ровно столько элементов,
    сколько лежит между текстом и контейнером, отправитель получает поддельное
    сообщение, которое является законным прямым потомком контейнера и от
    настоящего структурно неотличимо. Отличить его нечем в принципе: следа
    вставки в разобранном дереве не остаётся.

    Отсюда две вещи. Первая: проверка поднимает цену - отправителю нужно знать
    точную глубину вёрстки, а она меняется при любой правке шаблона. Вторая, и
    она важнее: последним рубежом остаётся правило, записанное в docstring
    модуля, - **происхождение system не является подтверждением оплаты**. Ни
    одна проверка формы этого правила не заменяет.

    Наблюдалось при этом, что площадка текст сообщения экранирует: по скелету
    это непроверяемо (класс p покрывает и угловые скобки), но косвенно на это
    указывает разметка ссылок в сообщениях - она построена площадкой из простого
    текста, а не сохранена как есть.

    Args:
        tree (HTMLParser): Разобранный документ.

    Returns:
        list[Defect]: Повреждения уровня страницы. Пустой перечень, если форма
        совпадает с наблюдённой.
    """
    defects: list[Defect] = []
    items = tree.css(_MESSAGE)

    # Считается число совпадений внутри узла, а не сравнение узлов между собой:
    # selectolax отдаёт новую обёртку на каждое обращение, и проверка по
    # тождеству срабатывала бы на неизменённой разметке. Совпадение ровно одно -
    # сам узел; больше одного означает вложенность.
    inside_message = sum(1 for node in items if len(node.css(_MESSAGE)) > 1)
    if inside_message:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="message_nested_in_message",
                detail=(
                    f"сообщений, вложенных в другое сообщение: {inside_message}. "
                    "В наблюдённой разметке такого не бывает, и вложенность "
                    "означает либо смену вёрстки, либо разметку внутри текста"
                ),
            )
        )

    inside_text = sum(1 for node in tree.css(_TEXT) if node.css_first(_MESSAGE) is not None)
    if inside_text:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="message_inside_message_text",
                detail=(
                    f"сообщений внутри текста другого сообщения: {inside_text}. "
                    "Текст пишет собеседник, и разметка в нём означает, что он "
                    "управляет разбором"
                ),
            )
        )

    stray = sum(
        1
        for node in items
        if node.parent is None
        or "chat-message-list" not in ((node.parent.attributes or {}).get("class") or "")
    )
    if stray:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="message_outside_the_list",
                detail=(
                    f"сообщений не на своём месте в дереве: {stray}. В наблюдённой "
                    "разметке каждое сообщение - прямой потомок контейнера"
                ),
            )
        )

    return defects


def parse_thread(html: str, *, observed_at: datetime, host: str = "funpay.com") -> Thread:
    """Разбирает переписку.

    Args:
        html (str): Тело страницы, уже признанное пригодным.
        observed_at (datetime): Момент наблюдения.
        host (str): Хост площадки, для отделения внешних ссылок в тексте.

    Returns:
        Thread: Сообщения вместе с полнотой и перечнем повреждений.

    Raises:
        ProtocolChangedError: Если контейнера сообщений нет либо кандидаты были,
            а собрать не удалось ни одного.
    """
    tree = HTMLParser(html)
    defects: list[Defect] = []

    if tree.css_first(_LIST) is None:
        raise ProtocolChangedError(
            f"на странице нет контейнера сообщений ({_LIST}). Пустую переписку "
            "вернуть нельзя: она неотличима от несуществующей"
        )

    found = collect_rows(tree, _LIST, _MESSAGE)
    defects.extend(found.defects)

    entries: list[Message] = []
    for index, node in enumerate(found.rows):
        entry, message_defects = _parse_message(node, index, host)
        defects.extend(message_defects)
        entries.append(entry)

    rows_total = max(len(found.rows), found.children, len(tree.css(_MESSAGE)))
    rows_accepted = len(entries)

    if rows_total and not rows_accepted:
        raise ProtocolChangedError(
            f"кандидатов в сообщения {rows_total}, собрать не удалось ни одного. "
            "Это изменение разметки, а не пустая переписка"
        )

    defects.extend(_shape_defects(tree))

    for name in _PAGE_LEVEL_FIELDS:
        if entries and all(not getattr(entry, name).is_observed for entry in entries):
            defects.append(
                Defect(
                    severity=Severity.PAGE,
                    code="field_missing_in_all_rows",
                    detail=f"поле {name} отсутствует у всех собранных сообщений",
                    field_name=name,
                )
            )

    if entries and all(m.origin is Origin.UNKNOWN for m in entries):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="origin_indeterminate_in_all_messages",
                detail=(
                    "происхождение не определяется ни у одного сообщения: "
                    "разметка изменилась, и различать площадку и собеседника нечем"
                ),
            )
        )

    if not rows_total:
        completeness, reason = Completeness.UNKNOWN, "empty_thread_not_observed"
    elif not defects:
        completeness, reason = Completeness.COMPLETE, "all_messages_parsed"
    elif any(d.severity is Severity.PAGE for d in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    else:
        completeness, reason = Completeness.PARTIAL, "row_defects"

    return Thread(
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        rows_total=rows_total,
        rows_accepted=rows_accepted,
        rows_rejected=rows_total - rows_accepted,
        defects=tuple(defects),
        _messages=tuple(entries),
    )
