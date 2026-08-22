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
from funora.budget import (
    BUCKETS,
    BURST_WINDOW_MS,
    DEMAND_WINDOW_MS,
    FLOOR_SHARE,
    ON_REFUSAL,
    RequestClass,
)
from funora.errors import BudgetExhaustedError

#: Ведро аккаунта - самое узкое, на нём правило и проявляется.
ACCOUNT = BUCKETS["account"]

#: Момент отсчёта. Любой: важны только разности.
NOW = 10_000.0


def _drain(budget: Budget, now: float, down_to: float) -> float:
    """Тратит бюджет от лица покупательских вызовов.

    Args:
        budget (Budget): Бюджет.
        now (float): Момент.
        down_to (float): До какого запаса опустошать ведро аккаунта.

    Returns:
        float: Момент, на котором расход закончился. Дальше спрашивать надо
        именно его: часы ведра ушли вперёд, и вопрос из прошлого дал бы
        отрицательный промежуток.
    """
    # Время идёт: залп не пускает больше burst запросов подряд, сколько бы ни
    # было в ведре. Шаг короче окна залпа, поэтому право на залп успевает
    # восстанавливаться, а запас почти нет.
    moment = now
    while budget._buckets[1].tokens > down_to:
        if not budget.reserve(moment, 1.0, RequestClass.INTERACTIVE).granted:
            moment += 0.05
            if moment - now > 60.0:
                raise AssertionError("ведро не опустошается - расход не работает")
    return moment


