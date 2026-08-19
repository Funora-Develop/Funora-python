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


def _const(name: str) -> str:
    """Превращает имя возможности в имя члена перечисления.

    Args:
        name (str): Имя из спецификации, например ``chats.send_text``.

    Returns:
        str: Имя члена, например ``CHATS_SEND_TEXT``.
    """
    return name.replace(".", "_").replace("-", "_").upper()


def render_capabilities(spec: Path) -> str:
    """Порождает модуль возможностей и их состояний.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.
    """
    doc = _load(spec, "spec/capabilities.yaml")
    states: dict[str, Any] = doc["states"]
    caps: dict[str, Any] = doc["capabilities"]

    # Множества строятся из predicates, а не из признаков внутри states, и это
    # исправление настоящей ошибки. У состояния experimental признак usable
    # равен true - возможность действительно работает, - но решение «звать или
    # отказать» принимается по предикату, и там его нет. Первая версия
    # генератора собирала множество из признаков, и проверка вида
    # «if not state.usable: raise» пропускала экспериментальную возможность без
    # включения, отменяя ту единственную ветку, ради которой состояние заведено.
    predicates: dict[str, Any] = doc["predicates"]
    if not predicates.get("normative"):
        raise ValueError(
            "spec/capabilities.yaml: predicates обязаны быть помечены normative, "
            "иначе решение о вызове выводится из описательных признаков"
        )
    usable = list(predicates["is_usable"]["true_for"])
    opt_in = list(predicates["requires_opt_in"]["true_for"])
    unknown_states = [s for s in usable + opt_in if s not in states]
    if unknown_states:
        raise ValueError(
            f"spec/capabilities.yaml: предикаты ссылаются на состояния, "
            f"которых нет: {', '.join(unknown_states)}"
        )

    extra = (
        "Состояний пять, и разница между ними не косметическая. Вызов блокируется\n"
        "только при unsupported, то есть при позитивном свидетельстве отсутствия.\n"
        "При unknown вызов выполняется оптимистично: неудачная проверка не\n"
        "доказывает, что возможности нет, и блокировать по ней значило бы\n"
        "запрещать работу из-за собственной неуверенности.\n"
        "\n"
        "Признаки usable и opt_in_required вынесены в код намеренно. Именно из них\n"
        "выводится решение «звать или отказать», и если каждая из шести реализаций\n"
        "выведет его сама, они разойдутся в поведении, оставаясь согласными в\n"
        "названиях.\n"
    )

    out = [
        HEADER.format(
            title="Возможности адаптера и их состояния.",
            source="spec/capabilities.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from enum import StrEnum\nfrom typing import Final",
        )
    ]

    out.append('__all__ = ["CapabilityState", "Capability", "CAPABILITY_SOURCE", ')
    out.append('"CAPABILITY_INITIAL"]\n')

    out.append("\n\nclass CapabilityState(StrEnum):\n")
    out.append('    """Состояние возможности в текущем сеансе.\n\n')
    for key, value in states.items():
        summary = textwrap.fill(
            " ".join(str(value["summary"]).split()), width=80, subsequent_indent="        "
        )
        out.append(f"    {key}:\n        {summary}\n")
    out.append('    """\n\n')
    for key in states:
        out.append(f'    {key.upper()} = "{key}"\n')

    out.append("\n    @property\n")
    out.append("    def usable(self) -> bool:\n")
    out.append('        """Сообщает, можно ли звать возможность без дополнительных условий.\n\n')
    out.append("        Отвечает на вопрос «можно ли звать прямо сейчас», а не «работает ли\n")
    out.append("        возможность вообще». У состояния experimental возможность работает,\n")
    out.append("        но звать её без явного включения нельзя, поэтому здесь False.\n\n")
    out.append("        Returns:\n")
    out.append("            bool: True, если вызов разрешён без включения.\n")
    out.append('        """\n')
    out.append("        return self in _USABLE\n")

    out.append("\n    @property\n")
    out.append("    def opt_in_required(self) -> bool:\n")
    out.append('        """Сообщает, требуется ли явное согласие вызывающего.\n\n')
    out.append("        Returns:\n")
    out.append("            bool: True, если без включения вызов отклоняется.\n")
    out.append('        """\n')
    out.append("        return self in _OPT_IN\n")

    out.append("\n    def allows_call(self, *, opted_in: bool) -> bool:\n")
    out.append('        """Решает, разрешён ли вызов в этом состоянии.\n\n')
    out.append("        Правило взято из predicates в spec/capabilities.yaml, где оно\n")
    out.append("        объявлено нормативным. Выводить его заново в каждой реализации\n")
    out.append("        нельзя: шесть SDK выведут шесть разных решений, оставаясь\n")
    out.append("        согласными в названиях состояний.\n\n")
    out.append("        Args:\n")
    out.append("            opted_in (bool): Включил ли вызывающий возможность явно.\n\n")
    out.append("        Returns:\n")
    out.append("            bool: True, если вызов разрешён.\n")
    out.append('        """\n')
    out.append("        return self in _USABLE or (opted_in and self in _OPT_IN)\n")

    out.append("\n\n#: Состояния, в которых вызов разрешён без дополнительных условий.\n")
    out.append("_USABLE: Final[frozenset[CapabilityState]] = frozenset(\n    {\n")
    for key in usable:
        out.append(f"        CapabilityState.{key.upper()},\n")
    out.append("    }\n)\n")
    out.append("\n#: Состояния, требующие явного включения вызывающим.\n")
    out.append("_OPT_IN: Final[frozenset[CapabilityState]] = frozenset(\n    {\n")
    for key in opt_in:
        out.append(f"        CapabilityState.{key.upper()},\n")
    out.append("    }\n)\n")

    out.append("\n\nclass Capability(StrEnum):\n")
    out.append('    """Возможность адаптера.\n\n')
    out.append("    Значение члена совпадает с именем возможности в спецификации, поэтому\n")
    out.append("    перечисление пригодно и для сравнения, и для записи в журнал.\n")
    out.append('    """\n\n')
    for name, entry in caps.items():
        summary = textwrap.fill(
            " ".join(str(entry["summary"]).split()), width=88, subsequent_indent="    #: "
        )
        out.append(f"    #: {summary}\n")
        out.append(f'    {_const(name)} = "{name}"\n')

    out.append("\n\n#: Откуда берётся состояние возможности.\n")
    out.append("#:\n")
    out.append("#: static - известно из спецификации, probe - выясняется проверкой,\n")
    out.append("#: derived - выводится из состояния других возможностей.\n")
    out.append("CAPABILITY_SOURCE: Final[dict[Capability, str]] = {\n")
    for name, entry in caps.items():
        out.append(f'    Capability.{_const(name)}: "{entry["source"]}",\n')
    out.append("}\n")

    out.append("\n#: Состояние возможности до первой проверки.\n")
    out.append("#:\n")
    out.append("#: Начальное значение unknown означает «ещё не выяснено», а не «нет».\n")
    out.append("#: Разница определяет, будет вызов выполнен или отклонён.\n")
    out.append("CAPABILITY_INITIAL: Final[dict[Capability, CapabilityState]] = {\n")
    for name, entry in caps.items():
        out.append(
            f"    Capability.{_const(name)}: CapabilityState.{str(entry['initial']).upper()},\n"
        )
    out.append("}\n")

    return "".join(out)


