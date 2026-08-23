"""Проверяет, что правило трёх исходов чтения атрибута живёт в одном месте.

spec/extraction/rules.yaml, раздел attribute_states, требует различать три
исхода: селектор не нашёл узла, узел есть - атрибута нет, атрибут есть и пуст.
Первые два - про наше незнание, третий - факт о странице.

Там же записано правило одного места и история: в эталонной реализации оно
однажды нашлось написанным трижды, две копии дословно одинаковые и обе неверные,
третья верная и разошедшаяся с ними в первый же час.

Единственное место - funora._extract.attribute. Проверка следит, чтобы
наблюдаемое значение не собиралось в обход него: сырое чтение через
``.attributes.get(name) or ""`` сводит «атрибута нет» с «атрибут пуст» - ровно то
различие, ради которого правило написано.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Каталог исходников пакета.
SRC = Path(__file__).resolve().parent.parent / "src" / "funora"

#: Где правилу жить положено.
HOME = "_extract.py"

#: Модули, к которым правило не относится.
#:
#: _skeleton строит подписи для снимков и читает разметку целиком, не выдавая
#: наблюдений вызывающему. _signals - инструмент наблюдений, он печатает отчёт
#: человеку, а не собирает контрактные значения.
OUTSIDE: frozenset[str] = frozenset({"_skeleton.py", "_signals.py"})


def _executable(text: str) -> list[str]:
    """Отбрасывает строки документации и комментарии.

    Args:
        text (str): Исходный текст модуля.

    Returns:
        list[str]: Строки исполняемого кода.
    """
    without_docs = re.sub(r'"""(?:.|\n)*?"""', "", text)
    return re.sub(r"#.*", "", without_docs).splitlines()


def test_observed_values_are_not_built_from_raw_attribute_reads() -> None:
    """Проверяет, что наблюдение не собирается в обход единого места.

    Сырое чтение атрибута отдаёт одно и то же для «атрибута нет» и «атрибут
    пуст». Собрав из него Observed, реализация отбирает у вызывающего
    единственный способ отличить «площадка не дала адрес» от «площадка дала
    пустой» - и делает это молча.

    Returns:
        None
    """
    offenders: list[str] = []

    for path in sorted(SRC.glob("*.py")):
        if path.name == HOME or path.name in OUTSIDE:
            continue
        for number, line in enumerate(_executable(path.read_text(encoding="utf-8")), 1):
            if ".attributes" in line and "Observed" in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, (
        f"наблюдение собирается из сырого чтения атрибута: {offenders}. "
        "Пользуйтесь funora._extract.attribute - он различает три исхода, "
        "а сырое чтение сводит два из них"
    )


def test_the_single_place_still_exists() -> None:
    """Проверяет, что единое место не переехало и не исчезло.

    Без этого предыдущая проверка проходила бы сама собой: удалите attribute -
    и обходить станет нечего, а нарушений всё равно не найдётся.

    Returns:
        None
    """
    from funora._extract import attribute

    source = (SRC / HOME).read_text(encoding="utf-8")
    assert "def attribute(" in source, f"{HOME} больше не содержит единого места"
    assert attribute.__doc__ and "три исхода" in attribute.__doc__, (
        "единое место перестало объяснять, зачем оно"
    )
