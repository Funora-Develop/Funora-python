"""Генерация модулей SDK из спецификации.

Зачем это существует. В спецификации 35 ошибок и 22 возможности. Переписать их
руками в шесть языков - гарантированное расхождение: одна реализация забудет
ошибку, другая переставит код, и обе будут считать себя правой. Расхождение
такого рода не ловится ни тестами реализации, ни ревью, потому что каждая
реализация сама по себе выглядит непротиворечиво.

Поэтому механические части не пишутся, а порождаются, и сборка падает, если
порождённое отстало от источника. Проверка живёт в tests/test_generated.py и
устроена так же, как сверка селекторов: обе отвечают на один вопрос - совпадает
ли то, что лежит в репозитории, с тем, что обещает спецификация.

Запуск:

    python tools/codegen.py --spec ПУТЬ_К_FUNORA_SPEC

Без аргумента путь берётся из переменной окружения FUNORA_SPEC_DIR.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import yaml

#: Корень пакета, в который пишутся порождённые модули.
PACKAGE = Path(__file__).resolve().parent.parent / "src" / "funora"

#: Шапка порождённого файла. Стоит первой строкой, чтобы правку заметили сразу.
HEADER = '''"""{title}

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - {source} в репозитории Funora-spec.
Перестроить: python tools/codegen.py

{extra}"""

from __future__ import annotations

from typing import ClassVar, Final

'''


def _load(spec: Path, relative: str) -> dict[str, Any]:
    """Читает файл спецификации.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.
        relative (str): Путь файла относительно корня.

    Returns:
        dict[str, Any]: Разобранный документ.

    Raises:
        FileNotFoundError: Если файла нет.
    """
    path = spec / relative
    if not path.is_file():
        raise FileNotFoundError(f"нет файла спецификации: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _order(errors: dict[str, Any]) -> list[str]:
    """Расставляет ошибки так, чтобы родитель шёл раньше потомка.

    Порядок обязан быть устойчивым: файл сравнивается посимвольно с порождённым
    заново, и произвольный порядок сделал бы проверку ложно срабатывающей.

    Args:
        errors (dict[str, Any]): Раздел errors спецификации.

    Returns:
        list[str]: Имена ошибок в порядке объявления.
    """

    def depth(name: str) -> int:
        """Считает глубину вложенности ошибки.

        Args:
            name (str): Имя ошибки.

        Returns:
            int: Число предков до корня.
        """
        level = 0
        current = errors[name].get("parent")
        while current:
            level += 1
            current = errors[current].get("parent")
        return level

    return sorted(errors, key=lambda n: (depth(n), errors[n]["abi_code"]))


def _flags(spec_entry: dict[str, Any]) -> str:
    """Составляет строку с пояснением поведенческих признаков ошибки.

    Args:
        spec_entry (dict[str, Any]): Описание одной ошибки.

    Returns:
        str: Текст для docstring либо пустая строка.
    """
    parts = ["Повтор допустим" if spec_entry.get("retryable") else "Повтор не поможет"]
    if spec_entry.get("side_effects_possible"):
        parts.append("действие могло произойти несмотря на ошибку")
    if spec_entry.get("user_actionable"):
        parts.append("исправляется тем, кто вызвал")
    return ", ".join(parts) + "."


def render_errors(spec: Path) -> str:
    """Порождает модуль иерархии ошибок.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.
    """
    doc = _load(spec, "spec/errors/errors.yaml")
    errors: dict[str, Any] = doc["errors"]
    names = _order(errors)

    extra = (
        "Числовой код abi_code одинаков во всех шести SDK и не переиспользуется\n"
        "никогда: код, освободившийся после удаления ошибки, остаётся занятым\n"
        "навсегда, иначе старый клиент истолкует новую ошибку как прежнюю.\n"
        "\n"
        "Имя TimeoutError затеняет встроенное. Это разрешено спецификацией и не\n"
        "требует переименования: имена ошибок одинаковы во всех реализациях, и\n"
        "уступать одному языку значило бы разойтись с пятью остальными. Внутри\n"
        "пакета встроенное исключение доступно как builtins.TimeoutError.\n"
    )

    out = [
        HEADER.format(
            title="Иерархия ошибок Funora.",
            source="spec/errors/errors.yaml",
            extra=extra,
        )
    ]

    out.append("__all__ = [\n")
    for name in names:
        out.append(f'    "{name}",\n')
    out.append('    "ERROR_BY_STABLE_ID",\n')
    out.append('    "ERROR_BY_ABI_CODE",\n')
    out.append("]\n")

    for name in names:
        entry = errors[name]
        parent = entry.get("parent") or "Exception"
        summary = " ".join(str(entry["summary"]).split())
        body = textwrap.fill(summary, width=84, subsequent_indent="    ")
        flags = textwrap.fill(_flags(entry), width=84, subsequent_indent="    ")
        out.append(f"\n\nclass {name}({parent}):\n")
        out.append(f'    """{body}\n\n')
        out.append(f"    {flags}\n\n")
        out.append("    Attributes:\n")
        out.append(f'        stable_id (str): Устойчивый идентификатор "{entry["stable_id"]}".\n')
        out.append(
            f"        abi_code (int): Числовой код {entry['abi_code']}, общий для всех SDK.\n"
        )
        out.append(
            f"        retryable (bool): Допустим ли повтор: {bool(entry.get('retryable'))}.\n"
        )
        out.append(
            "        side_effects_possible (bool): Могло ли действие произойти: "
            f"{bool(entry.get('side_effects_possible'))}.\n"
        )
        out.append(
            "        user_actionable (bool): Исправляется ли вызывающим: "
            f"{bool(entry.get('user_actionable'))}.\n"
        )
        out.append(f'        since_spec (str): Версия спецификации "{entry["since_spec"]}".\n')
        out.append('    """\n\n')
        # ClassVar, а не Final: Final запрещает переопределение в наследнике, а
        # здесь каждый наследник обязан объявить своё значение. Проверка типов
        # ловит это сразу, поэтому ошибка не дожила бы до слияния, но причина
        # достаточно неочевидна, чтобы записать её здесь.
        out.append(f'    stable_id: ClassVar[str] = "{entry["stable_id"]}"\n')
        out.append(f"    abi_code: ClassVar[int] = {entry['abi_code']}\n")
        out.append(f"    retryable: ClassVar[bool] = {bool(entry.get('retryable'))}\n")
        out.append(
            "    side_effects_possible: ClassVar[bool] = "
            f"{bool(entry.get('side_effects_possible'))}\n"
        )
        out.append(f"    user_actionable: ClassVar[bool] = {bool(entry.get('user_actionable'))}\n")
        out.append(f'    since_spec: ClassVar[str] = "{entry["since_spec"]}"\n')

    out.append("\n\n#: Поиск класса по устойчивому идентификатору.\n")
    out.append("#:\n")
    out.append("#: Нужен там, где ошибка приходит извне процесса: из журнала, из очереди,\n")
    out.append("#: от другой реализации. Идентификатор устойчив между версиями, имя класса\n")
    out.append("#: языка - нет.\n")
    out.append("ERROR_BY_STABLE_ID: Final[dict[str, type[Exception]]] = {\n")
    for name in names:
        out.append(f'    "{errors[name]["stable_id"]}": {name},\n')
    out.append("}\n\n")

    out.append("#: Поиск класса по числовому коду.\n")
    out.append("#:\n")
    out.append("#: Код одинаков во всех шести SDK, поэтому по нему ошибка опознаётся при\n")
    out.append("#: передаче между реализациями.\n")
    out.append("ERROR_BY_ABI_CODE: Final[dict[int, type[Exception]]] = {\n")
    for name in names:
        out.append(f"    {errors[name]['abi_code']}: {name},\n")
    out.append("}\n")

    return "".join(out)


