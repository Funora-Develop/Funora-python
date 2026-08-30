"""Проверяет, что напечатанные команды вправду выполнятся у читателя.

Набор появился после третьего случая подряд. Первый: план советовал
``ConvertFrom-SecureString -AsPlainText``, которого в Windows PowerShell 5.1
нет. Второй: план советовал голое ``funora-observe`` - точка входа лежит внутри
.venv, и без активации окружения оболочка её не находит. Третий нашёлся заодно и
опаснее обоих: ``python -m pip install PyYAML`` РАЗРЕШАЕТСЯ, но в системный
интерпретатор, и ставит пакет мимо окружения.

Проверка на первый случай была чёрным списком из одного литерала и второго не
поймала. Здесь список белый: команда обязана быть узнаваемо годной формы, всё
прочее - нарушение. Так ловится и то, чего никто не предвидел.

Проверяется ВЫВОД инструментов и тексты, которые читатель копирует глазами.
Сломанная команда пришла из спецификации, через поле, которое план печатает
дословно, - проверка исходников её бы не увидела.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: Точки входа пакета. У этих имён другого значения нет: где имя встретилось,
#: там команда, и годная форма у неё ровно одна.
ENTRY_POINTS: Final[tuple[str, ...]] = ("funora-observe",)

#: Годное начало пути внутрь окружения.
INSIDE_VENV: Final[str] = r".venv\Scripts"

#: Вызов интерпретатора. В КОМАНДНОЙ ПОЗИЦИИ он всегда обязан быть из окружения,
#: чем бы ни продолжался: и «python скрипт.py», и «python -m pip install».
#:
#: Первая редакция требовала после имени путь до скрипта - и пропустила ровно
#: тот случай, ради которого писалась: «python -m pip install PyYAML» скрипта не
#: называет. Образец, перечисляющий продолжения, не ловит того продолжения,
#: которого никто не предвидел.
#:
#: Прозу про интерпретатор это не задевает: «Похоже, запущен системный python» -
#: середина фразы, а не начало командной строки.
RUNS_A_SCRIPT: Final[re.Pattern[str]] = re.compile(r"(?<![\w\\/.-])python\b")

#: Установка пакета. Она обязана идти интерпретатором окружения.
INSTALLS: Final[re.Pattern[str]] = re.compile(r"(?<![\w\\/.-])pip\s+install\b")


def _rendered_plan() -> str:
    """Возвращает вывод плана наблюдений целиком.

    Возвращает:
        str: напечатанное планом.
    """
    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec or not Path(spec).is_dir():
        pytest.skip("перечень наблюдений живёт в спецификации, а её каталог не задан")

    run = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "tools" / "observe_plan.py")],
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


def _texts_a_reader_copies() -> dict[str, str]:
    """Собирает всё, откуда читатель копирует команды.

    Не только вывод инструментов: шапку порождённых модулей и подсказки в
    исходниках человек копирует ровно так же. Одна из трёх найденных поломок
    жила именно в шапке - и попала в десять файлов сразу.

    Возвращает:
        dict[str, str]: имя источника и его текст.
    """
    found: dict[str, str] = {"вывод плана наблюдений": _rendered_plan()}
    for path in sorted((ROOT / "tools").glob("*.py")):
        found[f"tools/{path.name}"] = path.read_text(encoding="utf-8")

    # И набор проверок тоже. Сообщение упавшей проверки читают ровно тогда, когда
    # ищут, что делать дальше, - и голая команда там стоит дороже, чем в прозе.
    # Одна такая нашлась сразу: «Перестройте: python tools/codegen.py».
    for path in sorted((ROOT / "tests").glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        found[f"tests/{path.name}"] = path.read_text(encoding="utf-8")
    for path in sorted((ROOT / "src" / "funora").glob("*.py")):
        head = path.read_text(encoding="utf-8")[:2000]
        if "Файл порождён из спецификации" in head:
            found[f"шапка src/funora/{path.name}"] = head
    return found


#: Что может стоять перед командой в её строке и командой не является.
#:
#: Отступ, номер шага, короткая подпись с двоеточием. Всё прочее перед именем
#: означает, что имя стоит В СЕРЕДИНЕ ФРАЗЫ, то есть это рассказ о команде, а не
#: сама команда. Рассказывать о сломанной команде надо уметь - иначе нельзя
#: объяснить, почему она сломана.
_LEADS_A_COMMAND: Final[re.Pattern[str]] = re.compile(
    r"^[\s>$]*(?:\d+\.\s*)?(?:[^\s:]{0,24}:\s+)?$"
)

#: Имена, которыми годная форма подставляется в исходниках.
_GOOD_PREFIX: Final[tuple[str, ...]] = (INSIDE_VENV, "RUN_PY", "RUN_OBSERVE")


def _at_command_position(text: str, start: int) -> bool:
    """Говорит, стоит ли имя в начале командной строки.

    Аргументы:
        text (str): проверяемый текст.
        start (int): позиция имени.

    Возвращает:
        bool: правда, если перед именем в строке нет ничего, кроме отступа,
        номера шага либо короткой подписи.
    """
    line_start = text.rfind("\n", 0, start) + 1
    return _LEADS_A_COMMAND.match(text[line_start:start]) is not None


def _offences(text: str) -> list[str]:
    """Находит команды, которые у читателя не выполнятся как есть.

    Смотрит только на КОМАНДНУЮ ПОЗИЦИЮ - начало строки. Имя, встреченное в
    середине фразы, - это рассказ, и запрещать его нельзя: тогда о сломанной
    команде нельзя было бы написать, что она сломана.

    Аргументы:
        text (str): проверяемый текст.

    Возвращает:
        list[str]: перечень нарушений с их окружением.
    """
    bad: list[str] = []

    def around(start: int, end: int) -> str:
        """Возвращает окружение находки для сообщения.

        Аргументы:
            start (int): начало находки.
            end (int): конец находки.

        Возвращает:
            str: строка с находкой.
        """
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        return text[line_start : line_end if line_end >= 0 else len(text)].strip()

    for name in ENTRY_POINTS:
        for match in re.finditer(re.escape(name), text):
            if not _at_command_position(text, match.start()):
                continue
            before = text[max(0, match.start() - 40) : match.start()]
            if not any(one in before for one in _GOOD_PREFIX):
                bad.append(f"голая точка входа {name!r}: {around(match.start(), match.end())}")

    for pattern, what in ((RUNS_A_SCRIPT, "запуск скрипта"), (INSTALLS, "установка пакета")):
        for match in pattern.finditer(text):
            if not _at_command_position(text, match.start()):
                continue
            before = text[max(0, match.start() - 40) : match.start()]
            if not any(one in before for one in _GOOD_PREFIX):
                bad.append(f"{what} мимо окружения: {around(match.start(), match.end())}")

    return bad


def test_every_printed_command_runs_in_the_shell_the_reader_has() -> None:
    """Требует, чтобы всякая напечатанная команда была годной формы.

    Список белый, а не чёрный, и это главное отличие от прежней проверки. Та
    запрещала один литерал и следующего случая не поймала: запрещать пришлось бы
    то, чего ещё никто не написал.

    Возвращает:
        None
    """
    complaints: list[str] = []
    for source, text in _texts_a_reader_copies().items():
        complaints += [f"{source}: {one}" for one in _offences(text)]

    assert not complaints, "команды, которые у читателя не выполнятся:\n" + "\n".join(complaints)


def test_the_entry_point_the_plan_advises_really_exists() -> None:
    """Требует, чтобы советуемая форма вправду запускалась.

    Форма может быть написана верно и при этом не существовать: точки входа
    появляются при установке пакета, а не при его наличии. Совет, ссылающийся на
    несуществующий файл, ничем не лучше голого имени.

    Возвращает:
        None
    """
    exe = ROOT / ".venv" / "Scripts" / "funora-observe.exe"
    if not exe.is_file():
        pytest.skip("окружение собрано иначе - проверять форму нечем")

    run = subprocess.run(  # noqa: S603
        [str(exe), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        check=False,
    )
    assert run.returncode == 0, f"советуемая точка входа не запускается: {run.stderr}"


def test_the_check_would_notice_a_broken_command() -> None:
    """Требует, чтобы проверка ловила каждую из трёх уже случившихся поломок.

    Проверка, не пойманная на настоящем примере, - это утверждение о самой себе.
    Три примера здесь не выдуманы: все три уже печатались читателю.

    Возвращает:
        None
    """
    already_happened = (
        "    funora-observe /chat/",
        "    python tools/codegen.py",
        "    python -m pip install PyYAML",
    )
    for one in already_happened:
        assert _offences(one), f"проверка не заметила бы {one!r}"

    # И не ругается на годные формы - иначе её обойдут, а не починят.
    good = (
        r"    .venv\Scripts\funora-observe.exe /chat/",
        r"    .venv\Scripts\python.exe tools/codegen.py",
        r"    .venv\Scripts\python.exe -m pip install PyYAML",
        "Похоже, запущен системный python, а не тот, что в .venv",
    )
    for one in good:
        assert not _offences(one), f"проверка ругается на годное: {one!r}"
