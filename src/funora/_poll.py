"""Расписание опроса и гашение повторов.

Оба механизма чистые и времени не знают: момент приходит числом. Расписание,
смотрящее на часы, проверяется настоящими минутами, а такая проверка живёт до
первого раза, когда она мешает.

Расписание отвечает на вопрос «через сколько спросить снова». Оно растёт в покое
и сбрасывается при событии данных. Управляющие события активностью не считаются:
иначе сообщение о деградации само себя продлевало бы, и клиент опрашивал бы
площадку часто именно потому, что у него что-то сломалось.

Нижний предел интервала обычной настройкой не понижается. Это единственное
число, которое защищает площадку от слишком уверенного пользователя, а аккаунт
пользователя - от него самого. Понизить его можно только именованным аргументом
с приставкой unsafe_, и факт понижения виден снаружи.

Гашение повторов работает в пределах ключа упорядочивания, а не глобально.
Глобальный кэш склеивал бы события разных диалогов при совпадении отпечатка, а
разъехавшиеся события двух аккаунтов гасили бы друг друга.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Final

from ._diff import Event
from .budget import SCHEDULING, Scheduling
from .events import DEDUP_TTL_MS

__all__ = ["Schedule", "Deduplicator", "UNSAFE_FLOOR_MARK"]

#: Пометка, которой отмечается понижение нижнего предела интервала.
#:
#: Появляется в состоянии клиента и в журнале, чтобы при разборе блокировки было
#: видно: опрос шёл чаще, чем позволяет спецификация, и это сделали намеренно.
UNSAFE_FLOOR_MARK: Final[str] = "unsafe_interval_floor_lowered"

#: Сколько записей о событиях хранится на один ключ упорядочивания.
_MIN_ENTRIES_PER_KEY: Final[int] = 256


@dataclass
class Schedule:
    """Адаптивный интервал опроса.

    Args:
        numbers (Scheduling): Числа расписания. По умолчанию из спецификации.
        unsafe_floor_ms (int | None): Понижённый нижний предел. Обычной
            настройкой не задаётся: приставка unsafe_ в имени намеренная, а сам
            факт понижения отмечается в :attr:`marks`.
    """

    numbers: Scheduling = SCHEDULING
    unsafe_floor_ms: int | None = None
    marks: set[str] = field(default_factory=set)
    _interval_ms: int = 0
    _last_data_event_at: float | None = None

    def __post_init__(self) -> None:
        """Задаёт начальный интервал и отмечает небезопасное понижение.

        Returns:
            None
        """
        self._interval_ms = self.numbers.active_interval_ms
        if self.unsafe_floor_ms is not None:
            self.marks.add(UNSAFE_FLOOR_MARK)

    @property
    def floor_ms(self) -> int:
        """Возвращает действующий нижний предел интервала.

        Returns:
            int: Предел в миллисекундах.
        """
        if self.unsafe_floor_ms is None:
            return self.numbers.min_floor_ms
        return max(1, self.unsafe_floor_ms)

    @property
    def interval_ms(self) -> int:
        """Возвращает текущий интервал опроса.

        Returns:
            int: Интервал в миллисекундах, не ниже действующего предела.
        """
        return max(self.floor_ms, min(self._interval_ms, self.numbers.max_interval_ms))

    def is_active(self, now: float) -> bool:
        """Сообщает, считается ли аккаунт активным.

        Args:
            now (float): Текущий момент, монотонные секунды.

        Returns:
            bool: True, если событие данных наблюдалось внутри окна активности.
        """
        if self._last_data_event_at is None:
            return False
        elapsed_ms = (now - self._last_data_event_at) * 1000
        return elapsed_ms <= self.numbers.activity_window_ms

    def note(self, events: tuple[Event, ...], now: float) -> int:
        """Учитывает итог опроса и возвращает интервал до следующего.

        Args:
            events (tuple[Event, ...]): События, порождённые этим опросом.
            now (float): Текущий момент, монотонные секунды.

        Returns:
            int: Через сколько миллисекунд спрашивать снова.
        """
        if events:
            self._last_data_event_at = now
            self._interval_ms = self.numbers.active_interval_ms
            return self.interval_ms

        if self.is_active(now):
            # Внутри окна активности интервал не растёт: событие только что
            # было, и следующее вероятно рядом.
            self._interval_ms = self.numbers.active_interval_ms
            return self.interval_ms

        self._interval_ms = min(
            int(self._interval_ms * self.numbers.idle_step_multiplier),
            self.numbers.max_interval_ms,
        )
        return self.interval_ms


class Deduplicator:
    """Гасит повторную выдачу одного и того же события.

    Гарантия доставки - не менее одного раза, поэтому повторы неизбежны и
    гасить их обязан получатель. Здесь это делается в пределах ключа
    упорядочивания: глобальный кэш склеивал бы события разных диалогов при
    совпадении отпечатка.

    Args:
        ttl_ms (int): Сколько хранится запись о доставленном событии.
        entries_per_key (int): Сколько записей хранится на один ключ.
    """

    __slots__ = ("_ttl_ms", "_entries_per_key", "_seen", "_suppressed")

    def __init__(
        self,
        ttl_ms: int = DEDUP_TTL_MS,
        entries_per_key: int = _MIN_ENTRIES_PER_KEY,
    ) -> None:
        self._ttl_ms = ttl_ms
        self._entries_per_key = entries_per_key
        self._seen: dict[str, OrderedDict[str, float]] = {}
        self._suppressed = 0

    @property
    def suppressed(self) -> int:
        """Возвращает число погашенных повторов.

        Величина обязана быть наблюдаемой. Ложное гашение - когда два разных
        события схлопнулись в одно - иначе невидимо: событие просто не приходит,
        и найти причину не по чему.

        Returns:
            int: Сколько событий было погашено за время жизни объекта.
        """
        return self._suppressed

    def filter(self, events: tuple[Event, ...], now: float) -> tuple[Event, ...]:
        """Отбрасывает события, которые уже выдавались.

        Args:
            events (tuple[Event, ...]): События одного опроса.
            now (float): Текущий момент, монотонные секунды.

        Returns:
            tuple[Event, ...]: События, которые вызывающий ещё не видел.
        """
        fresh: list[Event] = []
        for event in events:
            bucket = self._seen.setdefault(event.ordering_key, OrderedDict())
            self._evict(bucket, now)

            if event.id in bucket:
                self._suppressed += 1
                continue

            bucket[event.id] = now
            bucket.move_to_end(event.id)
            while len(bucket) > self._entries_per_key:
                bucket.popitem(last=False)
            fresh.append(event)

        return tuple(fresh)

    def _evict(self, bucket: OrderedDict[str, float], now: float) -> None:
        """Убирает записи, у которых вышел срок.

        Args:
            bucket (OrderedDict[str, float]): Записи одного ключа.
            now (float): Текущий момент, монотонные секунды.

        Returns:
            None
        """
        deadline = now - self._ttl_ms / 1000
        expired = [key for key, stamp in bucket.items() if stamp < deadline]
        for key in expired:
            del bucket[key]
