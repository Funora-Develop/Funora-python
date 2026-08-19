"""Роутер обработчиков и один шаг цикла наблюдения.

Роутер чистый, шаг цикла - почти: он получает снимки от вызывающего и ничего не
знает ни о сети, ни о часах. Сам цикл со сном живёт в клиенте.

Главное правило одно, и всё остальное здесь ради него: базовый снимок сдвигается
только после того, как все обработчики отработали. Упавший обработчик оставляет
базу на месте, и то же событие приходит снова. Сдвинь базу раньше - и событие,
которое обработчик не смог обработать, исчезнет навсегда, а обработчик как раз и
падает на тех событиях, которые важнее прочих.

Отсюда же требование к обработчику: он обязан быть идемпотентным. Гарантия
доставки - не менее одного раза, повтор возможен всегда, и второй вызов на том же
событии не должен выдавать товар дважды.

Холодный старт молчит намеренно. Первый снимок сохраняется без событий, и
выдаётся одно watch.primed: иначе первый запуск даёт лавину «изменений» по всему
наблюдаемому множеству сразу.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from ._diff import Event
from .errors import ConfigurationError, FunoraError, HandlerError
from .events import EventType

__all__ = ["Router", "Handler", "StepResult", "dispatch", "adispatch", "dispatch_core"]

_log = logging.getLogger("funora.watch")

#: Обработчик события. Асинхронный клиент принимает и сопрограммы: там
#: возвращённое ожидаемое значение дожидается, а здесь - отвергается вслух.
Handler = Callable[[Event], object]

#: Просьба вызвать один обработчик на одном событии. Общая часть раздачи
#: возвращает её вместо вызова: вызывать синхронно и асинхронно - разные вещи, а
#: решать, что считать отказом и можно ли двигать курсор, - одна и та же.
Invoke = tuple[Handler, Event]

#: Событие, которым отмечается сохранение первого снимка.
_PRIMED: Final[EventType] = EventType.WATCH_PRIMED


@dataclass
class Router:
    """Реестр обработчиков событий.

    Обработчики хранятся по типу события. Обработчик, зарегистрированный без
    типа, получает все события: это нужно журналированию и метрикам, которым
    важны не отдельные типы, а поток целиком.

    Attributes:
        by_type (dict[EventType, list[Handler]]): Обработчики по типам.
        catch_all (list[Handler]): Обработчики, получающие все события.
    """

    by_type: dict[EventType, list[Handler]] = field(default_factory=dict)
    catch_all: list[Handler] = field(default_factory=list)

    def on(self, event_type: EventType | None = None) -> Callable[[Handler], Handler]:
        """Регистрирует обработчик события.

        Args:
            event_type (EventType | None): Тип события. None означает все типы.

        Returns:
            Callable[[Handler], Handler]: Декоратор, возвращающий обработчик
            без изменений, чтобы его можно было вызвать и напрямую.
        """

        def register(handler: Handler) -> Handler:
            """Добавляет обработчик в реестр.

            Args:
                handler (Handler): Обработчик события.

            Returns:
                Handler: Тот же обработчик.
            """
            if event_type is None:
                self.catch_all.append(handler)
            else:
                self.by_type.setdefault(event_type, []).append(handler)
            return handler

        return register

    def handlers_for(self, event: Event) -> tuple[Handler, ...]:
        """Возвращает обработчики, которым положено это событие.

        Args:
            event (Event): Событие.

        Returns:
            tuple[Handler, ...]: Обработчики в порядке регистрации: сначала
            привязанные к типу, потом получающие всё.
        """
        return (*self.by_type.get(event.type, ()), *self.catch_all)


@dataclass(frozen=True, slots=True)
class StepResult:
    """Итог одного шага наблюдения.

    Attributes:
        delivered (tuple[Event, ...]): События, дошедшие до обработчиков.
        failed (tuple[Event, ...]): События, на которых обработчик упал. Курсор
            не сдвигается, пока список непуст: иначе они исчезнут навсегда.
        advance (bool): Можно ли сдвигать курсор.
        errors (tuple[HandlerError, ...]): Отказы обработчиков.
        fatal (FunoraError | None): Первая ошибка Funora, поднятая обработчиком.
            Это не его баг, а условие площадки - истёкшая сессия, исчерпанный
            бюджет, - и вызывающий обязан её увидеть. Партия при этом
            дорабатывается до конца: отказ на первом событии не должен терять
            все остальные.
    """

    delivered: tuple[Event, ...]
    failed: tuple[Event, ...]
    advance: bool
    errors: tuple[HandlerError, ...]
    fatal: FunoraError | None = None


def dispatch_core(
    router: Router, events: tuple[Event, ...]
) -> Generator[Invoke, Exception | None, StepResult]:
    """Раздаёт события обработчикам и решает судьбу базового снимка.

    Обработчики здесь не вызываются. Ядро просит вызвать очередной и ждёт
    ответа: None, если обошлось, либо пойманное исключение. Так решение о том,
    что считать отказом и можно ли двигать курсор, остаётся одно на синхронный
    и асинхронный клиент - а оно и есть главное правило цикла.

    Порядок соблюдается внутри одного ключа упорядочивания. События с разными
    ключами независимы, и здесь они всё равно идут последовательно: правило про
    порядок этим не нарушается.

    Отказ одного обработчика не отменяет остальные события. Он отменяет только
    сдвиг базы, и следующий шаг принесёт непринятое снова.

    Args:
        router (Router): Реестр обработчиков.
        events (tuple[Event, ...]): События этого шага.

    Yields:
        Invoke: Пара «обработчик и событие», которую надо вызвать.

    Returns:
        StepResult: Что доставлено, что нет, и можно ли сдвигать базу.
    """
    delivered: list[Event] = []
    failed: list[Event] = []
    errors: list[HandlerError] = []
    fatal: FunoraError | None = None

    for event in events:
        handlers = router.handlers_for(event)
        if not handlers:
            # Событие без обработчика не считается непринятым: подписка на всё
            # подряд не обязанность вызывающего, и база из-за неё стоять не
            # должна.
            delivered.append(event)
            continue

        broke = False
        for handler in handlers:
            exc = yield (handler, event)
            if exc is None:
                continue
            if isinstance(exc, FunoraError):
                # Раньше здесь стоял raise, и партия обрывалась посреди раздачи:
                # накопленные delivered и failed пропадали, курсор не
                # сохранялся, а цикл падал целиком. Условие площадки при этом
                # никуда не девалось - оно просто уносило с собой все остальные
                # события партии.
                broke = True
                if fatal is None:
                    fatal = exc
                _log.warning(
                    "обработчик получил ошибку площадки на событии %s (ключ %s): %s",
                    event.type,
                    event.ordering_key,
                    type(exc).__name__,
                )
                break
            broke = True
            error = HandlerError(
                f"обработчик {getattr(handler, '__name__', handler)!r} упал на "
                f"событии {event.type} с ключом {event.ordering_key}: "
                f"{type(exc).__name__}"
            )
            error.__cause__ = exc
            errors.append(error)
            _log.warning(
                "обработчик упал на событии %s (ключ %s): %s",
                event.type,
                event.ordering_key,
                type(exc).__name__,
            )
            break

        (failed if broke else delivered).append(event)

    return StepResult(
        delivered=tuple(delivered),
        failed=tuple(failed),
        # Курсор сдвигается только когда непринятых нет. Сдвинь его раньше - и
        # событие, на котором обработчик упал, исчезнет навсегда.
        advance=not failed,
        errors=tuple(errors),
        fatal=fatal,
    )


def dispatch(router: Router, events: tuple[Event, ...]) -> StepResult:
    """Раздаёт события синхронно.

    Args:
        router (Router): Реестр обработчиков.
        events (tuple[Event, ...]): События этого шага.

    Returns:
        StepResult: Что доставлено, что нет, и можно ли сдвигать базу.

    Raises:
        ConfigurationError: Если обработчик оказался сопрограммой. Синхронный
            клиент дожидаться её не умеет, а промолчать здесь значило бы
            зарегистрировать обработчик, который никогда не выполнится: ни
            исключения, ни события в журнале - просто ничего не происходит.
    """
    core = dispatch_core(router, events)
    reply: Exception | None = None
    while True:
        try:
            handler, event = core.send(reply)
        except StopIteration as stop:
            result: StepResult = stop.value
            return result
        reply = None
        try:
            outcome = handler(event)
        except Exception as exc:
            reply = exc
            continue
        if inspect.isawaitable(outcome):
            # Сопрограмму надо закрыть вручную, иначе интерпретатор допишет к
            # нашему внятному отказу своё «coroutine was never awaited».
            close = getattr(outcome, "close", None)
            if close is not None:
                close()
            raise ConfigurationError(
                f"обработчик {getattr(handler, '__name__', handler)!r} асинхронный, "
                "а клиент синхронный: дождаться его здесь некому. Возьмите "
                "AsyncClient либо сделайте обработчик обычной функцией"
            )


async def adispatch(router: Router, events: tuple[Event, ...]) -> StepResult:
    """Раздаёт события асинхронно.

    Принимаются и обычные функции, и сопрограммы: возвращённое ожидаемое
    значение дожидается, обычный результат берётся как есть. Решение о том, что
    считать отказом, - общее с синхронной раздачей и живёт в [dispatch_core].

    Args:
        router (Router): Реестр обработчиков.
        events (tuple[Event, ...]): События этого шага.

    Returns:
        StepResult: Что доставлено, что нет, и можно ли сдвигать базу.
    """
    core = dispatch_core(router, events)
    reply: Exception | None = None
    while True:
        try:
            handler, event = core.send(reply)
        except StopIteration as stop:
            result: StepResult = stop.value
            return result
        reply = None
        try:
            outcome = handler(event)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception as exc:
            reply = exc


def primed(account_id: str, observed_at: datetime, ordering_key: str) -> Event:
    """Собирает событие о сохранении первого снимка.

    Холодный старт молчит намеренно: события на каждую существующую сущность
    дали бы лавину «изменений» по всему наблюдаемому множеству сразу. Но молчать
    совсем нельзя - вызывающий должен знать, что наблюдение началось.

    Args:
        account_id (str): Идентификатор аккаунта.
        observed_at (datetime): Момент наблюдения.
        ordering_key (str): Ключ упорядочивания наблюдения.

    Returns:
        Event: Событие watch.primed.
    """
    return Event(
        id=f"primed:{account_id}:{ordering_key}",
        type=_PRIMED,
        ordering_key=ordering_key,
        entity_id=account_id,
        observed_at=observed_at,
        origin="structural",
        payload={"reason": "cold_start"},
    )
