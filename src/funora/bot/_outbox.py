"""Очередь исходящих: как попросить об отправке из чужого потока.

ЗАЧЕМ ОНА ВООБЩЕ НУЖНА. Клиент не защищён ни одной блокировкой. Бюджет,
ограничитель исходящих и состояние сессии - обычные изменяемые объекты, и у
двух из них проверка с последующей записью не атомарна: ограничитель сперва
спрашивает `check`, потом пишет `record`, а бюджет сперва резервирует, потом
списывает.

Второй поток, зовущий отправку напрямую, эти пары разрывает. Проявится это не
отказом и не исключением, а НЕДОСЧЁТОМ: ограничитель решит, что за час ушло
меньше сообщений, чем ушло на самом деле. Узнаете вы об этом от площадки.

Поэтому здесь заведён единственный порядок, при котором посторонний поток может
попросить об отправке: положить задание в очередь. Разбирает её тот же поток,
что ведёт наблюдение, - в паузе между опросами.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Final

from .._runner import SendResult
from ..errors import FunoraError

__all__ = ["SendCommand", "SendTicket", "Outbox"]

#: Сколько заданий очередь принимает, прежде чем отказывать.
#:
#: Предел нужен: телеграм-бот, у которого что-то заклинило, наполняет очередь
#: быстрее, чем наблюдение её вычерпывает - три отправки за шаг против
#: неограниченного числа нажатий. Очередь без предела съела бы память, а
#: сообщения всё равно ушли бы с опозданием на часы.
MAX_PENDING: Final[int] = 256


@dataclass(frozen=True, slots=True)
class SendCommand:
    """Просьба отправить сообщение.

    Attributes:
        chat_id (str): Числовой идентификатор диалога.
        text (str): Текст сообщения.
        idempotency_key (str): Ключ, по которому повтор узнаётся повтором.
            Задание с уже виденным ключом не отправляется второй раз.

            Обязателен НАРОЧНО, умолчания у него нет. Отправка необратима:
            лишнее сообщение покупателю не отменить, а перезапуск процесса,
            повтор события и нажатие кнопки дважды - обычные вещи. Ключ, который
            вызывающий обязан придумать сам, заставляет его хотя бы раз подумать
            о том, что считается тем же самым сообщением.
        declared_cold (bool): Признание, что переписка холодная и вы пишете
            первым.
    """

    chat_id: str
    text: str
    idempotency_key: str
    declared_cold: bool = False


@dataclass(slots=True)
class SendTicket:
    """Квитанция о принятом задании: чем оно кончилось.

    Ждать её можно из любого потока. Отправка при этом происходит не здесь, а в
    потоке наблюдения.

    Attributes:
        command (SendCommand): Задание, о котором квитанция.
    """

    command: SendCommand
    _done: threading.Event = field(default_factory=threading.Event)
    _result: SendResult | None = None
    _error: FunoraError | None = None
    _duplicate: bool = False

    @property
    def duplicate(self) -> bool:
        """Сообщает, что задание отброшено как повтор.

        Returns:
            bool: True, если ключ идемпотентности уже встречался.
        """
        return self._duplicate

    def settle(
        self,
        *,
        result: SendResult | None = None,
        error: FunoraError | None = None,
        duplicate: bool = False,
    ) -> None:
        """Закрывает квитанцию.

        Args:
            result (SendResult | None): Исход отправки.
            error (FunoraError | None): Отказ, если отправка не состоялась.
            duplicate (bool): Отброшено ли задание как повтор.

        Returns:
            None
        """
        self._result = result
        self._error = error
        self._duplicate = duplicate
        self._done.set()

    def wait(self, timeout: float | None = None) -> SendResult | None:
        """Ждёт исхода задания.

        Args:
            timeout (float | None): Сколько секунд ждать. None означает без
                предела.

        Returns:
            SendResult | None: Исход отправки. None означает, что задание
            отброшено как повтор либо ожидание истекло, - различить это можно
            признаками `duplicate` и `ready`.

        Raises:
            FunoraError: Тот самый отказ, что случился в потоке наблюдения.
                Бросается ЗДЕСЬ, в потоке вызывающего: иначе он о нём не узнал
                бы вовсе - в потоке наблюдения его некому ловить.
        """
        self._done.wait(timeout)
        if self._error is not None:
            raise self._error
        return self._result

    @property
    def ready(self) -> bool:
        """Сообщает, закрыта ли квитанция.

        Returns:
            bool: True, если задание уже отработано.
        """
        return self._done.is_set()


class Outbox:
    """Очередь исходящих сообщений.

    Класть задания можно из любого потока. Разбирает очередь тот, кто ведёт
    наблюдение, и только он.

    Args:
        max_pending (int): Сколько заданий держать, прежде чем отказывать.
    """

    __slots__ = ("_queue", "_seen", "_lock", "_owner")

    def __init__(self, max_pending: int = MAX_PENDING) -> None:
        self._queue: Queue[SendTicket] = Queue(maxsize=max_pending)
        # Ключи, которые уже отработаны. Множество растёт, и это осознанно:
        # забыть ключ значит разрешить повтор, а повтор здесь - второе сообщение
        # покупателю. Память тут дешевле.
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._owner: int | None = None

    def put(self, command: SendCommand) -> SendTicket:
        """Кладёт задание в очередь.

        Звать можно из любого потока.

        Args:
            command (SendCommand): Просьба отправить сообщение.

        Returns:
            SendTicket: Квитанция, по которой можно дождаться исхода.

        Raises:
            UsageError: Если очередь переполнена.
        """
        from ..errors import UsageError

        ticket = SendTicket(command=command)

        with self._lock:
            if command.idempotency_key in self._seen:
                # Повтор узнаётся ДО очереди: иначе он занял бы место и дождался
                # бы своей отбраковки только у исполнителя.
                ticket.settle(duplicate=True)
                return ticket
            self._seen.add(command.idempotency_key)

        try:
            self._queue.put_nowait(ticket)
        except Exception as exc:  # noqa: BLE001 - очередь бросает голый Full
            with self._lock:
                # Ключ снимается обратно: задание не принято, и запрещать его
                # навсегда было бы наказанием за нашу же переполненность.
                self._seen.discard(command.idempotency_key)
            raise UsageError(
                f"очередь исходящих переполнена: {self._queue.maxsize} заданий ждут "
                "отправки. Наблюдение разбирает её по нескольку за шаг, и класть "
                "быстрее, чем она вычерпывается, значит копить сообщения, которые "
                "уйдут с опозданием на часы"
            ) from exc
        return ticket

    def take(self, limit: int) -> list[SendTicket]:
        """Забирает из очереди до указанного числа заданий.

        Args:
            limit (int): Сколько заданий забрать.

        Returns:
            list[SendTicket]: Взятые задания в порядке поступления.
        """
        taken: list[SendTicket] = []
        for _ in range(max(0, limit)):
            try:
                taken.append(self._queue.get_nowait())
            except Empty:
                break
        return taken

    def claim(self) -> None:
        """Запоминает поток, который разбирает очередь.

        Returns:
            None
        """
        self._owner = threading.get_ident()

    def is_owner(self) -> bool:
        """Сообщает, тот ли это поток, что разбирает очередь.

        Returns:
            bool: True, если вызов идёт из потока наблюдения.
        """
        return self._owner is not None and self._owner == threading.get_ident()

    @property
    def pending(self) -> int:
        """Сколько заданий ждёт отправки.

        Returns:
            int: Длина очереди.
        """
        return self._queue.qsize()
