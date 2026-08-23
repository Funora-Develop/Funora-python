"""Проверяет участие в наборе соответствия.

Протокол объявлен в spec/conformance/runner-protocol.yaml. Раннер живёт в
репозитории спецификации: прогонять он будет любую из шести реализаций, и
принадлежать одной ему нельзя.

Главное свойство протокола - нельзя промолчать. Пропуск без ссылки на запись
реестра неисполненного считается отказом: набор, который можно тихо пропустить,
показывает согласие там, где его нет, а это хуже отсутствия набора. Отсутствие
видно, ложное согласие нет.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from funora.conformance import PROTOCOL, answer

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Причина пропуска, если спецификации рядом нет.
NO_SPEC = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "scripts" / "conformance.js").is_file(),
    reason=NO_SPEC,
)


def test_the_whole_suite_passes() -> None:
    """Прогоняет набор целиком настоящим раннером.

    Это единственная проверка, которая смотрит на реализацию снаружи - так же,
    как посмотрел бы автор второго SDK.

    Returns:
        None
    """
    root = Path(SPEC_DIR or ".")
    run = subprocess.run(  # noqa: S603
        [
            "node",
            str(root / "scripts" / "conformance.js"),
            f"{sys.executable} -m funora.conformance",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
        check=False,
    )

    assert run.returncode == 0, f"набор не пройден:\n{run.stdout}\n{run.stderr}"
    assert "отказов: 0" in run.stdout, run.stdout


def test_an_unknown_kind_is_a_failure_not_a_skip() -> None:
    """Проверяет, что незнакомый вид случая даёт отказ.

    Неизвестный вид означает, что реализация отстала от набора. Ответить на
    него пропуском значило бы объявить отставание объявленной неполнотой.

    Returns:
        None
    """
    result = answer({"id": "проба", "suite": "x", "kind": "невиданный", "vector": "a.b[0]"})

    assert result["outcome"] == "fail"
    assert "отстала" in result["detail"]


def test_a_broken_vector_reference_is_a_failure() -> None:
    """Проверяет, что ссылка в пустоту даёт отказ, а не падение.

    Раннер обязан получить ответ на каждый случай. Упавшая реализация не
    отвечает вовсе, и раннер не отличит поломку от молчания.

    Returns:
        None
    """
    result = answer(
        {"id": "проба", "suite": "canonical_form", "kind": "serialize", "vector": "нет.такого[99]"}
    )

    assert result["outcome"] == "fail"
    assert result["id"] == "проба"


def test_the_protocol_version_matches_the_contract() -> None:
    """Проверяет, что реализация отвечает по объявленной версии протокола.

    Returns:
        None
    """
    import yaml

    declared = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "conformance" / "runner-protocol.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert declared["protocol"] == PROTOCOL


def test_the_command_line_answers_line_by_line() -> None:
    """Проверяет сам транспорт: строка на входе - строка на выходе.

    Returns:
        None
    """
    case = json.dumps(
        {
            "id": "один",
            "suite": "canonical_form",
            "kind": "serialize",
            "vector": "serialize.accept[0]",
        },
        ensure_ascii=False,
    )
    run = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "funora.conformance"],
        input=case + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    lines = [line for line in run.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"на один случай пришло {len(lines)} строк"
    assert json.loads(lines[0])["id"] == "один"
