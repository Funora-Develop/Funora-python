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

import asyncio
import inspect
import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from ._diff import Event, make_event
from .budget import HANDLER_TIMEOUT_MS, MAX_CONCURRENT_HANDLERS
from .errors import (
    ConfigurationError,
    FunoraError,
    HandlerCancelledError,
    HandlerError,
    HandlerTimeoutError,
)
from .events import ORDERING_KEY, EventType

__all__ = [
    "Router",
    "loss",
    "incomplete",
    "Handler",
    "StepResult",
    "PRODUCIBLE",
    "dispatch",
    "adispatch",
    "dispatch_core",
]

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

#: Событие, которым отмечается неполно собранный снимок.
_INCOMPLETE: Final[EventType] = EventType.SNAPSHOT_INCOMPLETE

#: Событие, которым объявляется потеря событий.
_LOSS: Final[EventType] = EventType.EVENT_LOSS

#: Причина, по которой наблюдение объявляется начавшимся.
_COLD_START: Final[str] = "cold_start"

#: Разделитель частей версии сущности.
#:
#: Управляющий знак, а не двоеточие: причина неполноты приходит со страницы
#: и содержать двоеточие вправе.
_PART_SEP: Final[str] = "\x1f"


#: Виды событий, которые эта реализация вправду порождает.
#:
#: Перечисление объявляет шестнадцать видов, реализация порождает пять. Прочие
#: одиннадцать - не задел на будущее, а ловушка: обработчик на них принимался
#: без возражений и не срабатывал ни разу, а молчание неотличимо от «ничего не
#: произошло».
#:
#: Однажды это уже случилось с message.created - событие было объявлено, а цикл
#: наблюдения не читал переписок вовсе. Починили тогда одно событие; правила,
#: по которому этого не случится снова, не написали.
#:
#: Перечень обязан быть заработанным: проверка сверяет его с тем, что вправду
#: порождается на снимках. Объявить вид и не порождать - то же молчание с другой
#: стороны.
PRODUCIBLE: Final[frozenset[EventType]] = frozenset(
    {
        EventType.MESSAGE_CREATED,
        EventType.CHAT_UNREAD_CHANGED,
        EventType.ORDER_CREATED,
        EventType.ORDER_STATUS_CHANGED,
        EventType.WATCH_PRIMED,
        EventType.SNAPSHOT_INCOMPLETE,
        EventType.EVENT_LOSS,
    }
)


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

        Подписка на вид, которого реализация не порождает, отвергается вслух.
        Промолчать значило бы завести обработчик, который не выполнится никогда,
        - а его молчание неотличимо от «ничего не произошло». Продавец,
        подписавшийся на отзывы, увидел бы ровно то же, что при отсутствии новых
        отзывов, и узнал бы об ошибке в тот день, когда отзыв придёт.

        Отказ происходит при регистрации, то есть при запуске. Строка в журнале
        была бы тем же молчанием с отсрочкой: журнал читают после происшествия,
        а не до.

        Обработчик без указания вида отказа не получает: он просит поток
        целиком и видит ровно то, что пришло.

        Args:
            event_type (EventType | None): Тип события. None означает все типы.

        Returns:
            Callable[[Handler], Handler]: Декоратор, возвращающий обработчик
            без изменений, чтобы его можно было вызвать и напрямую.

        Raises:
            ConfigurationError: Если вид события эта реализация не порождает.
        """
        if event_type is not None and event_type not in PRODUCIBLE:
            raise ConfigurationError(
                f"эта реализация не порождает событий вида {event_type}; "
                f"порождаются: {', '.join(sorted(str(kind) for kind in PRODUCIBLE))}"
            )

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
            if isinstance(exc, HandlerError):
                # Отказ обработчика - его беда, а не условие площадки, и это
                # верно даже для отказа по времени: HandlerTimeoutError входит в
                # иерархию Funora, и без этой ветки один задумавшийся обработчик
                # останавливал бы наблюдение целиком.
                #
                # Ветка стоит ПЕРЕД проверкой на FunoraError намеренно:
                # HandlerError её потомок, и обратный порядок отправил бы отказ
                # обработчика в fatal.
                broke = True
                errors.append(exc)
                _log.warning(
                    "обработчик отказал на событии %s (ключ %s): %s",
                    event.type,
                    event.ordering_key,
                    type(exc).__name__,
                )
                break
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
                    exc_info=exc,
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
            # exc_info обязателен. Трассировка сохранена в error.__cause__ и
            # никуда дальше не идёт: движок читает у результата delivered,
            # advance, fatal и длину failed, а errors не читает никто. Без неё
            # в журнале остаётся имя класса без единой строки о том, ГДЕ упало,
            # а событие приходит снова каждый шаг - и так по кругу.
            _log.warning(
                "обработчик упал на событии %s (ключ %s): %s",
                event.type,
                event.ordering_key,
                type(exc).__name__,
                exc_info=exc,
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


async def _adispatch_serially(router: Router, events: tuple[Event, ...]) -> StepResult:
    """Раздаёт события асинхронно и последовательно.

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
                # Предел времени объявлен спецификацией и до сих пор не
                # применялся нигде: обработчик, ушедший в вечное ожидание,
                # останавливал цикл наблюдения целиком. Ни исключения, ни
                # строки в журнале - клиент просто переставал ходить на
                # площадку, и внешне это неотличимо от «ничего не происходит».
                #
                # Обещание должно быть либо выполнено, либо снято. Здесь оно
                # выполнимо: сопрограмму можно отменить.
                await asyncio.wait_for(outcome, HANDLER_TIMEOUT_MS / 1000)
        except TimeoutError as exc:
            _log.warning(
                "обработчик не уложился в %d мс на событии %s (ключ %s)",
                HANDLER_TIMEOUT_MS,
                event.type,
                event.ordering_key,
                exc_info=exc,
            )
            timeout = HandlerTimeoutError(
                f"обработчик {getattr(handler, '__name__', handler)!r} не уложился "
                f"в {HANDLER_TIMEOUT_MS} мс на событии {event.type} с ключом "
                f"{event.ordering_key}"
            )
            timeout.__cause__ = exc
            reply = timeout
        except asyncio.CancelledError as exc:
            # CancelledError - потомок BaseException, а не Exception, поэтому
            # ветка ниже её не ловила и она пробивала раздачу насквозь: партия
            # теряла и доставленное, и недоставленное, курсор не сохранялся.
            #
            # Но проглотить её целиком нельзя. Отмена, пришедшая ИЗВНЕ, - это
            # отмена всей задачи, и съев её, мы сделали бы задачу неотменяемой.
            # Различить два случая позволяет счётчик отмен самой задачи: он
            # больше нуля ровно тогда, когда отменяют нас, а не когда отменился
            # обработчик.
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            _log.warning(
                "обработчик отменился на событии %s (ключ %s)",
                event.type,
                event.ordering_key,
                exc_info=exc,
            )
            cancelled = HandlerCancelledError(
                f"обработчик {getattr(handler, '__name__', handler)!r} отменён "
                f"на событии {event.type} с ключом {event.ordering_key}"
            )
            cancelled.__cause__ = exc
            reply = cancelled
        except Exception as exc:  # noqa: BLE001
            reply = exc


