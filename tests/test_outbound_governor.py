"""Проверки ограничителя исходящих сообщений.

Отсутствие метода массовой рассылки не мешает написать цикл в пять строк, а
наказание по пункту 1.9 публичных правил площадки получает продавец. Ограничитель
- единственное, что стоит между тем и другим, и до 29.08.2026 его не было вовсе:
раздел контракта не читал ни кодогенератор, ни один тест.

Часы приходят СНАРУЖИ. Ограничитель, читающий их сам, нельзя ни проверить, ни
повторить: первая редакция так и сделала, и на пробе выяснилось, что переданное
время она попросту игнорирует.
"""

from __future__ import annotations

from typing import Any, Final

from funora._outbound import LIMIT_ORDER, OutboundGovernor
from funora.budget import (
    COLD_OUTREACH_QUOTA_PER_HOUR,
    COLD_OUTREACH_WINDOW_MS,
    OUTBOUND_MESSAGES_PER_HOUR,
    OUTBOUND_MIN_INTERVAL_PER_CHAT_MS,
    OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR,
    OUTBOUND_WARMING_EVENTS,
    OUTBOUND_WINDOW_MS,
)

#: Начало отсчёта по стенным часам.
WALL: Final[int] = 1_700_000_000_000

#: Начало отсчёта по монотонным.
MONO: Final[float] = 1000.0


def _at(ms: int) -> dict[str, Any]:
    """Возвращает пару часов, сдвинутую на столько-то миллисекунд.

    Аргументы:
        ms (int): сдвиг от начала отсчёта.

    Возвращает:
        dict[str, Any]: именованные аргументы now_ms и now_s.
    """
    return {"now_ms": WALL + ms, "now_s": MONO + ms / 1000}


def _warm(governor: OutboundGovernor, chat: str) -> None:
    """Греет переписку наблюдённым входящим сообщением.

    Аргументы:
        governor (OutboundGovernor): ограничитель.
        chat (str): переписка.
    """
    governor.note_incoming(chat, at_ms=WALL - 1000)


def test_a_cold_dialogue_refuses_without_an_explicit_flag() -> None:
    """Требует явного признака у обращения в холодную переписку.

    Переписка считается ХОЛОДНОЙ, пока не доказано обратное: тепло требует
    положительного свидетельства - наблюдённого входящего сообщения, - а не
    отсутствия свидетельства холода.

    Ошибка стоит по-разному в две стороны: лишний отказ - неудобство, лишняя
    отправка - наказание продавцу.

    Возвращает:
        None
    """
    governor = OutboundGovernor()

    refusal = governor.check("новый", **_at(0))
    assert refusal is not None
    assert refusal.limit == "cold_outreach_not_declared"
    assert refusal.retry_after_ms == 0, "ожидание тут не поможет, признак ставит вызывающий"

    assert governor.check("новый", **_at(0), declared_cold=True) is None


def test_only_an_incoming_message_makes_a_dialogue_warm() -> None:
    """Требует греть переписку только входящим сообщением.

    Счётчик непрочитанного греть не должен: он меняется и от НАШЕЙ отправки.
    Ограничитель, гревшийся бы на нём, отменял бы сам себя - первая отправка в
    холодную переписку делала бы её тёплой, и квота холодных не сработала бы ни
    разу.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    governor.record("свой", now_ms=WALL, now_s=MONO)

    assert governor.is_warm("свой", now_ms=WALL + 1000) is False, (
        "своя же отправка согрела переписку: ограничитель отменяет сам себя"
    )

    _warm(governor, "свой")
    assert governor.is_warm("свой", now_ms=WALL + 1000) is True


def test_warmth_expires_with_its_window() -> None:
    """Требует, чтобы тепло кончалось вместе с окном.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    governor.note_incoming("давний", at_ms=WALL)

    assert governor.is_warm("давний", now_ms=WALL + COLD_OUTREACH_WINDOW_MS - 1) is True
    assert governor.is_warm("давний", now_ms=WALL + COLD_OUTREACH_WINDOW_MS) is False


