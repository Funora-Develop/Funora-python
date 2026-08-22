"""Проверяет состояние доступа к площадке и ступени реакции на ограничение.

Шесть состояний были перечислены схемой события protocol.health_changed и не
связаны с классами ответа нигде: три файла спецификации ссылались на них по
именам, а откуда состояние берётся, не говорил ни один. Само событие при этом не
порождалось - оно было одним из девяти объявленных и молчащих.

От состояния зависит, приостановлена ли автоматика записи. Две реализации,
объявляющие клиента ограниченным в разные моменты, - это одна остановила
поднятие лотов, а другая нет.
"""

from __future__ import annotations

from funora._budget import Budget
from funora._identity import Identity
from funora.budget import RequestClass
from funora.response_classes import (
    HEALTH_BY_VERDICT,
    INITIAL_HEALTH,
    RESPONSE_CLASSES,
    WRITES_PAUSED_IN,
    Health,
)

#: Момент отсчёта. Любой: важны только разности.
NOW = 20_000.0


def test_every_response_class_says_where_it_leads() -> None:
    """Проверяет, что переход объявлен для каждого класса ответа.

    Класс, о котором таблица молчит, реализация истолкует сама - и две
    реализации объявят аккаунт ограниченным в разные моменты.

    Returns:
        None
    """
    missing = sorted(RESPONSE_CLASSES - set(HEALTH_BY_VERDICT))
    assert not missing, f"классы ответа {missing} не говорят, куда переводят"


def test_success_returns_to_the_initial_state() -> None:
    """Проверяет, что успешный ответ снимает подозрение.

    Без возврата первое же затруднение остановило бы автоматику записи
    навсегда.

    Returns:
        None
    """
    assert HEALTH_BY_VERDICT["ok"] is INITIAL_HEALTH


def test_transport_trouble_does_not_change_the_state() -> None:
    """Проверяет, что дорога не меняет отношения площадки к аккаунту.

    Сетевой отказ и неопознанный ответ говорят о нас и о дороге. Менять по ним
    состояние доступа значило бы объявлять аккаунт ограниченным из-за
    оборванного соединения.

    Returns:
        None
    """
    assert HEALTH_BY_VERDICT["transport_error"] is None
    assert HEALTH_BY_VERDICT["unknown"] is None


def test_writes_are_allowed_only_in_the_initial_state() -> None:
    """Проверяет, что запись разрешена ровно в одном состоянии.

    Returns:
        None
    """
    allowed = set(Health) - WRITES_PAUSED_IN
    assert allowed == {INITIAL_HEALTH}, (
        f"запись разрешена в {sorted(x.value for x in allowed)}"
    )


def _limited(times: int) -> Identity:
    """Создаёт идентичность, получившую ограничение названное число раз.

    Args:
        times (int): Сколько ограничений подряд в одном окне.

    Returns:
        Identity: Идентичность после ограничений.
    """
    identity = Identity(name="проба@funpay.com", budget=Budget())
    for step in range(times):
        identity.note_limit(NOW + step)
    return identity


def test_first_limit_leaves_everyone_in_the_queue() -> None:
    """Проверяет, что первое ограничение никого не снимает с очереди.

    Первая ступень режет ёмкость и даёт остыть. Снимать классы уже на ней
    значило бы останавливать наблюдение из-за одного ответа площадки.

    Returns:
        None
    """
    identity = _limited(1)
    for request_class in RequestClass:
        assert not identity.budget.is_suspended(request_class, NOW), (
            f"класс {request_class.value} снят уже на первом ограничении"
        )


def test_second_limit_removes_monitoring_and_automation() -> None:
    """Проверяет вторую ступень: снимаются наблюдение и автоматика.

    Остаются interactive и poll - то, без чего клиент перестаёт быть клиентом.
    Ступень выражается через классы запросов и потому была невыполнима, пока
    классов не было: она так и стояла объявленной и не сделанной.

    Returns:
        None
    """
    identity = _limited(2)

    assert identity.budget.is_suspended(RequestClass.MONITORING, NOW)
    assert identity.budget.is_suspended(RequestClass.AUTOMATION, NOW)
    assert not identity.budget.is_suspended(RequestClass.INTERACTIVE, NOW)
    assert not identity.budget.is_suspended(RequestClass.POLL, NOW)


def test_suspension_lasts_as_long_as_the_cooldown() -> None:
    """Проверяет, что снятие держится до конца остывания и не дольше.

    Снятие, истекающее раньше остывания, означало бы возврат к прежнему темпу
    без ожидания. Держащееся дольше - наказание, которого никто не объявлял.

    Returns:
        None
    """
    identity = _limited(2)
    just_before = identity.cooldown_until - 0.001
    just_after = identity.cooldown_until + 0.001

    assert identity.budget.is_suspended(RequestClass.MONITORING, just_before)
    assert not identity.budget.is_suspended(RequestClass.MONITORING, just_after)


def test_suspended_class_does_not_pass_even_with_a_full_bucket() -> None:
    """Проверяет, что снятый класс не проходит, сколько бы ни было в ведре.

    Иначе снятие означало бы «подожди токен», а не «подожди остывания», - и на
    полном ведре не значило бы ничего.

    Returns:
        None
    """
    identity = _limited(2)
    reservation = identity.budget.reserve(NOW, 1.0, RequestClass.MONITORING)

    assert not reservation.granted
    assert reservation.bucket == "suspended", (
        f"отказ пришёл от ведра {reservation.bucket!r}, а не от снятия с очереди"
    )


def test_cooldown_is_never_shorter_than_the_site_asked() -> None:
    """Проверяет, что просьбу площадки не укорачивают.

    Заголовок Retry-After - это просьба, и ждать меньше просимого значит спорить
    с площадкой без единого довода: она знает свою нагрузку, а мы нет. Ждать
    дольше безопасно всегда.

    Прежде ждали только своё: площадка просила пять минут, клиент отступал на
    минуту и возвращался. Ровно поведение, из-за которого ограничение и
    переходит в блокировку.

    Returns:
        None
    """
    identity = Identity(name="проба@funpay.com", budget=Budget())
    identity.note_limit(NOW, retry_after_ms=300_000)

    assert identity.cooldown_until - NOW >= 300.0, (
        f"площадка просила 300 с, отступили на {identity.cooldown_until - NOW:.0f}"
    )


def test_own_cooldown_wins_when_the_site_asks_for_less() -> None:
    """Проверяет обратную половину: короткая просьба не укорачивает своё.

    Иначе площадка, попросившая секунду, отменяла бы собственное отступление
    клиента - и вторая ступень реакции никогда бы не наступила.

    Returns:
        None
    """
    identity = Identity(name="проба@funpay.com", budget=Budget())
    identity.note_limit(NOW, retry_after_ms=1_000)

    assert identity.cooldown_until - NOW >= 60.0, (
        "своё остывание укоротилось до просьбы площадки"
    )


def test_a_policy_without_the_flag_does_not_hold_the_identity() -> None:
    """Проверяет, что общим отступление делает признак, а не любая ошибка.

    Признак account_scoped стоит у ограничения частоты и не стоит у прочих:
    сетевой отказ одного запроса не повод останавливать весь аккаунт.

    Returns:
        None
    """
    from funora.retry import RETRY_POLICIES

    scoped = {name for name, policy in RETRY_POLICIES.items() if policy.account_scoped}
    assert scoped == {"funora.transport.rate_limited"}, (
        f"общим объявлено отступление по {sorted(scoped)}"
    )
