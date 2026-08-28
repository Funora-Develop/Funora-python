"""Проверки инструмента, печатающего план наблюдений.

План печатает команды, которые человек вставляет в оболочку. Дважды это
кончилось тем, что ключ сессии оказывался в переписке: первый раз присваиванием
строкой, второй - подсказкой к Read-Host, чей первый аргумент и есть текст
приглашения.

Проверки здесь не про содержание плана, а про то, ЧТО он советует делать с
ключом. Совет, ведущий к утечке, хуже отсутствующего совета.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
TOOL: Final[Path] = ROOT / "tools" / "observe_plan.py"


def _printed() -> str:
    """Запускает инструмент и возвращает напечатанное.

    Возвращает:
        str: вывод плана целиком.
    """
    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec or not Path(spec).is_dir():
        pytest.skip("перечень наблюдений живёт в спецификации, а её каталог не задан")

    run = subprocess.run(  # noqa: S603
        [sys.executable, str(TOOL)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    assert run.returncode == 0, f"план не выполнился: {run.stderr}"
    return run.stdout


def test_the_key_is_asked_for_and_never_written_into_a_command() -> None:
    """Требует советовать ВВОД ключа, а не подстановку его в команду.

    Присваивание строкой кладёт ключ и на экран, и в историю оболочки. Ввод в
    приглашение не кладёт никуда: в историю ложится команда, а не введённое по
    ней.

    Возвращает:
        None
    """
    text = _printed()

    assert "Read-Host" in text, "план не советует вводить ключ приглашением"
    assert "-AsSecureString" in text, "ввод советуется незащищённым"

    # Присваивание чего-либо похожего на значение - то, чего быть не должно.
    assert '$env:FUNORA_GOLDEN_KEY = "' not in text, (
        "план советует подставить ключ прямо в команду: так он попадает и на "
        "экран, и в историю оболочки"
    )


def test_the_advice_works_in_the_powershell_the_user_has() -> None:
    """Требует советовать то, что вправду выполнится в Windows PowerShell 5.1.

    У ConvertFrom-SecureString признак -AsPlainText появился только в седьмой
    версии. Совет, написанный по семёрке, у пятой падает сообщением про
    несуществующий параметр - и это уже случилось.

    Возвращает:
        None
    """
    text = _printed()

    assert "ConvertFrom-SecureString" not in text, (
        "план советует ConvertFrom-SecureString: в Windows PowerShell 5.1 у неё "
        "нет признака -AsPlainText, и совет не выполнится"
    )
    assert "SecureStringToBSTR" in text, (
        "план не показывает способа достать строку из защищённого ввода в пятой "
        "версии - а без него ввод бесполезен"
    )


def test_the_plan_never_prints_anything_that_looks_like_a_key() -> None:
    """Требует, чтобы в напечатанном не было ничего похожего на ключ сессии.

    Проверка стоит на будущее: план читает файл спецификации, а туда однажды
    может попасть пример, написанный с настоящего значения.

    Возвращает:
        None
    """
    import re

    text = _printed()
    suspects = re.findall(r"\b[a-z0-9]{32}\b", text)
    assert not suspects, f"в плане есть значение формы ключа сессии: {suspects}"