def test_the_pause_between_two_messages_to_one_dialogue() -> None:
    """Требует выдерживать паузу на отдельную переписку.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    _warm(governor, "он")
    governor.record("он", now_ms=WALL, now_s=MONO)

    refusal = governor.check("он", **_at(OUTBOUND_MIN_INTERVAL_PER_CHAT_MS - 1000))
    assert refusal is not None
    assert refusal.limit == "min_interval_per_chat"
    assert refusal.retry_after_ms == 1000, refusal.retry_after_ms

    assert governor.check("он", **_at(OUTBOUND_MIN_INTERVAL_PER_CHAT_MS)) is None


def test_the_hourly_message_count() -> None:
    """Требует упираться в число сообщений за час.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    for index in range(OUTBOUND_MESSAGES_PER_HOUR):
        chat = f"тёплый{index % OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR}"
        _warm(governor, chat)
        governor.record(chat, now_ms=WALL + index, now_s=MONO + index / 1000)

    # Отодвигаемся ЗА паузу на переписку: иначе назовётся она, и назовётся
    # верно - она объявлена раньше в порядке.
    refusal = governor.check("тёплый0", **_at(OUTBOUND_MIN_INTERVAL_PER_CHAT_MS * 2))
    assert refusal is not None
    assert refusal.limit == "messages_per_hour"
    assert refusal.retry_after_ms > 0, "предел освободится сам - срок обязан быть назван"


def test_the_hourly_count_of_distinct_recipients() -> None:
    """Требует упираться в число РАЗЛИЧНЫХ адресатов.

    Предел не трогает переписку, которая в окне уже есть: писать тому, кому уже
    писал, - не новый адресат. Иначе предел совпал бы с числом сообщений и не
    значил бы ничего отдельно.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    for index in range(OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR):
        chat = f"адресат{index}"
        _warm(governor, chat)
        governor.record(chat, now_ms=WALL + index, now_s=MONO + index / 1000)

    _warm(governor, "новый")
    refusal = governor.check("новый", **_at(OUTBOUND_MIN_INTERVAL_PER_CHAT_MS * 2))
    assert refusal is not None
    assert refusal.limit == "unique_recipients_per_hour"

    # А прежнему адресату - можно: он в окне уже есть.
    assert governor.check("адресат0", **_at(OUTBOUND_MIN_INTERVAL_PER_CHAT_MS * 2)) is None


def test_the_cold_outreach_quota() -> None:
    """Требует упираться в квоту холодных обращений.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    for index in range(COLD_OUTREACH_QUOTA_PER_HOUR):
        governor.record(f"холодный{index}", now_ms=WALL + index, now_s=MONO + index / 1000)

    refusal = governor.check("ещё один", **_at(1000), declared_cold=True)
    assert refusal is not None
    assert refusal.limit == "cold_outreach_quota"

    # Тёплому при этом можно: квота только о холодных.
    _warm(governor, "тёплый")
    assert governor.check("тёплый", **_at(1000)) is None