def test_monitoring_takes_the_whole_bucket_when_nobody_competes() -> None:
    """Проверяет, что доля не превращается в потолок.

    Вытеснять некого, когда никто не претендует. Запрет наблюдению брать больше
    своей доли на пустой площадке наказывал бы его за чужое бездействие.

    Returns:
        None
    """
    budget = Budget()
    granted = 0
    moment = NOW
    while budget._buckets[1].tokens > 0.5:
        if budget.reserve(moment, 1.0, RequestClass.MONITORING).granted:
            granted += 1
        else:
            moment += 0.05
        assert moment - NOW < 60.0, "наблюдение не может исчерпать ведро"

    assert granted >= int(ACCOUNT.capacity), (
        f"наблюдение получило {granted} из {int(ACCOUNT.capacity)} при том, что "
        "никто больше не претендовал. Доля - это пол, а не потолок"
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
    moment = _drain(budget, NOW, ACCOUNT.capacity * 0.30)

    # Даём восстановиться праву на залп: спор идёт о доле, а не о темпе, и
    # мешать одно с другим значило бы проверять два предела одной проверкой.
    moment += BURST_WINDOW_MS / 1000

    with pytest.raises(BudgetExhaustedError, match="отменяемым"):
        budget.require(moment, 1.0, RequestClass.MONITORING)

    assert budget.reserve(moment, 1.0, RequestClass.INTERACTIVE).granted, (
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


def test_redirect_debt_does_not_make_a_stranger_look_demanding() -> None:
    """Проверяет, что долг за переходы не поднимает порог чужим классам.

    Запросы, ушедшие вслед за первым - переходы по редиректам и повторы, -
    относятся к классу той операции, которая их вызвала. Иного разумного ответа
    нет: класс есть свойство операции, а переход - её продолжение.

    Проверка появилась после настоящего регресса. Долг списывался без класса,
    то есть по умолчанию как interactive, и тем самым помечал самый защищённый
    класс претендующим. Цикл обновлений, прошедший через три перехода, на
    минуту поднимал порог допуска себе же и наблюдению за рынком - будто
    покупателю кто-то отвечал. Ровно то, от чего написано условие о спросе.

    Returns:
        None
    """
    budget = Budget()
    assert budget._floor_for(RequestClass.POLL, NOW) == 0.0

    # Три перехода, оплаченные классом той операции, что их вызвала.
    for _ in range(3):
        budget.reserve(NOW, 1.0, RequestClass.POLL)

    assert budget._floor_for(RequestClass.POLL, NOW) == 0.0, (
        "цикл обновлений поднял порог сам себе, заплатив за собственные переходы"
    )
    assert budget._floor_for(RequestClass.MONITORING, NOW) > 0.0, (
        "наблюдение обязано уступать циклу обновлений: тот вправду ходил"
    )
    assert budget._floor_for(RequestClass.AUTOMATION, NOW) == 0.0, (
        "автоматика уступает классу, который не приходил"
    )


def test_settle_charges_the_debt_to_the_calling_class() -> None:
    """Проверяет, что долг за переходы идёт через ядро верным классом.

    Предыдущая проверка смотрит на бюджет напрямую и потому не видит, каким
    классом платит САМО ЯДРО. Именно там и был регресс: settle списывал долг
    без класса.

    Returns:
        None
    """
    from funora._budget import Budget as EngineBudget
    from funora._engine import Engine
    from funora._transport import TransportSettings

    budget = EngineBudget()
    engine = Engine(TransportSettings(), budget)

    # Три ушедших вслед запроса от лица цикла обновлений.
    steps = engine.settle(3, RequestClass.POLL)
    reply = None
    while True:
        try:
            steps.send(reply)
        except StopIteration:
            break
        reply = None

    assert RequestClass.POLL in budget._demanded_at, "долг не списан вовсе"
    assert RequestClass.INTERACTIVE not in budget._demanded_at, (
        "ядро записало долг за переходы на interactive. На минуту это поднимет "
        "порог допуска всем прочим классам - будто покупателю кто-то отвечал"
    )


def test_a_free_retry_still_waits_out_the_cooldown() -> None:
    """Проверяет, что нулевая цена не отменяет отступления.

    Выбор был поведенческим, и спецификация о нём молчала. Два добросовестных
    прочтения: false снимает вызов бюджета целиком либо оставляет вызов с
    нулевой ценой. Расходятся они ровно в шторме повторов при пустом ведре - в
    том самом положении, ради которого правило и написано.

    Решение: false означает нулевую цену. Отменять вызов нельзя - тогда клиент,
    получивший 429, повторял бы запрос мимо собственного отступления, и шторм
    повторов стал бы не бесплатным, а неостановимым.

    Returns:
        None
    """
    from funora._identity import Identity

    identity = Identity(name="проба@funpay.com", budget=Budget())
    identity.note_limit(NOW)

    assert identity.is_cooling(NOW), "остывание не назначено - проверять нечего"

    # Даже бесплатный запрос идёт через бюджет, а значит через ядро, а значит
    # выжидает остывание. Проверка на уровне бюджета: снятый класс не проходит
    # ни за какую цену.
    free = identity.budget.reserve(NOW, 0.0, RequestClass.MONITORING)
    identity.note_limit(NOW + 1)
    after_second = identity.budget.reserve(NOW + 1, 0.0, RequestClass.MONITORING)

    assert free.granted, "после первого ограничения наблюдение ещё не снято"
    assert not after_second.granted, (
        "запрос ценой ноль прошёл, хотя класс снят с очереди вторым ограничением"
    )


def test_zero_cost_still_respects_the_class_floor() -> None:
    """Проверяет, что нулевая цена не отменяет правила допуска.

    Иначе повтор наблюдения проходил бы там, где обычный запрос наблюдения
    уступает, - то есть выключенный признак чинил бы бесплатность одного
    механизма поломкой другого.

    Returns:
        None
    """
    budget = Budget()
    for request_class in (RequestClass.INTERACTIVE, RequestClass.AUTOMATION, RequestClass.POLL):
        budget.reserve(NOW, 1.0, request_class)
    moment = _drain(budget, NOW, ACCOUNT.capacity * 0.30)

    free = budget.reserve(moment, 0.0, RequestClass.MONITORING)
    assert not free.granted, (
        "запрос ценой ноль прошёл мимо порога допуска: наблюдение обошло долю "
        "более защищённых классов, ничего не заплатив"
    )
