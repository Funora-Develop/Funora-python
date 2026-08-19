"""Проверки расписания опроса и гашения повторов.

Времени здесь нет: момент приходит числом. Расписание, смотрящее на часы,
проверялось бы настоящими минутами, и такая проверка живёт до первого раза,
когда мешает.

Числа расписания не закрепляются: они взяты из спецификации и помечены там
провизорными. Проверяются свойства - что интервал растёт, что не опускается ниже
предела, что событие его сбрасывает.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from funora._diff import Event
from funora._poll import UNSAFE_FLOOR_MARK, Deduplicator, Schedule
from funora.budget import SCHEDULING
from funora.events import EventType


def _event(event_id: str, key: str = "chat:1") -> Event:
    """Собирает событие для проверок.

    Args:
        event_id (str): Идентификатор события.
        key (str): Ключ упорядочивания.

    Returns:
        Event: Событие.
    """
    return Event(
        id=event_id,
        type=EventType.MESSAGE_CREATED,
        ordering_key=key,
        entity_id="1",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
        origin="structural",
        payload={},
    )


def test_idle_interval_grows() -> None:
    """Проверяет рост интервала в покое.

    Опрашивать площадку с прежней частотой, когда ничего не происходит, - это
    трата чужого ресурса и своего бюджета одновременно.

    Returns:
        None
    """
    schedule = Schedule()
    intervals = [schedule.note((), float(step)) for step in range(5)]

    assert intervals == sorted(intervals)
    assert intervals[0] < intervals[-1]


def test_interval_never_exceeds_the_cap() -> None:
    """Проверяет потолок интервала.

    Без потолка достаточно суток простоя, чтобы клиент замолчал навсегда.

    Returns:
        None
    """
    schedule = Schedule()
    for step in range(100):
        interval = schedule.note((), float(step))
    assert interval <= SCHEDULING.max_interval_ms


def test_event_resets_the_interval() -> None:
    """Проверяет сброс интервала при событии данных.

    Returns:
        None
    """
    schedule = Schedule()
    for step in range(10):
        schedule.note((), float(step))
    assert schedule.interval_ms > SCHEDULING.active_interval_ms

    assert schedule.note((_event("a"),), 20.0) == SCHEDULING.active_interval_ms


def test_interval_holds_inside_the_activity_window() -> None:
    """Проверяет, что внутри окна активности интервал не растёт.

    Событие только что было, и следующее вероятно рядом. Растить интервал здесь
    значило бы узнавать о втором сообщении переписки позже, чем о первом.

    Returns:
        None
    """
    schedule = Schedule()
    schedule.note((_event("a"),), 0.0)

    inside = SCHEDULING.activity_window_ms / 1000 / 2
    assert schedule.note((), inside) == SCHEDULING.active_interval_ms
    assert schedule.is_active(inside)


def test_activity_expires_with_the_window() -> None:
    """Проверяет, что активность заканчивается вместе с окном.

    Returns:
        None
    """
    schedule = Schedule()
    schedule.note((_event("a"),), 0.0)

    outside = SCHEDULING.activity_window_ms / 1000 + 1
    assert not schedule.is_active(outside)
    assert schedule.note((), outside) > SCHEDULING.active_interval_ms


def test_interval_never_drops_below_the_floor() -> None:
    """Проверяет нижний предел интервала.

    Это единственное число, которое защищает площадку от слишком уверенного
    пользователя, а аккаунт пользователя - от него самого.

    Returns:
        None
    """
    schedule = Schedule()
    assert schedule.interval_ms >= SCHEDULING.min_floor_ms
    assert schedule.note((_event("a"),), 0.0) >= SCHEDULING.min_floor_ms


def test_lowering_the_floor_marks_itself() -> None:
    """Проверяет, что понижение предела видно снаружи.

    Понизить предел можно, и это осознанное решение вызывающего. Но при разборе
    блокировки должно быть видно, что опрос шёл чаще, чем позволяет
    спецификация, - иначе причину будут искать где угодно, кроме настоящей.

    Returns:
        None
    """
    schedule = Schedule(unsafe_floor_ms=500)
    assert UNSAFE_FLOOR_MARK in schedule.marks
    assert schedule.floor_ms == 500

    assert not Schedule().marks


def test_duplicate_event_is_suppressed() -> None:
    """Проверяет гашение повтора.

    Гарантия доставки - не менее одного раза, поэтому повторы неизбежны и
    гасить их обязан получатель.

    Returns:
        None
    """
    dedup = Deduplicator()
    fresh = dedup.filter((_event("a"),), 0.0)
    assert len(fresh) == 1
    dedup.commit(fresh, 0.0)

    assert len(dedup.filter((_event("a"),), 1.0)) == 0
    assert dedup.suppressed == 1


def test_duplicates_inside_one_batch_are_suppressed() -> None:
    """Проверяет гашение повтора внутри одного набора.

    Returns:
        None
    """
    dedup = Deduplicator()
    fresh = dedup.filter((_event("a"), _event("a"), _event("b")), 0.0)
    assert len(fresh) == 2
    assert dedup.suppressed == 1


def test_dedup_is_scoped_to_the_ordering_key() -> None:
    """Проверяет, что гашение работает в пределах ключа.

    Глобальный кэш склеивал бы события разных диалогов при совпадении отпечатка,
    и второе событие просто не приходило бы - без следа и без объяснения.

    Returns:
        None
    """
    dedup = Deduplicator()
    assert len(dedup.filter((_event("a", "chat:1"),), 0.0)) == 1
    assert len(dedup.filter((_event("a", "chat:2"),), 0.0)) == 1
    assert dedup.suppressed == 0


def test_records_expire() -> None:
    """Проверяет истечение срока записи.

    Returns:
        None
    """
    dedup = Deduplicator(ttl_ms=1000)
    dedup.commit(dedup.filter((_event("a"),), 0.0), 0.0)

    assert len(dedup.filter((_event("a"),), 0.5)) == 0
    assert len(dedup.filter((_event("a"),), 2.0)) == 1


def test_bucket_is_bounded() -> None:
    """Проверяет предел числа записей на ключ.

    Без предела кэш растёт вместе с числом сообщений в переписке, и процесс,
    работающий неделю, теряет память на дедупликации.

    Returns:
        None
    """
    dedup = Deduplicator(entries_per_key=4)
    for index in range(20):
        event = (_event(f"e{index}"),)
        dedup.commit(dedup.filter(event, float(index)), float(index))

    assert len(dedup.filter((_event("e0"),), 100.0)) == 1, (
        "самая старая запись обязана быть вытеснена"
    )
    assert len(dedup.filter((_event("e19"),), 100.0)) == 0, "самая новая запись обязана сохраниться"


def test_suppressed_counter_is_observable() -> None:
    """Проверяет наблюдаемость числа погашенных повторов.

    Ложное гашение - когда два разных события схлопнулись в одно - иначе
    невидимо: событие просто не приходит, и найти причину не по чему.

    Returns:
        None
    """
    dedup = Deduplicator()
    assert dedup.suppressed == 0
    dedup.filter((_event("a"), _event("a")), 0.0)
    assert dedup.suppressed == 1


@pytest.mark.parametrize("count", [0, 1, 5])
def test_empty_batch_is_handled(count: int) -> None:
    """Проверяет работу с пустым и непустым набором.

    Args:
        count (int): Сколько событий в наборе.

    Returns:
        None
    """
    dedup = Deduplicator()
    events = tuple(_event(f"e{i}") for i in range(count))
    assert len(dedup.filter(events, 0.0)) == count


def test_nothing_is_remembered_until_committed() -> None:
    """Проверяет, что проверка ничего не запоминает.

    Это исправление настоящего дефекта, а не украшение. Записав событие в момент
    проверки, мы объявляли бы его доставленным до того, как обработчик его
    увидел. Обработчик падает - база не сдвигается, событие приходит снова и
    гасится как повтор. Оно исчезает навсегда, причём именно то, которое
    обработчику не далось.

    Returns:
        None
    """
    dedup = Deduplicator()
    assert len(dedup.filter((_event("a"),), 0.0)) == 1
    assert len(dedup.filter((_event("a"),), 1.0)) == 1, "непринятое событие обязано прийти снова"
    assert dedup.suppressed == 0


def test_committed_event_is_suppressed_afterwards() -> None:
    """Проверяет, что доставленное событие гасится при повторе.

    Returns:
        None
    """
    dedup = Deduplicator()
    fresh = dedup.filter((_event("a"),), 0.0)
    dedup.commit(fresh, 0.0)
    assert len(dedup.filter((_event("a"),), 1.0)) == 0
