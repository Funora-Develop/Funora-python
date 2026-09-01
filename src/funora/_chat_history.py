"""Дочитывание переписки назад от курсора.

ЗАПРОС ЗДЕСЬ ЗАИМСТВОВАН ЦЕЛИКОМ, и это отличает модуль от всех прочих. У
прежних заимствований чужим был ВЫВОД о действии запроса, который мы наблюдали
сами; здесь чужие и адрес, и оба имени параметров, и форма ответа. Своего
наблюдения этой точки нет ни одного: наш способ прочитать переписку - целая
страница /chat/?node=, и она отдаёт лишь то, что площадка показала сразу.

Согласия операция не спрашивает: это чтение, а правило о согласии разводит
чтение и запись нарочно. Ошибка чтения видна сразу и необратимого следа не
оставляет.

НАПРАВЛЕНИЕ ПРОВЕРЯЕТСЯ ЗДЕСЬ ЖЕ. Утверждение «курсор отдаёт сообщения СТАРШЕ
него» - чужое и наблюдением не подтверждённое. Молча отданный список выглядит
одинаково правильным в обе стороны, и непроверяемым это осталось бы навсегда,
поэтому разбор сверяет пришедшие идентификаторы с посланным курсором сам.

ОТКУДА БЕРЁТСЯ ИДЕНТИФИКАТОР СООБЩЕНИЯ. Из поля ответа, а не из разметки. На
странице он лежит в атрибуте узла сообщения, здесь же приходит отдельным числом
рядом с разметкой - и это надёжнее: атрибут может не попасть во фрагмент вовсе.
Ради этого разбор страницы переиспользуется, а идентификатор подменяется после
него; повреждение «идентификатор не найден» для такой строки снимается, потому
что найден он в другом месте.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from selectolax.parser import HTMLParser

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from ._thread import Message, _parse_message
from .errors import CursorIncompatibleError, IncompleteResultError, UnexpectedResponseError
from .extraction import SELECTORS

__all__ = ["CHAT_HISTORY_PATH", "HISTORY_HEADERS", "ChatHistory", "parse_history"]

#: Адрес догрузки. Заимствован; своего наблюдения нет.
CHAT_HISTORY_PATH: Final[str] = "/chat/history"

#: Заголовки запроса. Признак «спрашивает сценарий страницы» обязателен по тому
#: же чужому сообщению, что и адрес: без него площадка отвечает страницей.
HISTORY_HEADERS: Final[dict[str, str]] = {
    "accept": "*/*",
    "x-requested-with": "XMLHttpRequest",
}

#: Узел одного сообщения в разметке. Тот же, что на странице переписки.
_MESSAGE: Final[str] = SELECTORS["chats.message.item"]


@dataclass(frozen=True, slots=True)
class ChatHistory:
    """Итог дочитывания переписки назад.

    Attributes:
        chat_id (str): Узел переписки, у которого спрашивали.
        cursor_sent (str): Посланный курсор - идентификатор, от которого
            просили назад. Хранится ради двух вещей: на нём стоит сверка
            направления, и по нему вызывающий видит, сдвинулось ли листание.
        exhausted (bool): Площадка не отдала ни одного сообщения. Это
            наблюдение «начало переписки», а не сбой.
        completeness (Completeness): Полнота прочитанного.
        reason (str): Машиночитаемая причина, по которой полнота такова.
        observed_at (datetime): Момент наблюдения.
        rows_total (int): Сколько записей отдала площадка.
        rows_accepted (int): Сколько разобрано.
        rows_rejected (int): Сколько отброшено.
        defects (tuple[Defect, ...]): Обнаруженные повреждения.
    """

    chat_id: str
    cursor_sent: str
    exhausted: bool
    completeness: Completeness
    reason: str
    observed_at: datetime
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    defects: tuple[Defect, ...]
    _messages: tuple[Message, ...] = field(repr=False, default=())

    def messages(self, *, accept_incomplete: bool = False) -> tuple[Message, ...]:
        """Возвращает догруженные сообщения.

        Args:
            accept_incomplete (bool): Признание готовности работать с неполным
                результатом.

        Returns:
            tuple[Message, ...]: Сообщения в порядке ответа площадки.

        Raises:
            IncompleteResultError: Если полнота отлична от COMPLETE, а неполнота
                не признана. Пропущенное при листании сообщение не вернётся:
                следующий шаг возьмёт курсор от того, что дошло.
        """
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(
                f"переписка догружена не полностью ({self.completeness}, причина: "
                f"{self.reason}), собрано {self.rows_accepted} из {self.rows_total}. "
                "Передайте accept_incomplete=True, если готовы работать с неполными "
                "данными"
            )
        return self._messages

    def __len__(self) -> int:
        """Возвращает число догруженных сообщений.

        Returns:
            int: Число догруженных сообщений.
        """
        return len(self._messages)


def _entries(payload: object) -> list[object]:
    """Достаёт перечень сообщений из ответа, отвергая всё непонятное.

    ОТСУТСТВИЕ КЛЮЧА И ПУСТОЙ ПЕРЕЧЕНЬ РАЗВЕДЕНЫ НАРОЧНО. Соседняя реализация
    отвечает пустым списком на оба случая, и «переписка кончилась» становится у
    неё неотличимо от «ответ изменился». Пустой перечень - положительный признак
    конца; отсутствие ключа не признак ничего.

    Args:
        payload (object): Разобранное тело ответа.

    Returns:
        list[object]: Записи сообщений в порядке ответа.

    Raises:
        UnexpectedResponseError: Если ответ не той формы.
    """
    if not isinstance(payload, dict):
        raise UnexpectedResponseError(
            f"ответ догрузки ожидался объектом, получен {type(payload).__name__}"
        )
    chat = payload.get("chat")
    if not isinstance(chat, dict):
        raise UnexpectedResponseError(
            "в ответе догрузки нет объекта chat. Пустой переписки это не "
            "означает: у кончившейся переписки chat есть, а messages пуст"
        )
    if "messages" not in chat:
        raise UnexpectedResponseError(
            "в ответе догрузки нет ключа chat.messages. Отсутствие ключа - не "
            "признак конца переписки, а признак того, что ответ стал другим"
        )
    entries = chat["messages"]
    if not isinstance(entries, list):
        raise UnexpectedResponseError(
            f"chat.messages ожидалось перечнем, получено {type(entries).__name__}"
        )
    return entries


def _identifier(entry: object, index: int) -> tuple[str, Defect | None]:
    """Читает идентификатор записи.

    Args:
        entry (object): Запись сообщения из ответа.
        index (int): Порядковый номер записи.

    Returns:
        tuple[str, Defect | None]: Идентификатор и повреждение, если он непригоден.
    """
    raw = entry.get("id") if isinstance(entry, dict) else None
    # Признак строгий: у идентификатора сообщения только цифры. Строка «12a»
    # сравнивается с курсором как угодно, и сверка направления на ней молчит.
    text = str(raw) if isinstance(raw, int | str) else ""
    if not text.isdigit():
        return "", Defect(
            severity=Severity.ROW,
            code="identifier_unreadable",
            detail=(
                f"идентификатор записи не число: {text!r}. Сверить направление "
                "листания по нему нельзя, и запись отброшена"
            ),
            row_index=index,
            field_name="message_id",
        )
    return text, None


def _message(entry: object, identifier: str, index: int, host: str) -> tuple[Message, list[Defect]]:
    """Разбирает разметку одной записи тем же разбором, что и страницу.

    УЗЕЛ БЕРЁТСЯ ГИБКО. Приходит ли во фрагменте обёртка сообщения или только
    его нутро - у нас не наблюдалось, а разбор работает в обоих случаях: если
    обёртки нет, указатели ищутся от корня фрагмента и находят то же самое.

    Args:
        entry (object): Запись сообщения из ответа.
        identifier (str): Идентификатор, прочитанный из поля ответа.
        index (int): Порядковый номер записи.
        host (str): Хост площадки, для отделения внешних ссылок.

    Returns:
        tuple[Message, list[Defect]]: Сообщение и перечень повреждений.

    Raises:
        UnexpectedResponseError: Если разметки в записи нет.
    """
    markup = entry.get("html") if isinstance(entry, dict) else None
    if not isinstance(markup, str):
        raise UnexpectedResponseError(
            f"у записи {index} нет разметки в поле html: {type(markup).__name__}"
        )

    tree = HTMLParser(markup)
    node = tree.css_first(_MESSAGE) or tree.body
    if node is None:
        raise UnexpectedResponseError(f"разметку записи {index} не удалось разобрать вовсе")

    parsed, defects = _parse_message(node, index, host)
    # Идентификатор подменяется прочитанным из поля ответа: оно надёжнее
    # атрибута, которого во фрагменте может не быть вовсе. Повреждение о его
    # отсутствии снимается тем же движением - иначе разбор жалуется на то, что
    # у него есть.
    parsed = dataclasses.replace(parsed, message_id=Observed.present(identifier))
    defects = [
        one
        for one in defects
        if not (one.code == "field_not_observed" and one.field_name == "message_id")
    ]
    return parsed, defects


def _check_direction(collected: list[tuple[str, Message]], cursor: str) -> None:
    """Сверяет направление листания с посланным курсором.

    ЗДЕСЬ ПРОВЕРЯЕТСЯ ЧУЖОЕ УТВЕРЖДЕНИЕ, и ради него всё и затевалось. Что
    курсор отдаёт сообщения СТАРШЕ него, известно от независимой реализации того
    же протокола и нами не наблюдалось.

    Args:
        collected (list[tuple[str, Message]]): Разобранные записи.
        cursor (str): Посланный курсор.

    Returns:
        None

    Raises:
        CursorIncompatibleError: Если площадка вернула не ту сторону.
    """
    edge = int(cursor)
    wrong = sorted(int(one) for one, _ in collected if int(one) >= edge)
    if wrong:
        raise CursorIncompatibleError(
            f"догрузка от курсора {cursor} вернула {len(wrong)} сообщений НЕ СТАРШЕ "
            f"него (например {wrong[0]}). Направление листания у этой точки взято "
            "у независимой реализации того же протокола и нами не наблюдалось; "
            "расхождение означает, что взято оно неверно либо площадка его "
            "изменила. Ничего не возвращаем: список, отданный молча, выглядит "
            "одинаково правильным в обе стороны"
        )


def parse_history(
    payload: object,
    *,
    chat_id: str,
    cursor: str,
    observed_at: datetime,
    host: str = "funpay.com",
) -> ChatHistory:
    """Разбирает ответ догрузки переписки.

    Args:
        payload (object): Разобранное тело ответа.
        chat_id (str): Узел переписки, у которого спрашивали.
        cursor (str): Посланный курсор.
        observed_at (datetime): Момент наблюдения.
        host (str): Хост площадки, для отделения внешних ссылок.

    Returns:
        ChatHistory: Сообщения вместе с полнотой и признаком конца.

    Raises:
        UnexpectedResponseError: Если ответ не той формы.
        CursorIncompatibleError: Если площадка вернула не ту сторону листания.
    """
    entries = _entries(payload)
    defects: list[Defect] = []
    collected: list[tuple[str, Message]] = []

    for index, entry in enumerate(entries):
        identifier, broken = _identifier(entry, index)
        if broken is not None:
            defects.append(broken)
            continue
        parsed, row_defects = _message(entry, identifier, index, host)
        defects.extend(row_defects)
        collected.append((identifier, parsed))

    # Сверка направления идёт ДО объявления полноты и до всего прочего: список
    # не с той стороны непригоден целиком, и полнота у него бессмысленна.
    _check_direction(collected, cursor)

    seen = [one for one, _ in collected]
    if len(set(seen)) != len(seen):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="duplicate_identifiers",
                detail=(
                    f"записей с прочитанным идентификатором {len(seen)}, "
                    f"различимых {len(set(seen))}: листание встанет на месте"
                ),
                field_name="message_id",
            )
        )

    rows_total = len(entries)
    rows_accepted = len(collected)

    if not rows_total:
        # Пустой ответ - ПОЛНОЕ наблюдение, а не неизвестность. Площадке нечего
        # отдать назад, и это ровно тот ответ, ради которого спрашивали.
        completeness, reason = Completeness.COMPLETE, "history_exhausted"
    elif not defects:
        completeness, reason = Completeness.COMPLETE, "all_messages_parsed"
    elif any(one.severity is Severity.PAGE for one in defects):
        completeness, reason = Completeness.PARTIAL, "page_defects"
    else:
        completeness, reason = Completeness.PARTIAL, "row_defects"

    return ChatHistory(
        chat_id=chat_id,
        cursor_sent=cursor,
        exhausted=not rows_total,
        completeness=completeness,
        reason=reason,
        observed_at=observed_at,
        rows_total=rows_total,
        rows_accepted=rows_accepted,
        rows_rejected=rows_total - rows_accepted,
        defects=tuple(defects),
        _messages=tuple(one for _, one in collected),
    )