def _by_ordering_key(events: tuple[Event, ...]) -> tuple[tuple[Event, ...], ...]:
    """Разбивает партию на группы по ключу упорядочивания.

    Спецификация говорит прямо: порядок сохраняется внутри одного ключа, события
    с разными ключами порядка между собой не имеют. Отсюда и разбиение - внутри
    группы события останутся последовательными, между группами могут идти
    одновременно.

    Порядок групп - по первому вошедшему событию. Он нужен не исполнению, а
    сборке итога: итог обязан получиться один и тот же независимо от того, кто
    из групп успел раньше.

    Args:
        events (tuple[Event, ...]): События партии в порядке порождения.

    Returns:
        tuple[tuple[Event, ...], ...]: Группы в порядке первого события.
    """
    groups: dict[str, list[Event]] = {}
    for event in events:
        groups.setdefault(event.ordering_key, []).append(event)
    return tuple(tuple(group) for group in groups.values())


def _merge(events: tuple[Event, ...], results: tuple[StepResult, ...]) -> StepResult:
    """Собирает итог партии из итогов групп.

    Сборка нарочно не зависит от того, кто из групп завершился раньше. Иначе одна
    и та же партия давала бы разные итоги от запуска к запуску, а разными в них
    были бы список непринятых событий и первая ошибка площадки - то есть ровно
    то, по чему принимается решение о сдвиге курсора.

    Args:
        events (tuple[Event, ...]): События партии в исходном порядке.
        results (tuple[StepResult, ...]): Итоги групп в порядке групп.

    Returns:
        StepResult: Итог партии.
    """
    place = {id(event): index for index, event in enumerate(events)}

    def ordered(chosen: list[Event]) -> tuple[Event, ...]:
        """Возвращает события в исходном порядке партии.

        Args:
            chosen (list[Event]): События вперемешку.

        Returns:
            tuple[Event, ...]: Они же в порядке порождения.
        """
        return tuple(sorted(chosen, key=lambda event: place[id(event)]))

    delivered: list[Event] = []
    failed: list[Event] = []
    errors: list[HandlerError] = []
    fatal: FunoraError | None = None
    for result in results:
        delivered.extend(result.delivered)
        failed.extend(result.failed)
        errors.extend(result.errors)
        if fatal is None:
            fatal = result.fatal

    return StepResult(
        delivered=ordered(delivered),
        failed=ordered(failed),
        advance=not failed,
        errors=tuple(errors),
        fatal=fatal,
    )


