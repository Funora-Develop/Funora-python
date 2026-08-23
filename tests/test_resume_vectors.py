"""Проверяет набор resume: переживает ли гашение повторов перезапуск.

Свойство объявлено в spec/events/delivery.yaml как conformance_suite: resume -
«убийство процесса после старта обработчика, перезапуск, то же событие с тем же
идентификатором и тем же ключом идемпотентности, ноль потерь». Проверить его
было нечем.

Ловушка настоящая и тихая: гашение хранит метки времени, и реализация,
сохранившая их показанием СВОЕГО секундомера, после перезапуска прочтёт не то,
что записала. Начало отсчёта у монотонных часов своё в каждом запуске. На давно
работающей машине показание огромное, весь кэш выбрасывается разом, и всё уже
доставленное приходит заново - для обработчика выдачи это выданный дважды
товар. На свежей машине наоборот: разность отрицательная, и запись не истекает
никогда.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from funora.conformance import _run_scenario, _scenario, answer
from funora.errors import ValidationError

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Файл векторов набора.
VECTORS = Path(SPEC_DIR) / "spec" / "conformance" / "resume.vectors.json" if SPEC_DIR else None

#: Причина пропуска, если спецификации рядом нет.
NO_SPEC = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(VECTORS is None or not VECTORS.is_file(), reason=NO_SPEC)


def _document() -> dict[str, Any]:
    """Читает файл векторов.

    Returns:
        dict[str, Any]: Разобранный файл набора.
    """
    assert VECTORS is not None
    parsed: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return parsed


def _scenarios() -> list[dict[str, Any]]:
    """Возвращает сценарии набора.

    Returns:
        list[dict[str, Any]]: Сценарии в порядке объявления.
    """
    scenarios: list[dict[str, Any]] = _document()["scenarios"]
    return scenarios


def test_every_scenario_gives_what_the_vectors_declare() -> None:
    """Прогоняет каждый сценарий и сверяет дошедшее по шагам.

    Returns:
        None
    """
    for index, scenario in enumerate(_scenarios()):
        got = _run_scenario(scenario)
        want = [list(step) for step in scenario["expected"]]
        assert got == want, (
            f"сценарий {index} «{scenario['name']}»: получено {got}, ожидалось {want}. "
            f"{scenario.get('why', '')}"
        )


def test_every_scenario_declares_an_outcome_for_every_step() -> None:
    """Проверяет, что ожидаемое покрывает все шаги, а не часть.

    Сценарий, у которого шагов больше, чем ожиданий, проверял бы меньше, чем
    выглядит, и заметить это по зелёной сборке было бы нельзя.

    Returns:
        None
    """
    for scenario in _scenarios():
        assert len(scenario["expected"]) == len(scenario["steps"]), (
            f"сценарий «{scenario['name']}»: {len(scenario['steps'])} шагов и "
            f"{len(scenario['expected'])} ожиданий"
        )


def test_every_scenario_declares_its_uptime_and_it_is_not_zero() -> None:
    """Требует явный ненулевой аптайм первого процесса.

    При нуле показание секундомера совпадает с прошедшим по стенным часам, и
    реализация, перепутавшая одно с другим, случайно даёт верный ответ. Так и
    было: с нулём сценарий про истечение срока проходил на сломанной
    реализации, и мутация это показала.

    Прежде проверка была пустой дважды. Ни один сценарий поля не объявлял,
    поэтому тело цикла сверяло собственный литерал сам с собой; а сам литерал
    дублировал умолчание из кода, то есть запрет распространялся на данные и не
    распространялся на то место, которым ноль и задавался бы. Умолчание из кода
    убрано, поле стало обязательным.

    Returns:
        None
    """
    for scenario in _scenarios():
        assert "uptime_s" in scenario, (
            f"сценарий «{scenario['name']}» не объявил аптайма. Умолчание вернуло "
            "бы выбор в код, где запрет на ноль его не достаёт"
        )
        assert scenario["uptime_s"] != 0, (
            f"сценарий «{scenario['name']}» начинает первый процесс с нулевого "
            "аптайма: при нуле сломанная реализация даёт верный ответ случайно"
        )


def test_a_scenario_without_an_uptime_is_refused() -> None:
    """Проверяет, что умолчания в коде вправду нет.

    Проверка выше сверяет данные. Эта - код: она подаёт сценарий без поля и
    требует отказа. Без неё умолчание могло бы вернуться, и данные остались бы
    в порядке, а проверялось бы не объявленное.

    Returns:
        None
    """
    scenario = dict(_scenarios()[0])
    del scenario["uptime_s"]

    with pytest.raises(ValidationError):
        _run_scenario(scenario)


def test_at_least_one_record_is_aged_when_the_process_restarts() -> None:
    """Требует сценарий, где снимок снимается позже последней фиксации.

    Перевод метки через стенные часы - то самое выражение, ради которого набор
    написан, - проверялся во всех первых сценариях тождественно нулём: снимок
    снимался в тот же момент, в который была последняя фиксация, и возраст
    записи был всегда ноль. Реализация, восстанавливающая записи как только что
    доставленные, проходила набор целиком.

    Returns:
        None
    """
    aged = False
    for scenario in _scenarios():
        snapshot_at = None
        first_commit = None
        for step in scenario["steps"]:
            if "restart" in step:
                # Снимок снимается на момент последнего шага. Возраст ненулевой,
                # если к этому времени хоть одна запись уже полежала.
                if (
                    first_commit is not None
                    and snapshot_at is not None
                    and snapshot_at > first_commit
                ):
                    aged = True
                snapshot_at = None
                first_commit = None
                continue
            snapshot_at = step["at_ms"]
            if step.get("commit") and first_commit is None:
                first_commit = step["at_ms"]

    assert aged, (
        "ни в одном сценарии снимок не снимается позже последней фиксации: "
        "возраст записи всюду ноль, и перевод метки через стенные часы не "
        "проверяется вовсе"
    )


def test_the_suite_keeps_both_halves_of_the_trap() -> None:
    """Требует перезапуска и на свежей, и на давно работающей машине.

    У ошибки с монотонными часами два исхода, и они противоположны. Малое
    показание - запись не истекает никогда; огромное - кэш выбрасывается разом
    и товар выдаётся дважды. Набор, потерявший одну из половин, ловил бы только
    одну, и потеря была бы не видна.

    Returns:
        None
    """
    fresh = False
    aged = False
    for scenario in _scenarios():
        ttl_s = scenario["ttl_ms"] / 1000
        for step in scenario["steps"]:
            restart = step.get("restart")
            if restart is None:
                continue
            if restart["uptime_s"] < ttl_s:
                fresh = True
            if restart["uptime_s"] > ttl_s:
                aged = True

    assert fresh, "в наборе нет перезапуска на свежей машине - половина ловушки потеряна"
    assert aged, "в наборе нет перезапуска на давно работающей машине - вторая половина потеряна"


def test_a_scenario_that_starts_with_a_restart_is_refused() -> None:
    """Проверяет отказ там, где сохранять состояние не на какой момент.

    Перезапуск переводит метки через стенные часы, а первого шага со временем
    ещё не было. Молча взять текущий момент значило бы подставить в проверку
    настоящие часы машины - ровно то, от чего набор и уходит.

    Returns:
        None
    """
    with pytest.raises(ValidationError):
        _run_scenario({"ttl_ms": 1000, "steps": [{"restart": {"uptime_s": 5}}], "expected": []})


def test_a_broken_scenario_reference_is_refused() -> None:
    """Проверяет, что ссылка в пустоту даёт отказ, а не падение.

    Returns:
        None
    """
    with pytest.raises(ValidationError):
        _scenario("scenarios[999]")
    with pytest.raises(ValidationError):
        _scenario("не ссылка")


def test_the_case_answer_carries_the_steps() -> None:
    """Проверяет ответ по протоколу для вида resume.

    Ожидаемого реализация не знает: сверяет раннер. Иначе она сверяла бы себя
    сама, и расхождение с чужой реализацией осталось бы невидимым.

    Returns:
        None
    """
    case = {
        "id": "resume/проба",
        "suite": "resume",
        "kind": "resume",
        "vector": "scenarios[0]",
    }
    result = answer(case)

    assert result["outcome"] == "pass"
    assert result["steps"] == [list(step) for step in _scenarios()[0]["expected"]]
    assert "expected" not in result


def test_the_answer_does_not_depend_on_the_declared_uptime() -> None:
    """Прогоняет каждый сценарий при разных аптаймах первого процесса.

    Это и есть проверяемое свойство в чистом виде: аптайм не должен влиять ни
    на что. Если влияет - метки хранятся не в том.

    Returns:
        None
    """
    for scenario in _scenarios():
        answers = [
            _run_scenario({**scenario, "uptime_s": uptime}) for uptime in (1, 3600, 604800, 8640000)
        ]
        assert all(one == answers[0] for one in answers), (
            f"сценарий «{scenario['name']}» отвечает по-разному при разном аптайме: {answers}"
        )