def render_response_classes(spec: Path) -> str:
    """Порождает таблицу соответствия вердиктов ошибкам.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.
    """
    doc = _load(spec, "spec/protocol/response-classes.yaml")
    errors: dict[str, Any] = _load(spec, "spec/errors/errors.yaml")["errors"]
    by_stable = {entry["stable_id"]: name for name, entry in errors.items()}
    table: dict[str, Any] = doc["verdict_errors"]

    if doc.get("pipeline", {}).get("order_is_normative") is not True:
        raise ValueError(
            "spec/protocol/response-classes.yaml: порядок шагов обязан быть объявлен нормативным"
        )

    extra = (
        "Ключ - пара из класса ответа и машиночитаемой причины. Значение - класс\n"
        "ошибки либо None, если ответ пригоден для разбора.\n"
        "\n"
        "Таблица порождается, а не пишется, потому что от неё зависит, повторит\n"
        "клиент запрос или остановится навсегда. Шесть реализаций, составивших её\n"
        "порознь, разойдутся именно на негативных ветках - там, где расхождение\n"
        "дороже всего и заметно позже всего.\n"
    )

    out = [
        HEADER.format(
            title="Соответствие вердиктов классификатора ошибкам.",
            source="spec/protocol/response-classes.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from typing import Final\n\nfrom .errors import (\n"
            + "".join(
                f"    {name},\n"
                for name in sorted(
                    {by_stable[sid] for rows in table.values() for sid in rows.values() if sid}
                )
            )
            + ")",
        )
    ]

    out.append('__all__ = ["VERDICT_ERRORS", "RESPONSE_CLASSES"]\n')

    out.append("\n#: Классы ответа, объявленные спецификацией.\n")
    out.append("#:\n")
    out.append("#: Перечень нужен, чтобы проверить полноту таблицы: класс без единой\n")
    out.append("#: записи означает, что реализации выберут ошибку сами.\n")
    out.append("RESPONSE_CLASSES: Final[frozenset[str]] = frozenset(\n    {\n")
    for name in doc["classes"]:
        out.append(f'        "{name}",\n')
    out.append("    }\n)\n")

    out.append("\n#: Пара «класс ответа, причина» и ошибка, которую она означает.\n")
    out.append("VERDICT_ERRORS: Final[dict[tuple[str, str], type[Exception] | None]] = {\n")
    for cls, rows in table.items():
        for reason, stable_id in rows.items():
            value = by_stable[stable_id] if stable_id else "None"
            out.append(f'    ("{cls}", "{reason}"): {value},\n')
    out.append("}\n")

    return "".join(out)


#: Что порождается: имя файла в пакете и функция, которая его строит.
TARGETS: Final[dict[str, Callable[[Path], str]]] = {
    "errors.py": render_errors,
    "capabilities.py": render_capabilities,
    "response_classes.py": render_response_classes,
}


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