async def adispatch(
    router: Router, events: tuple[Event, ...], *, concurrency: int = 1
) -> StepResult:
    """Раздаёт события асинхронно.

    По умолчанию последовательно - и это не осторожность ради осторожности.
    Обработчики принадлежат вызывающему, и одновременный их запуск меняет
    условия, в которых они писались: счётчик, дописывание в файл, соединение с
    базой перестают быть в единоличном пользовании. Просить об этом надо явно.

    С ``concurrency`` больше единицы события раздаются группами по ключу
    упорядочивания. Внутри группы порядок сохраняется, между группами - нет, и
    это ровно то, что говорит спецификация: события с разными ключами порядка
    между собой не имеют.

    Итог собирается детерминированно, независимо от того, кто из групп успел
    раньше. Иначе одна и та же партия давала бы разные списки непринятых
    событий - то есть разные решения о сдвиге курсора.

    Args:
        router (Router): Реестр обработчиков.
        events (tuple[Event, ...]): События этого шага.
        concurrency (int): Сколько ключей упорядочивания обрабатывать
            одновременно. Единица означает последовательную раздачу. Значение
            выше объявленного спецификацией предела отвергается: договор об
            одновременности - часть контракта, а не настройка вызывающего.

    Returns:
        StepResult: Что доставлено, что нет, и можно ли сдвигать базу.

    Raises:
        ConfigurationError: Если запрошено больше одновременных обработчиков,
            чем объявляет спецификация.
    """
    if concurrency > MAX_CONCURRENT_HANDLERS:
        # Предел объявлен спецификацией и до сих пор не применялся: вызывающий
        # мог попросить хоть тысячу. Тысяча одновременных обработчиков - это
        # тысяча одновременных соединений с чужой базой у него и, что важнее,
        # тысяча параллельных реакций на площадке.
        #
        # Отказ, а не тихое понижение: понизить молча значило бы дать
        # вызывающему неверное представление о том, как работает его код.
        raise ConfigurationError(
            f"запрошено {concurrency} одновременных обработчиков, "
            f"спецификация объявляет предел {MAX_CONCURRENT_HANDLERS}"
        )
    if concurrency <= 1 or len(events) < 2:
        return await _adispatch_serially(router, events)

    groups = _by_ordering_key(events)
    if len(groups) < 2:
        return await _adispatch_serially(router, events)

    limit = asyncio.Semaphore(concurrency)

    async def run(group: tuple[Event, ...]) -> StepResult:
        """Раздаёт одну группу, не превышая заданной одновременности.

        Args:
            group (tuple[Event, ...]): События одного ключа упорядочивания.

        Returns:
            StepResult: Итог группы.
        """
        async with limit:
            return await _adispatch_serially(router, group)

    results = await asyncio.gather(*(run(group) for group in groups))
    return _merge(events, tuple(results))


def primed(account_id: str, observed_at: datetime, entities: tuple[str, ...]) -> Event:
    """Собирает событие о сохранении первого снимка.

    Холодный старт молчит намеренно: события на каждую существующую сущность
    дали бы лавину «изменений» по всему наблюдаемому множеству сразу. Но молчать
    совсем нельзя - вызывающий должен знать, что наблюдение началось, а не что
    оно молчит по неисправности.

    Идентификатор и ключ упорядочивания строятся общим строителем, а не вручную.
    Прежде здесь собиралась строка «primed:{account_id}:{ordering_key}» с
    ключом «account:...», и это расходилось с нормативным «watch:{watch_id}»
    из спецификации, а версии сущности в идентификаторе не было вовсе.

    Args:
        account_id (str): Идентификатор аккаунта. Он же служит идентификатором
            наблюдения: наблюдение ведётся за аккаунтом целиком.
        observed_at (datetime): Момент наблюдения.
        entities (tuple[str, ...]): За чем установлено наблюдение.

    Returns:
        Event: Событие watch.primed.
    """
    return make_event(
        account_id=account_id,
        event_type=_PRIMED,
        entity_id=account_id,
        # Версия - причина. Приветствие приходит один раз за срок гашения, и
        # второе приветствие того же наблюдения было бы повтором.
        revision=_COLD_START,
        observed_at=observed_at,
        key_field="watch_id",
        payload={
            "watch_id": account_id,
            # Перечень, а не число. Прежняя редакция схемы объявляла здесь
            # количество - оно отвечает на «сколько», тогда как получателю нужен
            # ответ на «за чем».
            "entities": list(entities),
            "reason": _COLD_START,
        },
    )


