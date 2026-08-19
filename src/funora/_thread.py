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

from ._observed import Observed
from ._orders import Completeness, Defect, Severity
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
        external_links (tuple[str, ...]): Ссылки из текста, ведущие за пределы
            площадки. Собраны для того, чтобы вызывающий видел их, а не для того,
            чтобы по ним ходить.
    """

    message_id: Observed[str]
    row_index: int
    origin: Origin
    author_name: Observed[str]
    author_href: Observed[str]
    text: Observed[str]
    time_text: Observed[str]
    time_full_text: Observed[str]
    external_links: tuple[str, ...]


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


def _external_links(message: Node, host: str) -> tuple[str, ...]:
    """Собирает ссылки из текста, ведущие за пределы площадки.

    Ссылки собираются, чтобы вызывающий их видел. Ходить по ним нельзя: их пишет
    собеседник, и переход означал бы, что содержимое переписки управляет
    поведением клиента.

    Args:
        message (Node): Узел сообщения.
        host (str): Хост площадки.

    Returns:
        tuple[str, ...]: Адреса, ведущие на другие хосты.
    """
    found: list[str] = []
    for link in message.css(".chat-msg-text a[href]"):
        href = ((link.attributes or {}).get("href") or "").strip()
        if href and f"//{host}" not in href:
            found.append(href)
    return tuple(found)


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
        author_name=_text(message.css_first(".media-user-name"), "author_name"),
        author_href=(
            Observed.present(((author_link.attributes or {}).get("href") or "").strip())
            if author_link is not None
            else Observed.missing("selector_no_match:author_href")
        ),
        text=_text(message.css_first(".chat-msg-text"), "text"),
        time_text=_text(message.css_first(".chat-msg-date"), "time_text"),
        time_full_text=_title(message.css_first(".chat-msg-date")),
        external_links=_external_links(message, host),
    )

    if not entry.message_id.is_observed:
        defects.append(
            Defect(
                severity=Severity.FIELD,
                code="field_not_observed",
                detail="у сообщения нет идентификатора в разметке",
                row_index=index,
                field_name="message_id",
            )
        )

    return entry, defects


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

    container = tree.css_first(_LIST)
    if container is None:
        raise ProtocolChangedError(
            f"на странице нет контейнера сообщений ({_LIST}). Пустую переписку "
            "вернуть нельзя: она неотличима от несуществующей"
        )

    messages = container.css(_MESSAGE)
    children = [node for node in container.iter() if node.tag != "-text"]

    if len(messages) != len(children):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="row_selector_undercount",
                detail=(
                    f"селектор сообщения нашёл {len(messages)}, "
                    f"а прямых детей контейнера {len(children)}"
                ),
            )
        )

    entries: list[Message] = []
    for index, node in enumerate(messages):
        entry, message_defects = _parse_message(node, index, host)
        defects.extend(message_defects)
        entries.append(entry)

    rows_total = max(len(messages), len(children))
    rows_accepted = len(entries)

    if rows_total and not rows_accepted:
        raise ProtocolChangedError(
            f"кандидатов в сообщения {rows_total}, собрать не удалось ни одного. "
            "Это изменение разметки, а не пустая переписка"
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
