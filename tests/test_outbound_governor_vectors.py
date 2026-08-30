"""Проверяет набор outbound-governor: поведение ограничителя исходящих.

НАБОР БЫЛ ОБЪЯВЛЕН И НЕ СУЩЕСТВОВАЛ. spec/runtime/budget.yaml называл файл
векторов, файла не было, а раннер соответствия пропускает ненайденный набор
молча. Проверка спецификации существования объявленных векторов не требовала.

Прогоняется здесь, а не только раннером соответствия, по простой причине:
раннер в сборку не входит. Набор, который гоняют, когда вспомнят, - это тот же
объявленный и молчащий механизм, только с лишним шагом.

Часы виртуальные и подаются обе оси: стенная живёт на диске, монотонная - внутри
запуска. Сценарий с переводом часов подаёт их расходящимися, и это единственный
способ проверить, что реализация не путает одно с другим.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from funora.conformance import _run_outbound, answer

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Файл векторов набора.
VECTORS = (
    Path(SPEC_DIR) / "spec" / "conformance" / "outbound-governor.vectors.json" if SPEC_DIR else None
)

#: Причина пропуска, если спецификации рядом нет.
NO_SPEC = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(VECTORS is None or not VECTORS.is_file(), reason=NO_SPEC)


def _scenarios() -> list[dict[str, Any]]:
    """Возвращает сценарии набора.

    Возвращает:
        list[dict[str, Any]]: Сценарии из файла векторов.
    """
    assert VECTORS is not None
    doc = json.loads(VECTORS.read_text(encoding="utf-8"))
    scenarios: list[dict[str, Any]] = doc["scenarios"]
    return scenarios


def _ids() -> list[str]:
    """Возвращает имена сценариев для отчёта.

    Возвращает:
        list[str]: Имена.
    """
    return [str(one["name"]) for one in _scenarios()] if VECTORS and VECTORS.is_file() else []


@pytest.mark.parametrize("index", range(len(_scenarios())) if VECTORS else [], ids=_ids())
def test_the_scenario_gives_the_declared_decisions(index: int) -> None:
    """Прогоняет сценарий и сверяет решения дословно.

    Аргументы:
        index (int): Номер сценария.

    Возвращает:
        None
    """
    scenario = _scenarios()[index]
    got = _run_outbound(scenario)

    assert got == scenario["expected"], (
        f"сценарий «{scenario['name']}»: решения {got}, ожидались {scenario['expected']}"
    )


@pytest.mark.parametrize("index", range(len(_scenarios())) if VECTORS else [], ids=_ids())
def test_the_scenario_goes_through_the_protocol(index: int) -> None:
    """Прогоняет сценарий тем же путём, каким его гоняет раннер.

    Проверка не лишняя. Первая зовёт внутреннюю функцию напрямую, а раннер
    ходит через ответчик протокола: разойдись они - набор соответствия отвечал
    бы не то, что проверяет своя сборка, и расхождение вылезло бы у второй
    реализации.

    Аргументы:
        index (int): Номер сценария.

    Возвращает:
        None
    """
    scenario = _scenarios()[index]
    reply = answer(
        {
            "id": f"outbound-governor/{scenario['name']}",
            "suite": "outbound_governor",
            "kind": "outbound_governor",
            "vector": f"scenarios[{index}]",
        }
    )

    assert reply["outcome"] == "pass", reply
    assert reply["decisions"] == scenario["expected"], reply


def test_every_scenario_declares_as_many_verdicts_as_it_has_attempts() -> None:
    """Требует, чтобы решений было ровно столько же, сколько попыток отправки.

    Сценарий, у которого их разное число, судит не то, что делает: лишнее
    ожидаемое никогда не проверяется, недостающее никогда не сверяется.

    Возвращает:
        None
    """
    for scenario in _scenarios():
        sends = sum(1 for one in scenario["events"] if one["kind"] == "send")
        assert sends == len(scenario["expected"]), (
            f"сценарий «{scenario['name']}»: попыток {sends}, ожидаемых {len(scenario['expected'])}"
        )


def test_the_set_covers_both_outcomes() -> None:
    """Требует, чтобы в наборе были и разрешения, и отказы.

    Набор из одних отказов проходит на реализации, отвергающей всё подряд;
    набор из одних разрешений - на реализации без единого предела.

    Возвращает:
        None
    """
    seen: set[str] = set()
    for scenario in _scenarios():
        seen.update(scenario["expected"])

    assert "allowed" in seen, "в наборе нет ни одного разрешения"
    assert len(seen - {"allowed"}) >= 3, (
        f"в наборе всего {len(seen - {'allowed'})} видов отказа: {sorted(seen)}. "
        "Пределов у ограничителя больше, и непроверенный предел - это предел, "
        "который вторая реализация вправе посчитать иначе"
    )
