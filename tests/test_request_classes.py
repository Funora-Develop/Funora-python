"""Проверяет правило допуска по классу запроса.

Классы объявлены спецификацией давно, а до бюджета не доходили вовсе: класс
стоял у каждой операции и не читался никем. Следствие названо в реестре
неисполненного прямо - собственный мониторинг продавца вытеснял ответы
покупателям на общих основаниях, ровно то, ради чего доли и придуманы.

Доля - это ПОЛ, а не потолок. Она не ограничивает класс сверху: пока никто не
претендует, любой класс вправе израсходовать ведро целиком. Она обещает другое -
что менее защищённый не съест последнюю долю более защищённого.
"""

from __future__ import annotations

import pytest

from funora._budget import Budget
from funora.budget import BUCKETS, DEMAND_WINDOW_MS, FLOOR_SHARE, ON_REFUSAL, RequestClass
from funora.errors import BudgetExhaustedError

#: Ведро аккаунта - самое узкое, на нём правило и проявляется.
ACCOUNT = BUCKETS["account"]

#: Момент отсчёта. Любой: важны только разности.
NOW = 10_000.0


def _drain(budget: Budget, now: float, down_to: float) -> None:
    """Тратит бюджет от лица покупательских вызовов.

    Args:
        budget (Budget): Бюджет.
        now (float): Момент.
        down_to (float): До какого запаса опустошать ведро аккаунта.

    Returns:
        None
    """
    while budget.reserve(now, 1.0, RequestClass.INTERACTIVE).granted:
        if budget._buckets[1].tokens <= down_to:
            return


def test_monitoring_takes_the_whole_bucket_when_nobody_competes() -> None:
    """Проверяет, что доля не превращается в потолок.

    Вытеснять некого, когда никто не претендует. Запрет наблюдению брать больше
    своей доли на пустой площадке наказывал бы его за чужое бездействие.

    Returns:
        None
    """
    budget = Budget()
    granted = sum(
        1
        for _ in range(int(ACCOUNT.capacity))
        if budget.reserve(NOW, 1.0, RequestClass.MONITORING).granted
    )
    assert granted == int(ACCOUNT.capacity), (
        f"наблюдение получило {granted} из {int(ACCOUNT.capacity)} при том, что "
        "никто больше не претендовал"
    )


def test_monitoring_yields_first_when_buyers_are_answered() -> None:
    """Проверяет, что наблюдение уступает, а ответ покупателю проходит.

    Это главная проверка раздела. Пропущенный цикл наблюдения стоит задержки в
    данных, пропущенный ответ покупателю стоит денег.

    Returns:
        None
    """
    budget = Budget()
    for request_class in (RequestClass.INTERACTIVE, RequestClass.AUTOMATION, RequestClass.POLL):
        budget.reserve(NOW, 1.0, request_class)
    _drain(budget, NOW, ACCOUNT.capacity * 0.30)

    with pytest.raises(BudgetExhaustedError, match="отменяемым"):
        budget.require(NOW, 1.0, RequestClass.MONITORING)

    assert budget.reserve(NOW, 1.0, RequestClass.INTERACTIVE).granted, (
        "ответ покупателю не прошёл там, где ради него всё и написано"
    )


def test_demand_expires() -> None:
    """Проверяет, что спрос стареет.

    Класс, замолчавший надолго, перестаёт занимать долю: иначе один вызов в
    начале суток резервировал бы ёмкость до вечера.

    Проверяется сам порог, а не выданный бюджет. Через окно спроса ведро успело
    бы пополниться, и заём прошёл бы при любом пороге - то есть проверка
    доказывала бы работу часов, а не старение спроса.

    Returns:
        None
    """
    budget = Budget()
    budget.reserve(NOW, 1.0, RequestClass.INTERACTIVE)

    assert budget._floor_for(RequestClass.MONITORING, NOW) > 0.0, (
        "свежий спрос интерактивного класса не поднял порог наблюдению"
    )

    later = NOW + DEMAND_WINDOW_MS / 1000 + 1
    assert budget._floor_for(RequestClass.MONITORING, later) == 0.0, (
        "спрос не состарился: класс, замолчавший на всё окно, продолжает "
        "занимать долю"
    )


def test_floors_sum_to_the_whole_bucket() -> None:
    """Проверяет, что доли делят ёмкость без остатка.

    Недостача означала бы ничью ёмкость, избыток - обещание, которого ведро не
    выполнит.

    Returns:
        None
    """
    assert abs(sum(FLOOR_SHARE.values()) - 1.0) < 1e-9


def test_only_the_cancellable_class_is_refused_outright() -> None:
    """Проверяет, что немедленный отказ достаётся ровно одному классу.

    Отказать можно только тому, кого спецификация объявила отменяемым. Ответ
    покупателю, не отправленный из-за собственного мониторинга продавца, -
    худший исход, какой этот раздел вообще может дать.

    Returns:
        None
    """
    refused = {name for name, mode in ON_REFUSAL.items() if mode == "refuse"}
    assert refused == {RequestClass.MONITORING}, (
        f"немедленный отказ достаётся {sorted(x.value for x in refused)}"
    )


def test_interactive_is_never_the_one_who_yields() -> None:
    """Проверяет, что у самого защищённого класса порога нет вовсе.

    Returns:
        None
    """
    budget = Budget()
    for request_class in RequestClass:
        budget.reserve(NOW, 1.0, request_class)

    assert budget._floor_for(RequestClass.INTERACTIVE, NOW) == 0.0
    assert budget._floor_for(RequestClass.MONITORING, NOW) > 0.0