#: Что порождается: имя файла в пакете и функция, которая его строит.
TARGETS: Final[dict[str, Callable[[Path], str]]] = {"errors.py": render_errors}


def generate(spec: Path) -> dict[str, str]:
    """Строит содержимое всех порождаемых модулей.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        dict[str, str]: Имя файла в пакете и его содержимое.
    """
    return {name: render(spec) for name, render in TARGETS.items()}


def main(argv: list[str] | None = None) -> int:
    """Точка входа генератора.

    Args:
        argv (list[str] | None): Аргументы командной строки.

    Returns:
        int: 0 при успехе, 1 если спецификация недоступна.
    """
    parser = argparse.ArgumentParser(
        prog="codegen",
        description="Порождает модули SDK из спецификации Funora.",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=os.environ.get("FUNORA_SPEC_DIR"),
        help="корень рабочей копии Funora-spec; по умолчанию из FUNORA_SPEC_DIR",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="ничего не писать, а сообщить, отстали ли файлы от спецификации",
    )
    args = parser.parse_args(argv)

    if args.spec is None:
        print(
            "путь к спецификации не задан: укажите --spec или FUNORA_SPEC_DIR",
            file=sys.stderr,
        )
        return 1

    try:
        rendered = generate(Path(args.spec))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    stale: list[str] = []
    for name, body in rendered.items():
        target = PACKAGE / name
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current == body:
            print(f"  {name}: не изменился")
            continue
        stale.append(name)
        if args.check:
            print(f"  {name}: ОТСТАЛ от спецификации")
            continue
        target.write_text(body, encoding="utf-8", newline="\n")
        print(f"  {name}: перестроен")

    if args.check and stale:
        print(
            "порождённые файлы отстали от спецификации. Перестройте: python tools/codegen.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