def test_the_limits_are_named_in_the_declared_order() -> None:
    """Требует называть упёршийся предел в объявленном порядке.

    На решении порядок не сказывается - отказ есть отказ, - а на ИМЕНИ
    сказывается, и имя есть часть ответа: по нему вызывающий решает, ждать
    полминуты или не писать до завтра.

    Проверка ставит попытку, упирающуюся В ДВА предела сразу, и требует имени
    того, что объявлен раньше.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    for index in range(OUTBOUND_MESSAGES_PER_HOUR):
        chat = f"тёплый{index % OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR}"
        _warm(governor, chat)
        governor.record(chat, now_ms=WALL + index, now_s=MONO + index / 1000)

    # Упирается и в паузу на переписку, и в часовое число сразу.
    refusal = governor.check("тёплый0", **_at(1000))
    assert refusal is not None
    assert refusal.limit == "min_interval_per_chat", (
        f"назван предел {refusal.limit}, а раньше него в порядке стоит "
        f"min_interval_per_chat: {LIMIT_ORDER}"
    )


def test_without_a_durable_ledger_the_governor_refuses() -> None:
    """Требует отказывать без долговечного реестра.

    Пределы часовые, а память обнуляется перезапуском. Тридцать сообщений в час
    превратились бы в тридцать НА ЗАПУСК, то есть защита снималась бы
    перезапуском процесса.

    Возвращает:
        None
    """
    governor = OutboundGovernor(durable=False)
    _warm(governor, "он")

    refusal = governor.check("он", **_at(0))
    assert refusal is not None
    assert refusal.limit == "no_durable_ledger"


def test_the_ledger_survives_a_restart() -> None:
    """Требует, чтобы записи пережили перезапуск.

    Монотонная метка наружу НЕ уходит: отсчёт её свой в каждом запуске, и
    сохранённая означала бы не то, что значила при записи.

    Возвращает:
        None
    """
    before = OutboundGovernor()
    _warm(before, "он")
    before.record("он", now_ms=WALL, now_s=MONO)

    saved = before.snapshot()
    assert "monotonic" not in repr(saved), "монотонная метка ушла в файл состояния"

    # Новый запуск: монотонные часы отсчитываются заново, с нуля.
    after = OutboundGovernor()
    after.restore(saved)

    refusal = after.check("он", now_ms=WALL + 1000, now_s=0.5)
    assert refusal is not None, "после перезапуска пауза на переписку обнулилась"
    assert refusal.limit == "min_interval_per_chat"

    # Тепло тоже пережило.
    assert after.is_warm("он", now_ms=WALL + 1000) is True


def test_a_clock_moved_backwards_does_not_reset_the_quota() -> None:
    """Требует не обнулять квоту переведёнными назад часами.

    Запись с меткой БОЛЬШЕ текущего момента остаётся в окне, а не выбрасывается
    как просроченная. Направление ограничительное.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    governor.restore(
        {
            "sent": [{"chat_id": "он", "at_ms": WALL + 600_000, "cold": False}],
            "incoming": {"он": WALL - 1000},
        }
    )

    refusal = governor.check("он", now_ms=WALL, now_s=MONO)
    assert refusal is not None, "часы, подведённые назад, обнулили паузу на переписку"
    assert refusal.limit == "min_interval_per_chat"

    # Срок освобождения не может превышать сам предел. Превысил - значит возраст
    # записи посчитан отрицательным, и над бессмыслицей сделана арифметика:
    # вызывающему назвали бы десять минут ожидания там, где предел полминуты.
    assert 0 < refusal.retry_after_ms <= OUTBOUND_MIN_INTERVAL_PER_CHAT_MS, (
        f"названо {refusal.retry_after_ms} мс при пределе {OUTBOUND_MIN_INTERVAL_PER_CHAT_MS} мс"
    )


def test_a_clock_moved_forward_inside_one_run_does_not_reset_the_quota() -> None:
    """Требует держать квоту при переводе часов вперёд во время работы.

    Пока процесс работает, у каждой записи есть монотонная метка, и считается по
    ней. Монотонные часы не прыгают от синхронизации времени.

    После перезапуска второй метки нет - и это объявлено в контракте честной
    половиной, а не закрытой дырой.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    _warm(governor, "он")
    governor.record("он", now_ms=WALL, now_s=MONO)

    # Стенные часы прыгнули на час вперёд, монотонные - на секунду.
    refusal = governor.check("он", now_ms=WALL + OUTBOUND_WINDOW_MS, now_s=MONO + 1)
    assert refusal is not None, "перевод стенных часов вперёд обнулил паузу на переписку"
    assert refusal.limit == "min_interval_per_chat"


def test_expired_records_are_forgotten() -> None:
    """Требует выбрасывать записи, вышедшие из самого широкого окна.

    Реестр иначе растёт без предела: он переживает перезапуск, а значит живёт
    столько же, сколько аккаунт.

    Возвращает:
        None
    """
    governor = OutboundGovernor()
    governor.restore(
        {
            "sent": [{"chat_id": "давний", "at_ms": WALL, "cold": True}],
            "incoming": {"давний": WALL},
        }
    )

    widest = max(OUTBOUND_WINDOW_MS, COLD_OUTREACH_WINDOW_MS)
    governor.forget_expired(now_ms=WALL + widest, now_s=MONO)

    assert governor.snapshot()["sent"] == []
    assert governor.snapshot()["incoming"] == {}


def test_only_the_declared_event_kinds_warm_a_dialogue() -> None:
    """Требует брать греющие виды событий ИЗ КОНТРАКТА, а не из литерала.

    Перечень закрыт и состоит сегодня из одного вида. Счётчик непрочитанного в
    него не входит нарочно: он меняется и от нашей отправки.

    Возвращает:
        None
    """
    governor = OutboundGovernor()

    assert governor.note_event("chat.unread_changed", "он", at_ms=WALL) is False
    assert governor.is_warm("он", now_ms=WALL + 1000) is False, (
        "счётчик непрочитанного согрел переписку: ограничитель отменяет сам себя"
    )

    for kind in OUTBOUND_WARMING_EVENTS:
        fresh = OutboundGovernor()
        assert fresh.note_event(kind, "он", at_ms=WALL) is True, kind
        assert fresh.is_warm("он", now_ms=WALL + 1000) is True, kind
