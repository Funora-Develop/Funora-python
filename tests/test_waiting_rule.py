"""Проверяет правило ожидания: округление паузы и ветку отказа.

Спецификация объявляет его в spec/runtime/budget.yaml разделом waiting: две
попытки и одна сторожевая миллисекунда. Область действия там же перечнем:
ВСЯКАЯ пауза, вычисленная из монотонных секунд и выданная целыми
миллисекундами - ожидание запаса, ожидание права на залп и ожидание конца
остывания.

Перечень заведён после того, как выяснилось, что правило было сформулировано
под один случай, а два других применяли то же округление литералом, то есть по
совпадению. Правка объявленного числа обошла бы их стороной, и половина пауз
поехала бы, а половина нет.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from funora._budget import Budget, TokenBucket
from funora._engine import Engine, Pause
from funora._identity import Identity
from funora._transport import TransportSettings
from funora.budget import BUCKETS, WAIT_ATTEMPTS, WAIT_GUARD_MS, RequestClass
from funora.errors import BudgetExhaustedError

#: Где лежит пакет.
PACKAGE = Path(__file__).resolve().parent.parent / "src" / "funora"

#: Как выглядит перевод монотонных секунд в целые миллисекунды.
ROUNDING = re.compile(r"int\(\s*\(.*?\)\s*\*\s*1000\s*\)")


def test_every_pause_rounds_by_the_declared_guard() -> None:
    """Запрещает округление паузы литералом.

    Сторожевая миллисекунда объявлена числом спецификации. Место, применившее
    то же округление литералом, выполняет правило по совпадению: правка
    объявленного числа обойдёт его стороной, и половина пауз поедет, а половина
    нет. Ровно так и было в двух местах из трёх.

    Returns:
        None
    """
    offenders: list[str] = []
    for path in (PACKAGE / "_budget.py", PACKAGE / "_engine.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not ROUNDING.search(line):
                continue
            tail = " ".join(lines[number - 1 : number + 1])
            if "WAIT_GUARD_MS" not in tail:
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, (
        "пауза округляется литералом вместо объявленной величины:\n  "
        + "\n  ".join(offenders)
    )


def test_the_suspended_class_resumes_past_the_moment() -> None:
    """Проверяет, что пауза снятого класса кончается ПОСЛЕ конца снятия.

    Величина выбрана так, что целая часть теряет дробь: две секунды и семь
    десятых миллисекунды. Округление вровень дало бы ровно две тысячи
    миллисекунд, вызывающий вернулся бы на четыре десятых миллисекунды раньше
    конца снятия и получил бы отказ, прождав всё положенное.

    Returns:
        None
    """
    budget = Budget()
    now = 100.0
    until = now + 2.0007
    budget.suspend((RequestClass.MONITORING,), until=until)

    reservation = budget.reserve(now, 1.0, RequestClass.MONITORING)
    assert not reservation.granted
    assert reservation.bucket == "suspended"

    resumed = now + reservation.wait_ms / 1000
    assert not budget.is_suspended(RequestClass.MONITORING, resumed), (
        f"пауза {reservation.wait_ms} мс кончилась раньше конца снятия: "
        f"вернулись в {resumed}, а снятие держится до {until}"
    )


def test_the_cooldown_pause_resumes_past_the_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет то же для остывания идентичности.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены часов.

    Returns:
        None
    """
    clock = [500.0]
    monkeypatch.setattr("funora._engine.monotonic", lambda: clock[0])

    identity = Identity(name="проба@funpay.com")
    identity.cooldown_until = clock[0] + 1.5004
    engine = Engine(TransportSettings(), identity.budget, frozenset(), identity)

    steps = engine.wait_out_cooldown()
    request = next(steps)
    assert isinstance(request, Pause)

    resumed = clock[0] + request.ms / 1000
    assert not identity.is_cooling(resumed), (
        f"пауза {request.ms} мс кончилась раньше конца остывания: вернулись в "
        f"{resumed}, а остывание держится до {identity.cooldown_until}"
    )


def test_the_token_wait_resumes_past_the_moment() -> None:
    """Проверяет то же для ожидания запаса.

    Ведро аккаунта пополняется по половине токена в секунду, и нужная доля
    выбрана так, чтобы деление не легло на целую миллисекунду.

    Returns:
        None
    """
    bucket = TokenBucket(BUCKETS["account"], tokens=0.4993, updated_at=0.0)
    wait = bucket.wait_for(0.0, cost=1.0)
    assert wait > 0

    bucket.wait_for(wait / 1000)
    assert bucket.tokens >= 1.0, (
        f"после паузы {wait} мс запаса всё ещё нет: {bucket.tokens}. Вызывающий "
        "получит отказ, прождав всё положенное"
    )


def test_the_refusal_branch_of_spend_budget_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Доводит spend_budget до отказа при ЧЕСТНЫХ часах.

    Ветка отказа - живой путь, на котором принимается решение не отправлять
    запрос. Проверить её замороженными часами нельзя: при остановленных часах
    ведро не пополняется никогда, и отказ приходит по причине, которой в жизни
    не бывает. Стоит отсыпать паузу честно - и запрос уходит.

    Поэтому здесь ждут честно, а бюджет за время паузы забирает СОСЕД по той же
    сетевой идентичности. Это и есть настоящая причина отказа: бюджет
    принадлежит идентичности, а не клиенту, и пополнения может не достаться.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены часов.

    Returns:
        None
    """
    clock = [1000.0]
    monkeypatch.setattr("funora._engine.monotonic", lambda: clock[0])

    identity = Identity(name="проба@funpay.com")
    budget = identity.budget

    # Осушается право на залп, а не запас: пауза тогда короткая и осмысленная.
    for _ in range(BUCKETS["account"].burst):
        assert budget.reserve(clock[0], 1.0, RequestClass.INTERACTIVE).granted

    engine = Engine(TransportSettings(), budget, frozenset(), identity)
    steps = engine.spend_budget(RequestClass.INTERACTIVE)

    request = next(steps)
    assert isinstance(request, Pause), f"ядро не попросило паузы, а вернуло {request}"
    assert WAIT_ATTEMPTS == 2, "проверка написана под две попытки"

    # Часы идут честно на всю паузу, и ровно в этот момент сосед по
    # идентичности забирает пополнение.
    clock[0] += request.ms / 1000
    taken = budget.reserve(clock[0], 1.0, RequestClass.INTERACTIVE)
    assert taken.granted, "сосед не смог занять: проверка проверяет не то"

    with pytest.raises(BudgetExhaustedError) as caught:
        steps.send(None)

    assert str(request.ms) in str(caught.value), (
        "текст ошибки не называет, сколько прождали. Разбирающему отказ придётся "
        f"искать это заново: {caught.value}"
    )


def test_the_guard_is_one_millisecond_and_that_is_declared() -> None:
    """Связывает проверки выше с объявленным числом.

    Проверки выбирают дроби под сторожевую величину в одну миллисекунду. Если
    величину объявят другой, они станут проверять не то, о чём написаны, и
    молчать об этом нельзя.

    Returns:
        None
    """
    assert WAIT_GUARD_MS == 1, (
        f"сторожевая величина объявлена {WAIT_GUARD_MS}, а проверки этого файла "
        "написаны под одну миллисекунду: дроби в них подобраны под неё"
    )
