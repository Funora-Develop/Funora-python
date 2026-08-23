"""Проверяет набор rate-budget: поведение бюджета запросов.

Набор объявлен в spec/runtime/budget.yaml одним словом и до сих пор существовал
только им. Числа вёдер сверялись со спецификацией - что ёмкость шестьдесят и
залп десять, - а поведение, которое из этих чисел следует, не проверялось ничем.

Часы виртуальные. Бюджет не спит: он отвечает, сколько ждать, а ожидание
выполняет вызывающий и двигает часы сам. Иначе проверка шла бы столько же,
сколько занимает настоящее ожидание, и проверяла бы заодно точность таймера.

Ожидаемое задано границами, а не точной меткой везде. Запас в ведре считается
числами с плавающей точкой, и последний бит деления даёт то 199, то 200
миллисекунд: набор, требующий точной метки, показал бы отказ там, где
реализация права. Запись об этом заведена в реестре как budget_exact_trace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from funora._budget import Budget
from funora.budget import BUCKETS, BURST_WINDOW_MS, WAIT_ATTEMPTS, RequestClass
from funora.conformance import _requests, _run_trace, answer
from funora.errors import BudgetExhaustedError

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Файл векторов набора.
VECTORS = Path(SPEC_DIR) / "spec" / "conformance" / "rate-budget.vectors.json" if SPEC_DIR else None

#: Причина пропуска, если спецификации рядом нет.
NO_SPEC = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(VECTORS is None or not VECTORS.is_file(), reason=NO_SPEC)


def _scenarios() -> list[dict[str, Any]]:
    """Возвращает сценарии набора.

    Returns:
        list[dict[str, Any]]: Сценарии в порядке объявления.
    """
    assert VECTORS is not None
    document: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    scenarios: list[dict[str, Any]] = document["scenarios"]
    return scenarios


def _runnable() -> list[dict[str, Any]]:
    """Возвращает сценарии, которые эталонная реализация умеет прогнать.

    Одновременное поступление решает общая очередь с приоритетами, а эталонная
    реализация ходит на площадку по одному запросу за раз. Такой сценарий
    отвечается пропуском со ссылкой на реестр, а не тихо проходится по порядку
    поступления.

    Returns:
        list[dict[str, Any]]: Сценарии без признака concurrent.
    """
    return [one for one in _scenarios() if not one.get("concurrent")]


def test_every_trace_stays_inside_what_the_vectors_declare() -> None:
    """Прогоняет каждую трассу и сверяет с ожидаемым.

    Returns:
        None
    """
    for scenario in _runnable():
        sent = _run_trace(scenario)
        want = scenario["expected"]
        note = f"сценарий «{scenario['name']}»"

        if "refused" in want:
            got = [index for index, one in enumerate(sent) if one is None]
            assert got == want["refused"], f"{note}: не ушли {got}, ожидалось {want['refused']}"

        for index, moment in want.get("at", {}).items():
            assert sent[int(index)] == moment, (
                f"{note}: запрос {index} ушёл в {sent[int(index)]}, ожидалось ровно {moment}"
            )

        for index, floor in want.get("not_before", {}).items():
            assert sent[int(index)] is not None, f"{note}: запрос {index} не ушёл вовсе"
            assert sent[int(index)] >= floor, (  # type: ignore[operator]
                f"{note}: запрос {index} ушёл в {sent[int(index)]}, а не раньше {floor}"
            )

        for index, ceiling in want.get("not_after", {}).items():
            assert sent[int(index)] is not None, f"{note}: запрос {index} не ушёл вовсе"
            assert sent[int(index)] <= ceiling, (  # type: ignore[operator]
                f"{note}: запрос {index} ушёл в {sent[int(index)]}, а граница {ceiling}"
            )

        if "total_sent_at_most" in want:
            total = sum(1 for one in sent if one is not None)
            assert total <= want["total_sent_at_most"], (
                f"{note}: ушло {total}, а больше {want['total_sent_at_most']} уйти не может"
            )


def test_the_budget_belongs_to_the_identity_and_not_to_the_client() -> None:
    """Показывает, что сценарий про двадцать аккаунтов не пустой.

    Та же трасса, прогнанная с отдельным бюджетом на каждый аккаунт, выпускает
    больше запросов, чем вмещает ведро идентичности. Это и есть ошибка, ради
    которой сценарий написан: двадцать клиентов, каждый со «своим» бюджетом,
    дают двадцатикратную нагрузку с одного адреса при формально соблюдённых
    правилах.

    Без этой проверки сценарий выглядел бы работающим и не значил бы ничего:
    ограничение проходит и тогда, когда ушло всего пять запросов.

    Returns:
        None
    """
    scenario = next(one for one in _runnable() if "total_sent_at_most" in one["expected"])
    limit = scenario["expected"]["total_sent_at_most"]

    # Тот же прогон, но бюджет заводится под каждый аккаунт - как если бы
    # единицей учёта был клиент, а не сетевая идентичность.
    own: dict[str, Budget] = {}
    attempts = WAIT_ATTEMPTS if scenario.get("waits", True) else 1
    now_ms = 0
    loose = 0
    for request in _requests(scenario):
        now_ms = max(now_ms, int(request.get("at_ms", 0)))
        budget = own.setdefault(request["account"], Budget())
        for attempt in range(attempts):
            try:
                reservation = budget.require(
                    now_ms / 1000, cost=1.0, request_class=RequestClass(request["class"])
                )
            except BudgetExhaustedError:
                break
            if reservation.granted:
                loose += 1
                break
            if attempt + 1 == attempts:
                break
            now_ms += reservation.wait_ms

    assert loose > limit, (
        f"бюджет на клиента выпустил {loose} запросов при пределе {limit}: сценарий "
        "не различает единицу учёта и ничего не проверяет"
    )


def test_the_burst_scenario_is_bounded_by_the_declared_recovery() -> None:
    """Сверяет границы залпа с правилом восстановления.

    Границы в векторах - не подобранные числа. Право на залп восстанавливается
    равномерно, burst единиц за окно, то есть одна единица за window_ms / burst.
    Проверка требует, чтобы вектор с этим совпадал: разошедшаяся граница
    отменила бы правило молча.

    Окно берётся из спецификации, а не литералом. Прежде здесь стояла тысяча, и
    проверка сверяла вектор с константой вместо объявленного окна: правка
    window_ms проходила насквозь - кодогенератор перестраивал число, ворота
    спецификации молчали, набор оставался зелёным, - а настоящая трасса уезжала
    вдвое.

    Связывающим оказывается ведро АККАУНТА, а не идентичности: залп у него пять
    против десяти, и упирается трасса в меньший из двух.

    Returns:
        None
    """
    scenario = next(one for one in _runnable() if "not_before" in one["expected"])
    account = BUCKETS["account"]
    step = BURST_WINDOW_MS / account.burst

    for index, floor in scenario["expected"]["not_before"].items():
        # Первые burst запросов уходят без ожидания, следующие - по одному за
        # шаг восстановления.
        expected = (int(index) - account.burst + 1) * step
        assert floor == expected, (
            f"граница запроса {index} объявлена {floor}, а из правила "
            f"восстановления следует {expected}"
        )


def test_every_lower_bound_has_an_upper_one() -> None:
    """Требует коридор там, где объявлена нижняя граница.

    Одна нижняя граница ловит только слишком быстрое. Реализация,
    восстанавливающая право на залп ВДВОЕ МЕДЛЕННЕЕ объявленного, все нижние
    границы соблюдает - и проходила набор целиком, пока верхних не было.

    Returns:
        None
    """
    for scenario in _runnable():
        want = scenario["expected"]
        low = set(want.get("not_before", {}))
        high = set(want.get("not_after", {}))
        assert low <= high, (
            f"сценарий «{scenario['name']}»: у запросов {sorted(low - high)} есть "
            "нижняя граница и нет верхней. Слишком медленную реализацию такой "
            "сценарий не отличает от правильной"
        )


def test_the_corridor_is_wide_enough_for_the_guard_and_no_wider() -> None:
    """Держит коридор узким.

    Коридор существует затем, что запас считается числами с плавающей точкой, а
    к паузе прибавляется сторожевая миллисекунда. Ширины хватает на несколько
    таких прибавок; коридор шире прятал бы настоящее расхождение.

    Returns:
        None
    """
    for scenario in _runnable():
        want = scenario["expected"]
        for index, floor in want.get("not_before", {}).items():
            width = want["not_after"][index] - floor
            assert 0 < width <= 100, (
                f"сценарий «{scenario['name']}», запрос {index}: коридор шириной "
                f"{width} мс. Узкий не переживёт сторожевой миллисекунды, широкий "
                "спрячет расхождение"
            )


def test_the_concurrent_scenario_is_skipped_with_a_registry_reference() -> None:
    """Проверяет, что несделанное объявляется словами, а не проходится тихо.

    Пропуск без ссылки на реестр протокол считает отказом. Набор, который можно
    тихо пропустить, показывает согласие там, где его нет, и это хуже
    отсутствия набора: отсутствие видно, ложное согласие нет.

    Returns:
        None
    """
    index, scenario = next(
        (index, one) for index, one in enumerate(_scenarios()) if one.get("concurrent")
    )
    result = answer(
        {
            "id": "rate-budget/проба",
            "suite": "rate_budget",
            "kind": "rate_budget",
            "vector": f"scenarios[{index}]",
        }
    )

    assert result["outcome"] == "skip"
    assert result["not_implemented"] == scenario["requires"]

    assert SPEC_DIR is not None
    registry = (Path(SPEC_DIR) / "spec" / "conformance" / "not-implemented.yaml").read_text(
        encoding="utf-8"
    )
    assert f"\n  {scenario['requires']}:" in registry, (
        f"сценарий ссылается на запись «{scenario['requires']}», которой в реестре нет"
    )


def test_a_generated_trace_matches_the_rule_that_describes_it() -> None:
    """Проверяет разворачивание порождающего правила.

    Правило нужно там, где запросов сотня и перечень был бы нечитаем. Ошибка в
    развороте сделала бы сценарий короче, чем он выглядит, и ограничение прошло
    бы по этой причине, а не по правильной.

    Returns:
        None
    """
    scenario = next(one for one in _runnable() if "generate" in one)
    rule = scenario["generate"]
    requests = _requests(scenario)

    assert len(requests) == rule["accounts"] * rule["per_account"]
    assert len({one["account"] for one in requests}) == rule["accounts"]
    assert all(one["class"] == rule["class"] for one in requests)
    assert all(one["at_ms"] == rule["at_ms"] for one in requests)


def test_a_trace_gives_the_same_answer_every_time() -> None:
    """Требует, чтобы прогон не зависел от порядка и от прошлых сценариев.

    Реестр идентичностей общий на процесс. Прогон, оставивший в нём остатки,
    сделал бы трассу зависимой от того, что прогоняли раньше, - и отказ
    появлялся бы через раз.

    Returns:
        None
    """
    for scenario in _runnable():
        first = _run_trace(scenario)
        second = _run_trace(scenario)
        assert first == second, f"сценарий «{scenario['name']}» отвечает по-разному подряд"