def incomplete(
    account_id: str,
    observed_at: datetime,
    *,
    entity: str,
    reason: str,
    rows_total: int,
    rows_accepted: int,
    entity_ref: str | None = None,
) -> Event:
    """Собирает событие о неполно собранном снимке.

    Цикл наблюдения умел обращаться с неполным чтением и молчал о нём. Курсор он
    не двигал - это верно и защищает будущее: строки, выпавшие из неполного
    чтения, не будут сочтены исчезнувшими. Настоящее несдвинутый курсор не
    защищает никак: события по прочитанному порождаются, и обработчик принимает
    их за полную картину - обрабатывает часть заказов как все.

    Событие приходит в той же партии, что и события по этому списку. Отдельный
    поздний сигнал бесполезен: решение по неполным данным к тому времени уже
    принято.

    Версия сущности - сущность, причина И ОБА ЧИСЛА. Первая редакция брала одно
    число принятых строк, и этого мало: чтение 50 из 50 с тремя отброшенными и
    чтение 100 из 100 с пятьюдесятью тремя отброшенными дают одинаковое число
    принятых в одном случае из многих, а разница между «не хватает трёх» и «не
    хватает пятидесяти трёх» - это новость, которую гасить нельзя.

    Args:
        account_id (str): Идентификатор аккаунта, он же наблюдения.
        observed_at (datetime): Момент наблюдения.
        entity (str): Что прочитано неполно: orders, chats, thread.
        reason (str): Машиночитаемая причина неполноты со страницы.
        rows_total (int): Сколько кандидатов в строки нашлось.
        rows_accepted (int): Сколько строк принято.
        entity_ref (str | None): К какой сущности относится неполнота, если она
            не про список целиком. У переписки это диалог: без ссылки получатель
            не узнает, какой из полусотни прочитан наполовину.

    Returns:
        Event: Событие snapshot.incomplete.
    """
    return make_event(
        account_id=account_id,
        event_type=_INCOMPLETE,
        entity_id=account_id,
        revision=_PART_SEP.join(
            (entity, entity_ref or "", reason, f"{rows_accepted}/{rows_total}")
        ),
        observed_at=observed_at,
        key_field="watch_id",
        payload={
            # Идентификатор наблюдения лежит и в нагрузке: так велит схема
            # события, и это не дубль конверта без причины. Нагрузку принято
            # передавать дальше отдельно от конверта - в очередь, в журнал, - и
            # там она обязана оставаться самодостаточной.
            "watch_id": account_id,
            "entity": entity,
            # Ключ на месте всегда, а None означает «неполон список целиком»:
            # у списка нет отдельной сущности, к которой неполноту можно
            # отнести. Это неприменимость, а не незнание, и контракт различает
            # два этих смысла пометкой у поля.
            #
            # Опускать ключ было бы хуже: в нагрузке, уехавшей в очередь,
            # отсутствие ключа неотличимо от потери по дороге.
            "entity_ref": entity_ref,
            # Чтение однослойное: страница запрошена одна и получена одна. Поля
            # сохранены, потому что неполнота бывает и постраничной, а получатель
            # обязан уметь различать роды по одному и тому же событию. Здесь
            # расходятся не страницы, а строки.
            "pages_fetched": 1,
            "pages_requested": 1,
            "rows_total": rows_total,
            "rows_accepted": rows_accepted,
            "reason_code": reason,
        },
    )


def loss(account_id: str, observed_at: datetime, *, lost: int, reason: str) -> Event:
    """Собирает событие о потерянных событиях.

    Очередь дочитывания переписок ограничена: спецификация объявляет предел, и
    предел этот нужен - очередь пополняется на каждом изменении диалога, а
    вычерпывается по несколько штук за шаг. У продавца с полусотней активных
    переписок она растёт быстрее, чем убывает.

    Но ограничить очередь и молча выбросить лишнее - худший исход из возможных:
    сообщение покупателя не будет прочитано никогда, и узнать об этом неоткуда.
    Поэтому выброшенное объявляется вслух.

    Само это событие не выбрасывается никогда: выбросить сообщение о потере
    значит потерять и сам факт потери.

    Args:
        account_id (str): Идентификатор аккаунта, он же наблюдения.
        observed_at (datetime): Момент наблюдения.
        lost (int): Сколько диалогов выпало из очереди.
        reason (str): Машиночитаемая причина потери.

    Returns:
        Event: Событие event.loss.
    """
    return make_event(
        account_id=account_id,
        event_type=_LOSS,
        entity_id=account_id,
        # Версия - причина и число: две потери подряд по одной причине с разным
        # числом - разные новости, а с одинаковым - одна и та же.
        revision=f"{reason}{_PART_SEP}{lost}",
        observed_at=observed_at,
        key_field="account_id",
        payload={
            "ordering_key": ORDERING_KEY[_LOSS].format(account_id=account_id),
            "lost": lost,
            "reason_code": reason,
        },
    )
