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


#: Файлы спецификации, которые читает генератор.
#:
#: Перечень объявлен, а не выводится по факту чтения, и это не бюрократия.
#: Проверка покрытия сверяет с ним реестр spec/conformance/coverage.yaml, где у
#: каждого файла спецификации объявлен механизм связи с реализацией. Выведи
#: перечень по факту - и реестр начнёт сверяться сам с собой.
#:
#: Генератор отказывается читать необъявленное: файл, добавленный в чтение
#: молча, обошёл бы реестр и остался бы вне покрытия, оставаясь при этом
#: прочитанным. Ровно такая незаметность и была причиной завести реестр.
SOURCES: Final[frozenset[str]] = frozenset(
    {
        "spec/capabilities.yaml",
        "spec/errors/errors.yaml",
        "spec/events/delivery.yaml",
        "spec/extraction/orders.yaml",
        "spec/extraction/session.yaml",
        "spec/protocol/response-classes.yaml",
        "spec/protocol/retry-policy.yaml",
        "spec/runtime/budget.yaml",
        "spec/services/account.yaml",
        "spec/services/catalog.yaml",
        "spec/services/chats.yaml",
        "spec/services/lots.yaml",
        "spec/services/market.yaml",
        "spec/services/orders.yaml",
        "spec/version.yaml",
    }
)


def _load(spec: Path, relative: str) -> dict[str, Any]:
    """Читает файл спецификации.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.
        relative (str): Путь файла относительно корня.

    Returns:
        dict[str, Any]: Разобранный документ.

    Raises:
        FileNotFoundError: Если файла нет.
        SystemExit: Если файл не объявлен в SOURCES.
    """
    if relative not in SOURCES:
        raise SystemExit(
            f"генератор читает {relative}, не объявив этого в SOURCES. "
            "Необъявленное чтение обходит реестр покрытия: файл оказывается "
            "прочитанным и при этом вне учёта"
        )
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
            "from enum import StrEnum\nfrom typing import Final, NoReturn",
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

    out.append("\n    def __bool__(self) -> NoReturn:\n")
    out.append('        """Запрещает приведение к булеву значению.\n\n')
    out.append("        Состояний пять, и к двум они не сводятся. Запись\n")
    out.append("        ``if client.capability(cap): call()`` выглядит настолько\n")
    out.append("        естественно, что её пишут не задумываясь, - а состояние это\n")
    out.append("        строка, и любая непустая строка истинна. Проверка пропускала\n")
    out.append("        вызов при unsupported: ровно тот случай, ради которого она и\n")
    out.append("        написана.\n\n")
    out.append("        Тот же запрет стоит у Observed и по той же причине.\n\n")
    out.append("        Raises:\n")
    out.append("            TypeError: Всегда.\n")
    out.append('        """\n')
    out.append("        raise TypeError(\n")
    out.append('            "CapabilityState нельзя привести к булеву значению: состояний "\n')
    out.append('            "пять, а не два, и unsupported истинно как непустая строка. "\n')
    out.append('            "Спросите allows_call(opted_in=...), если нужен факт "\n')
    out.append('            "допустимости вызова, либо сравните состояние с нужным"\n')
    out.append("        )\n")

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

    out.append('__all__ = ["VERDICT_ERRORS", "RESPONSE_CLASSES", "STATUS_CLASS"]\n')

    out.append("\n#: Классы ответа, объявленные спецификацией.\n")
    out.append("#:\n")
    out.append("#: Перечень нужен, чтобы проверить полноту таблицы: класс без единой\n")
    out.append("#: записи означает, что реализации выберут ошибку сами.\n")
    out.append("RESPONSE_CLASSES: Final[frozenset[str]] = frozenset(\n    {\n")
    for name in doc["classes"]:
        out.append(f'        "{name}",\n')
    out.append("    }\n)\n")

    session = _load(spec, "spec/extraction/session.yaml")
    codes = {int(key): value for key, value in session["status_codes"].items() if key.isdigit()}
    strange = sorted(value for value in codes.values() if value not in doc["classes"])
    if strange:
        raise SystemExit(
            f"spec/extraction/session.yaml: коды ответа отображаются в {strange}, "
            "а таких классов ответа нет. Имена обязаны совпадать со словарём из "
            "spec/protocol/response-classes.yaml: иначе перевод одного имени в "
            "другое остаётся рукописным и нигде не записанным"
        )

    out.append("\n#: Класс ответа по коду, когда тело разбирать бессмысленно.\n")
    out.append("#:\n")
    out.append("#: Прежде таблица жила рукописной копией в классификаторе. Правка\n")
    out.append("#: спецификации не давала ни одного признака: сборка зелёная,\n")
    out.append("#: спецификация зелёная, а расхождение обнаружилось бы в работе - и\n")
    out.append("#: ровно на той ошибке, от которой спецификация предостерегает\n")
    out.append("#: отдельным разделом: код 429, принятый за блокировку, навсегда\n")
    out.append("#: останавливает опрос.\n")
    out.append("STATUS_CLASS: Final[dict[int, str]] = {\n")
    for code in sorted(codes):
        out.append(f'    {code}: "{codes[code]}",\n')
    out.append("}\n")

    out.append("\n#: Пара «класс ответа, причина» и ошибка, которую она означает.\n")
    out.append("VERDICT_ERRORS: Final[dict[tuple[str, str], type[Exception] | None]] = {\n")
    for cls, rows in table.items():
        for reason, stable_id in rows.items():
            value = by_stable[stable_id] if stable_id else "None"
            out.append(f'    ("{cls}", "{reason}"): {value},\n')
    out.append("}\n")

    return "".join(out)


def render_retry(spec: Path) -> str:
    """Порождает таблицу политик повторов.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.
    """
    doc = _load(spec, "spec/protocol/retry-policy.yaml")
    policies: dict[str, Any] = doc["policies"]
    limits: dict[str, Any] = doc["limits"]

    fallbacks = [name for name, entry in policies.items() if entry.get("fallback")]
    if len(fallbacks) != 1:
        raise ValueError(
            "spec/protocol/retry-policy.yaml: запасная политика обязана быть "
            f"ровно одна, найдено {len(fallbacks)}"
        )

    extra = (
        "Числа не выбираются реализацией. Отступление, синхронизированное между\n"
        "шестью SDK, превращается в согласованную волну запросов, и площадка\n"
        "видит не шесть вежливых клиентов, а один невежливый.\n"
        "\n"
        "Запасная политика намеренно строже конкретных: неизвестный класс отказа\n"
        "не повод быть смелее.\n"
    )

    out = [
        HEADER.format(
            title="Политики повторов.",
            source="spec/protocol/retry-policy.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from dataclasses import dataclass\nfrom enum import StrEnum\nfrom typing import Final",
        )
    ]

    out.append(
        "__all__ = [\n"
        '    "RetryPolicy",\n'
        '    "RETRY_POLICIES",\n'
        '    "FALLBACK_POLICY",\n'
        '    "GLOBAL_MAX_ATTEMPTS",\n'
        '    "RetryDecision",\n'
        '    "DECISION_MATRIX",\n'
        "]\n"
    )

    out.append("\n\n@dataclass(frozen=True, slots=True)\n")
    out.append("class RetryPolicy:\n")
    out.append('    """Политика повторов для одного класса ошибки.\n\n')
    out.append("    Attributes:\n")
    out.append("        stable_id (str): Устойчивый идентификатор класса ошибки.\n")
    out.append("        max_attempts (int): Сколько попыток допустимо всего, включая первую.\n")
    out.append("        base_ms (int): Основа задержки, миллисекунды.\n")
    out.append("        multiplier (float): Во сколько раз растёт задержка.\n")
    out.append("        cap_ms (int): Потолок задержки, миллисекунды.\n")
    out.append("        jitter (str): Вид разброса.\n")
    out.append("        respect_retry_after (bool): Уважать ли заголовок Retry-After.\n")
    out.append("        max_retry_after_ms (int): Верхняя граница уважения заголовка.\n")
    out.append("        fail_closed (bool): Останавливать ли работу до вмешательства.\n")
    out.append("        account_scoped (bool): Действует ли ограничение на весь аккаунт.\n")
    out.append('    """\n\n')
    out.append("    stable_id: str\n")
    out.append("    max_attempts: int\n")
    out.append("    base_ms: int\n")
    out.append("    multiplier: float\n")
    out.append("    cap_ms: int\n")
    out.append("    jitter: str\n")
    out.append("    respect_retry_after: bool\n")
    out.append("    max_retry_after_ms: int\n")
    out.append("    fail_closed: bool\n")
    out.append("    account_scoped: bool\n")

    default_after = limits["max_retry_after_ms"]["value"]
    out.append("\n\n#: Политика по устойчивому идентификатору класса ошибки.\n")
    out.append("RETRY_POLICIES: Final[dict[str, RetryPolicy]] = {\n")
    for name, entry in policies.items():
        out.append(f'    "{name}": RetryPolicy(\n')
        out.append(f'        stable_id="{name}",\n')
        out.append(f"        max_attempts={entry['max_attempts']},\n")
        out.append(f"        base_ms={entry.get('base_ms', 0)},\n")
        out.append(f"        multiplier={float(entry.get('multiplier', 1))},\n")
        out.append(f"        cap_ms={entry.get('cap_ms', 0)},\n")
        out.append(f'        jitter="{entry.get("jitter", limits["jitter"]["kind"])}",\n')
        out.append(f"        respect_retry_after={bool(entry.get('respect_retry_after'))},\n")
        out.append(
            f"        max_retry_after_ms={entry.get('max_retry_after_ms', default_after)},\n"
        )
        out.append(f"        fail_closed={bool(entry.get('fail_closed'))},\n")
        out.append(f"        account_scoped={bool(entry.get('account_scoped'))},\n")
        out.append("    ),\n")
    out.append("}\n")

    out.append("\n#: Политика для классов ошибок без собственной записи.\n")
    out.append("#:\n")
    out.append("#: Строже конкретных намеренно: неизвестный класс отказа не повод быть\n")
    out.append("#: смелее. Реализация, подставляющая здесь самую щедрую политику,\n")
    out.append("#: получает самое агрессивное поведение как раз тогда, когда меньше\n")
    out.append("#: всего понимает происходящее.\n")
    out.append(f'FALLBACK_POLICY: Final[RetryPolicy] = RETRY_POLICIES["{fallbacks[0]}"]\n')

    out.append("\n#: Потолок числа попыток независимо от политики класса ошибки.\n")
    out.append(f"GLOBAL_MAX_ATTEMPTS: Final[int] = {limits['global_max_attempts']['value']}\n")

    matrix = doc.get("decision_matrix")
    if not matrix:
        raise SystemExit(
            "spec/protocol/retry-policy.yaml: матрица решения о повторе отсутствует. "
            "Без неё каждая реализация решает сама, и одна повторит отправку "
            "сообщения, а другая нет - на одной и той же трассе"
        )

    known_row = {
        "error_retryable",
        "operation_safety",
        "error_side_effects_possible",
        "requires",
        "result",
        "summary",
    }
    results: list[str] = []
    for index, row in enumerate(matrix):
        unknown = sorted(set(row) - known_row)
        if unknown:
            raise SystemExit(
                f"spec/protocol/retry-policy.yaml: в строке {index} матрицы поля "
                f"{unknown}, а генератор о них не знает. Молча уронить их значило "
                "бы решать о повторе по неполному правилу"
            )
        if row["result"] not in results:
            results.append(row["result"])

    out.append("\n\nclass RetryDecision(StrEnum):\n")
    out.append('    """Что матрица говорит о повторе.\n\n')
    out.append("    Решение о повторе - пересечение класса ошибки и безопасности\n")
    out.append("    операции. Реализация, сводящая матрицу к «повторяем только\n")
    out.append("    чтения», строже контракта: это безопасно, но расходится -\n")
    out.append("    второй SDK на той же трассе поступит иначе.\n")
    out.append('    """\n\n')
    for value in results:
        out.append(f'    {value.upper()} = "{value}"\n')

    out.append("\n\n#: Строки матрицы решения о повторе, в порядке спецификации.\n")
    out.append("#:\n")
    out.append("#: Порядок значим: строки читаются сверху вниз, и первая подошедшая\n")
    out.append("#: решает. Первая строка отсекает неповторяемый класс ошибки\n")
    out.append("#: независимо от операции.\n")
    out.append("#:\n")
    out.append("#: Кортеж: повторяем ли класс ошибки; безопасность операции либо\n")
    out.append("#: None, если строка о любой; возможен ли побочный эффект либо None,\n")
    out.append("#: если неважно; решение.\n")
    out.append(
        "DECISION_MATRIX: Final[tuple[tuple[bool, str | None, bool | None, "
        "RetryDecision], ...]] = (" + chr(10)
    )
    for row in matrix:
        safety = row.get("operation_safety")
        effects = row.get("error_side_effects_possible")
        safety_text = "None" if safety is None else f'"{safety}"'
        effects_text = "None" if effects is None else str(bool(effects))
        out.append(
            f"    ({bool(row['error_retryable'])}, {safety_text}, {effects_text}, "
            f"RetryDecision.{row['result'].upper()}),\n"
        )
    out.append(")\n")

    return "".join(out)


def render_budget(spec: Path) -> str:
    """Порождает числа бюджета запросов.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.
    """
    doc = _load(spec, "spec/runtime/budget.yaml")
    buckets: dict[str, Any] = doc["buckets"]
    limits: dict[str, Any] = doc["limits"]

    extra = (
        "Числа помечены в спецификации провизорными: измерять настоящие пороги\n"
        "площадки означало бы намеренно их превышать. Поэтому они подобраны\n"
        "консервативно и будут уточняться наблюдением, а не подбором.\n"
        "\n"
        "Расходуются отправленные запросы, включая повторы и переходы по\n"
        "редиректам. Считать только логические операции нельзя: тогда шторм\n"
        "повторов оказывается бесплатным ровно в тот момент, когда площадке\n"
        "хуже всего.\n"
    )

    out = [
        HEADER.format(
            title="Числа бюджета запросов.",
            source="spec/runtime/budget.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from dataclasses import dataclass\nfrom typing import Final",
        )
    ]

    out.append("__all__ = [\n")
    for name in (
        "BucketLimits",
        "BUCKETS",
        "MAX_WAIT_MS",
        "COUNTS_RETRIES",
        "COUNTS_REDIRECTS",
        "MAX_QUEUE_DEPTH_PER_KEY",
        "MAX_CONCURRENT_HANDLERS",
        "HANDLER_TIMEOUT_MS",
        "MAX_CONNECTIONS_PER_HOST",
        "MAX_RESPONSE_BYTES",
        "MAX_DECOMPRESSED_BYTES",
        "MAX_REDIRECTS",
        "RateLimitResponse",
        "RATE_LIMIT_RESPONSE",
        "Scheduling",
        "SCHEDULING",
        "PROVISIONAL",
    ):
        out.append(f'    "{name}",\n')
    out.append("]\n")

    out.append("\n\n@dataclass(frozen=True, slots=True)\n")
    out.append("class BucketLimits:\n")
    out.append('    """Ёмкость и скорость пополнения одного ведра.\n\n')
    out.append("    Attributes:\n")
    out.append("        name (str): Имя ведра.\n")
    out.append("        capacity (int): Сколько запросов помещается всего.\n")
    out.append("        refill_per_second (float): Сколько восстанавливается за секунду.\n")
    out.append("        burst (int): Сколько можно потратить залпом.\n")
    out.append('    """\n\n')
    out.append("    name: str\n")
    out.append("    capacity: int\n")
    out.append("    refill_per_second: float\n")
    out.append("    burst: int\n")

    out.append("\n\n#: Вёдра бюджета. Вложены: запрос расходует сначала общее, потом ведро\n")
    out.append("#: аккаунта. Порядок нормативен, иначе при нескольких аккаунтах в одном\n")
    out.append("#: процессе общий предел обходится.\n")
    out.append("BUCKETS: Final[dict[str, BucketLimits]] = {\n")
    for name, entry in buckets.items():
        out.append(f'    "{name}": BucketLimits(\n')
        out.append(f'        name="{name}",\n')
        out.append(f"        capacity={entry['capacity']},\n")
        out.append(f"        refill_per_second={float(entry['refill_per_second'])},\n")
        out.append(f"        burst={entry['burst']},\n")
        out.append("    ),\n")
    out.append("}\n")

    out.append("\n#: Сколько ждать освобождения бюджета, прежде чем отказать.\n")
    out.append(f"MAX_WAIT_MS: Final[int] = {doc['exhausted']['max_wait_ms']}\n")

    out.append("\n#: Расходуют ли бюджет повторы.\n")
    out.append(f"COUNTS_RETRIES: Final[bool] = {bool(doc['counting']['counts_retries'])}\n")

    out.append("\n#: Расходуют ли бюджет переходы по редиректам.\n")
    out.append(f"COUNTS_REDIRECTS: Final[bool] = {bool(doc['counting']['counts_redirects'])}\n")

    out.append("\n#: Предел числа переходов на один запрос.\n")
    # Каждое число из раздела limits попадает в модуль. Прежде попадало одно из
    # семи, а остальные оседали литералами в транспорте и в цикле - то есть
    # правка спецификации меняла порождённый файл и не меняла поведение. Молча.
    #
    # Ключи перечислены поимённо, а не обходятся циклом, потому что каждому нужна
    # своя единица измерения в комментарии. Неизвестный ключ - отказ: раздел
    # дописали, а генератор об этом не знает, и знать об этом должен человек.
    known_limits = {
        "max_queue_depth_per_key": (
            "MAX_QUEUE_DEPTH_PER_KEY",
            "int",
            "Сколько событий помещается в очередь одного ключа упорядочивания, штук.",
        ),
        "max_concurrent_handlers": (
            "MAX_CONCURRENT_HANDLERS",
            "int",
            "Сколько обработчиков выполняется одновременно, штук.",
        ),
        "handler_timeout_ms": (
            "HANDLER_TIMEOUT_MS",
            "int",
            "Сколько ждать обработчик, прежде чем счесть его зависшим, миллисекунды.",
        ),
        "max_connections_per_host": (
            "MAX_CONNECTIONS_PER_HOST",
            "int",
            "Сколько соединений с одним хостом держать одновременно, штук.",
        ),
        "max_response_bytes": (
            "MAX_RESPONSE_BYTES",
            "int",
            "Предел размера полученного тела, байты.",
        ),
        "max_decompressed_bytes": (
            "MAX_DECOMPRESSED_BYTES",
            "int",
            "Предел размера тела после распаковки, байты.",
        ),
        "max_redirects": (
            "MAX_REDIRECTS",
            "int",
            "Предел числа переходов при ручном следовании, штук.",
        ),
    }
    unknown = sorted(
        key
        for key in limits
        if key not in known_limits and not key.endswith("_rule") and not key.endswith("_note")
    )
    if unknown:
        raise SystemExit(
            f"spec/runtime/budget.yaml: раздел limits содержит {unknown}, "
            "а генератор о них не знает. Молча уронить их значило бы завести "
            "число в контракте, которого нет в реализации: правка спецификации "
            "меняла бы порождённый файл и не меняла поведение"
        )

    for key, (const_name, kind, note) in known_limits.items():
        if key not in limits:
            raise SystemExit(f"spec/runtime/budget.yaml: в разделе limits нет {key}")
        out.append(f"\n#: {note}\n")
        out.append(f"{const_name}: Final[{kind}] = {limits[key]}\n")

    reaction = doc["rate_limit_response"]
    known_reaction = {
        "first",
        "second_in_window",
        "third_in_window",
        "recovery",
        "capacity_multiplier",
        "min_capacity_factor",
        "min_capacity_note",
        "cooldown_ms",
        "cooldown_note",
        "window_ms",
        "window_note",
        "successes_per_step",
        "recovery_multiplier",
        "successes_note",
        "asymmetry_rule",
    }
    unknown = sorted(set(reaction) - known_reaction)
    if unknown:
        raise SystemExit(
            f"spec/runtime/budget.yaml: в реакции на ограничение поля {unknown}, "
            "а генератор о них не знает. Реакция на ограничение - последнее, что "
            "стоит ронять молча"
        )
    if not 0 < reaction["capacity_multiplier"] < 1:
        raise SystemExit(
            "spec/runtime/budget.yaml: множитель ёмкости при ограничении обязан "
            "быть между нулём и единицей - иначе ограничение не уменьшает темп"
        )
    if reaction["recovery_multiplier"] <= 1:
        raise SystemExit(
            "spec/runtime/budget.yaml: множитель восстановления обязан быть больше "
            "единицы - иначе ёмкость не возвращается никогда"
        )

    out.append("\n\n@dataclass(frozen=True, slots=True)\n")
    out.append("class RateLimitResponse:\n")
    out.append('    """Как источник отвечает на ограничение частоты.\n\n')
    out.append("    Восстановление медленнее падения намеренно. Симметричное\n")
    out.append("    восстановление даёт автоколебания: система отступает, тут же\n")
    out.append("    возвращается к прежней частоте, получает ограничение снова и так\n")
    out.append("    по кругу.\n\n")
    out.append("    Attributes:\n")
    out.append("        capacity_multiplier (float): На сколько умножается ёмкость.\n")
    out.append("        min_capacity_factor (float): Ниже этой доли ёмкость не падает.\n")
    out.append("        cooldown_ms (int): Остывание за первое ограничение, мс.\n")
    out.append("        window_ms (int): Окно учёта ограничений, мс.\n")
    out.append("        successes_per_step (int): Успехов подряд на шаг восстановления.\n")
    out.append("        recovery_multiplier (float): Во сколько раз растёт ёмкость.\n")
    out.append('    """\n\n')
    out.append("    capacity_multiplier: float\n")
    out.append("    min_capacity_factor: float\n")
    out.append("    cooldown_ms: int\n")
    out.append("    window_ms: int\n")
    out.append("    successes_per_step: int\n")
    out.append("    recovery_multiplier: float\n")

    out.append("\n\n#: Реакция на ограничение частоты.\n")
    out.append("#:\n")
    out.append("#: Раздел долго называл ступени именами и не давал ни одного числа:\n")
    out.append("#: реализация не могла его выполнить, даже захотев.\n")
    out.append("RATE_LIMIT_RESPONSE: Final[RateLimitResponse] = RateLimitResponse(\n")
    for field_name in (
        "capacity_multiplier",
        "min_capacity_factor",
        "cooldown_ms",
        "window_ms",
        "successes_per_step",
        "recovery_multiplier",
    ):
        out.append(f"    {field_name}={reaction[field_name]!r},\n")
    out.append(")\n")

    schedule = doc["scheduling"]
    out.append("\n\n@dataclass(frozen=True, slots=True)\n")
    out.append("class Scheduling:\n")
    out.append('    """Числа расписания опроса.\n\n')
    out.append("    Attributes:\n")
    out.append("        active_interval_ms (int): Интервал при активном аккаунте.\n")
    out.append("        idle_step_multiplier (float): Во сколько раз растёт интервал в покое.\n")
    out.append("        max_interval_ms (int): Потолок интервала.\n")
    out.append("        activity_window_ms (int): Окно, в котором аккаунт считается активным.\n")
    out.append("        min_floor_ms (int): Нижний предел интервала.\n")
    out.append('    """\n\n')
    out.append("    active_interval_ms: int\n")
    out.append("    idle_step_multiplier: float\n")
    out.append("    max_interval_ms: int\n")
    out.append("    activity_window_ms: int\n")
    out.append("    min_floor_ms: int\n")

    out.append("\n\n#: Расписание опроса.\n")
    out.append("#:\n")
    out.append("#: Нижний предел интервала обычной настройкой не понижается. Это\n")
    out.append("#: единственное число, которое защищает площадку от слишком уверенного\n")
    out.append("#: пользователя, а аккаунт пользователя - от него самого.\n")
    out.append("SCHEDULING: Final[Scheduling] = Scheduling(\n")
    out.append(f"    active_interval_ms={schedule['active_interval_ms']},\n")
    out.append(f"    idle_step_multiplier={float(schedule['idle_step_multiplier'])},\n")
    out.append(f"    max_interval_ms={schedule['max_interval_ms']},\n")
    out.append(f"    activity_window_ms={schedule['activity_window_ms']},\n")
    out.append(f"    min_floor_ms={schedule['min_floor_ms']},\n")
    out.append(")\n")

    out.append("\n#: Признак того, что числа подобраны, а не измерены.\n")
    out.append("#:\n")
    out.append("#: Снимается только тогда, когда пороги станут известны из наблюдений.\n")
    out.append("#: Измерять их намеренным превышением нельзя.\n")
    out.append(f"PROVISIONAL: Final[bool] = {bool(doc.get('provisional', True))}\n")

    return "".join(out)


def render_events(spec: Path) -> str:
    """Порождает типы событий и правило вывода ключа упорядочивания.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.
    """
    doc = _load(spec, "spec/events/delivery.yaml")
    derivation: dict[str, Any] = doc["ordering"]["derivation"]
    identity: dict[str, Any] = doc["identity"]
    dedup: dict[str, Any] = doc["deduplication"]

    if not doc["ordering"].get("key_required"):
        raise ValueError(
            "spec/events/delivery.yaml: ключ упорядочивания обязан быть объявлен обязательным"
        )

    extra = (
        "Правило вывода ключа упорядочивания нормативно. Две реализации,\n"
        "выведшие разные ключи, получат разную степень параллелизма и разный\n"
        "наблюдаемый порядок - при полном согласии в том, какие события бывают.\n"
        "\n"
        "Поля, запрещённые в отпечатке события, перечислены здесь же. Момент\n"
        "наблюдения и версия адаптера меняются от запуска к запуску и от релиза\n"
        "к релизу; включение любого из них обнулит дедупликацию ровно там, где\n"
        "она нужнее всего - после перезапуска.\n"
    )

    out = [
        HEADER.format(
            title="Типы событий и вывод ключа упорядочивания.",
            source="spec/events/delivery.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from enum import StrEnum\nfrom typing import Final",
        )
    ]

    out.append("__all__ = [\n")
    for name in (
        "EventType",
        "ORDERING_KEY",
        "FINGERPRINT_FIELDS",
        "FINGERPRINT_SEPARATOR",
        "FINGERPRINT_HASH",
        "FINGERPRINT_DIGEST_BYTES",
        "FINGERPRINT_LENGTH",
        "MIN_ENTRIES_PER_KEY",
        "EVENT_LANE",
        "LANE_DROPPABLE",
        "DEDUP_TTL_MS",
    ):
        out.append(f'    "{name}",\n')
    out.append("]\n")

    out.append("\n\nclass EventType(StrEnum):\n")
    out.append('    """Тип события.\n\n')
    out.append("    Значение совпадает с именем типа в спецификации: оно уходит в журнал\n")
    out.append("    и в конверт события, где обязано совпадать между всеми реализациями.\n")
    out.append('    """\n\n')
    for name in derivation:
        out.append(f'    {_const(name)} = "{name}"\n')

    out.append("\n\n#: Шаблон ключа упорядочивания для каждого типа события.\n")
    out.append("#:\n")
    out.append("#: Порядок сохраняется внутри одного ключа. События с разными ключами\n")
    out.append("#: обрабатываются параллельно и порядка между собой не имеют.\n")
    out.append("ORDERING_KEY: Final[dict[EventType, str]] = {\n")
    for name, template in derivation.items():
        out.append(f'    EventType.{_const(name)}: "{template}",\n')
    out.append("}\n")

    out.append("\n#: Поля, из которых строится отпечаток события.\n")
    out.append("#:\n")
    out.append("#: Перечень закрытый. Добавление поля меняет идентичность всех событий\n")
    out.append("#: сразу, то есть обнуляет дедупликацию и сохранённые ключи\n")
    out.append("#: идемпотентности.\n")
    out.append("FINGERPRINT_FIELDS: Final[tuple[str, ...]] = (\n")
    for field_name in identity["fingerprint_from"]:
        out.append(f'    "{field_name}",\n')
    out.append(")\n")

    algorithm: dict[str, Any] = doc["fingerprint_algorithm"]
    if algorithm.get("separator") != "U+001F":
        raise SystemExit(
            "spec/events/delivery.yaml: разделитель отпечатка обязан быть U+001F - "
            "все части приходят снаружи, и печатный разделитель рано или поздно "
            "встретится внутри части, склеив две разные четвёрки в одну строку"
        )

    out.append("\n#: Чем разделяются части при склейке перед хэшированием.\n")
    out.append("#:\n")
    out.append("#: Управляющий знак, а не печатный: все части приходят снаружи, и любой\n")
    out.append("#: печатный разделитель рано или поздно встретится внутри части. Тогда две\n")
    out.append("#: разные четвёрки склеятся в одну строку, и два разных события получат\n")
    out.append("#: один отпечаток - молча.\n")
    out.append('FINGERPRINT_SEPARATOR: Final[str] = "\\x1f"\n')

    out.append("\n#: Имя алгоритма хэширования из hashlib.\n")
    out.append("#:\n")
    out.append("#: Выбор не про стойкость: отпечаток не защищает ни от кого, он различает.\n")
    out.append("#: Зафиксирован он потому, что должен совпасть у шести реализаций.\n")
    out.append(f'FINGERPRINT_HASH: Final[str] = "{algorithm["hash"]}"\n')

    out.append("\n#: Длина хэша в байтах.\n")
    out.append(f"FINGERPRINT_DIGEST_BYTES: Final[int] = {algorithm['digest_size_bytes']}\n")

    out.append("\n#: Длина отпечатка в знаках шестнадцатеричной записи.\n")
    out.append("#:\n")
    out.append("#: Зафиксирована отдельно от длины хэша, чтобы реализация не удлинила её\n")
    out.append("#: вслед за сменой алгоритма, не заметив, что этим обнулила сохранённые\n")
    out.append("#: ключи идемпотентности у всех, кто уже работает.\n")
    out.append(f"FINGERPRINT_LENGTH: Final[int] = {algorithm['length_chars']}\n")

    out.append("\n#: Сколько записей о ключе упорядочивания хранится минимум.\n")
    out.append("#:\n")
    out.append("#: Число объявлено спецификацией и прежде совпадало с ним по\n")
    out.append("#: совпадению: в реализации оно было литералом. Слишком малое\n")
    out.append("#: значение вытесняет запись о доставленном событии до истечения\n")
    out.append("#: срока, и событие приходит второй раз - тихо и не всегда.\n")
    out.append(f"MIN_ENTRIES_PER_KEY: Final[int] = {dedup['min_entries_per_key']}\n")

    out.append("\n#: Сколько хранится запись о доставленном событии, миллисекунды.\n")
    out.append(f"DEDUP_TTL_MS: Final[int] = {dedup['ttl_ms']}\n")

    lanes = doc.get("backpressure", {}).get("lanes")
    if not lanes:
        raise SystemExit(
            "spec/events/delivery.yaml: полосы очереди не объявлены. Без них "
            "реализация сама решает, какое событие можно выбросить при "
            "переполнении, - и выбросит сообщение о потере событий"
        )

    known_lane = {"carries", "droppable", "rule", "coalescing"}
    by_type: dict[str, str] = {}
    for lane, body in lanes.items():
        unknown = sorted(set(body) - known_lane)
        if unknown:
            raise SystemExit(
                f"spec/events/delivery.yaml: у полосы {lane} поля {unknown}, а "
                "генератор о них не знает"
            )
        for kind in body["carries"]:
            if kind in by_type:
                raise SystemExit(
                    f"spec/events/delivery.yaml: вид {kind} отнесён сразу к "
                    f"полосам {by_type[kind]} и {lane}"
                )
            by_type[kind] = lane

    missing = sorted(set(derivation) - set(by_type))
    if missing:
        raise SystemExit(
            f"spec/events/delivery.yaml: виды {missing} не отнесены ни к одной "
            "полосе. Реализация решит сама, можно ли их выбрасывать"
        )

    out.append("\n\n#: Полоса очереди, к которой относится вид события.\n")
    out.append("#:\n")
    out.append("#: Полоса решает две вещи: можно ли выбросить событие при\n")
    out.append("#: переполнении и считается ли оно признаком активности. События о\n")
    out.append("#: самом наблюдении - приветствие, жалоба на неполноту, сообщение о\n")
    out.append("#: потере - данными не являются, и держать по ним опрос на\n")
    out.append("#: минимальном интервале значит стучаться в площадку из-за\n")
    out.append("#: собственного состояния.\n")
    out.append("EVENT_LANE: Final[dict[EventType, str]] = {\n")
    for name in derivation:
        out.append(f'    EventType.{_const(name)}: "{by_type[name]}",\n')
    out.append("}\n")

    out.append("\n#: Можно ли выбрасывать события полосы при переполнении.\n")
    out.append("LANE_DROPPABLE: Final[dict[str, bool]] = {\n")
    for lane in sorted(lanes):
        out.append(f'    "{lane}": {bool(lanes[lane].get("droppable"))},\n')
    out.append("}\n")

    return "".join(out)


def render_contract(spec: Path) -> str:
    """Порождает сведения о версии контракта.

    Пакет не нёс машиночитаемого ответа на вопрос «какую версию контракта я
    реализую». При шести SDK и независимом версионировании спецификации это и
    есть тот вопрос, ради которого version.yaml заведён.

    Семейство адаптера жило рукописной константой в _state.py, а перечень
    поддерживаемых локалей - нигде: заголовок Accept-Language в транспорте
    собирался из своих значений.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.

    Raises:
        SystemExit: Если в файле версии незнакомый ключ.
    """
    doc = _load(spec, "spec/version.yaml")

    known = {
        "spec_version",
        "status",
        "canonical_form_version",
        "runner_protocol",
        "supported_locales",
        "adapter_family",
    }
    unknown = sorted(set(doc) - known)
    if unknown:
        raise SystemExit(
            f"spec/version.yaml: ключи {unknown}, а генератор о них не знает. "
            "Молча уронить их значило бы завести ось версионирования, о которой "
            "реализация не подозревает"
        )

    extra = (
        "Три оси версий разведены намеренно. Версия спецификации говорит, какой\n"
        "контракт реализован. Версия канонической формы меняется отдельно: одна\n"
        "и та же модель может сериализоваться по-новому, и это ломает\n"
        "сохранённые отпечатки и ключи гашения повторов.\n"
        "\n"
        "Семейство адаптера отделяет состояние, снятое с одной площадки, от\n"
        "состояния другой: совпадение идентификаторов было бы случайным, а\n"
        "последствия - молчаливым гашением чужих событий.\n"
    )

    out = [
        HEADER.format(
            title="Версия контракта, которую реализует пакет.",
            source="spec/version.yaml",
            extra=extra,
        ).replace("from typing import ClassVar, Final", "from typing import Final")
    ]

    out.append(
        "__all__ = [\n"
        '    "SPEC_VERSION",\n'
        '    "SPEC_STATUS",\n'
        '    "CANONICAL_FORM_VERSION",\n'
        '    "RUNNER_PROTOCOL",\n'
        '    "SUPPORTED_LOCALES",\n'
        '    "ADAPTER_FAMILY",\n'
        "]\n"
    )

    out.append("\n#: Версия спецификации, которую реализует пакет.\n")
    out.append(f'SPEC_VERSION: Final[str] = "{doc["spec_version"]}"\n')

    out.append("\n#: Состояние спецификации: draft либо released.\n")
    out.append("#:\n")
    out.append("#: В состоянии draft контракт может меняться без соблюдения правил\n")
    out.append("#: совместимости, и классификация изменений носит осведомительный\n")
    out.append("#: характер.\n")
    out.append(f'SPEC_STATUS: Final[str] = "{doc["status"]}"\n')

    out.append("\n#: Версия правил канонической сериализации.\n")
    out.append("#:\n")
    out.append("#: Меняется отдельно от версии спецификации: одна и та же модель\n")
    out.append("#: может сериализоваться по-новому, и это ломает сохранённые\n")
    out.append("#: отпечатки и ключи гашения повторов.\n")
    out.append(f"CANONICAL_FORM_VERSION: Final[int] = {doc['canonical_form_version']}\n")

    out.append("\n#: Версия протокола запуска набора соответствия.\n")
    out.append(f"RUNNER_PROTOCOL: Final[int] = {doc['runner_protocol']}\n")

    out.append("\n#: Локали интерфейса, для которых у проекта есть снимки страниц.\n")
    out.append("#:\n")
    out.append("#: Локаль привязана к аккаунту, а не к адресу, и переключить её\n")
    out.append("#: запросом нельзя. При локали вне перечня реализация обязана\n")
    out.append("#: вернуть типизированную ошибку, но никогда - пустой результат.\n")
    out.append("SUPPORTED_LOCALES: Final[tuple[str, ...]] = (\n")
    for locale in doc["supported_locales"]:
        out.append(f'    "{locale}",\n')
    out.append(")\n")

    out.append("\n#: Семейство протокольного адаптера.\n")
    out.append("#:\n")
    out.append("#: Состояние, снятое с другой площадки, бессмысленно здесь целиком:\n")
    out.append("#: совпадение идентификаторов было бы случайным, а последствия -\n")
    out.append("#: молчаливым гашением чужих событий.\n")
    out.append(f'ADAPTER_FAMILY: Final[str] = "{doc["adapter_family"]}"\n')

    return "".join(out)


def render_operations(spec: Path) -> str:
    """Порождает таблицу операций служб.

    Раздел services спецификации не читался генератором вовсе, а именно там
    объявлена безопасность операции - половина нормативного входа решения о
    повторе. Вторая половина, класс ошибки, порождается из errors.yaml и
    проверяется на свежесть; первая жила рукописным перечислением в _retry.py с
    примечанием «значения взяты из спецификации».

    Смена безопасности операции с safe на unsafe не отражалась нигде: ни в
    порождённом коде, ни в проверке. Когда появится первая операция записи, цена
    этого расхождения станет ценой повторно отправленного сообщения.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.

    Raises:
        SystemExit: Если у операции незнакомое поле либо значение вне словаря.
    """
    services = sorted((spec / "spec" / "services").glob("*.yaml"))
    if not services:
        raise SystemExit("spec/services: не найдено ни одного файла служб")

    # Поля перечислены поимённо. Незнакомое поле - отказ: раздел дописали, а
    # генератор об этом не знает, и знать об этом должен человек. Ровно так из
    # budget.yaml молча терялись шесть чисел из семи.
    known = {
        "summary",
        "capability",
        "safety",
        "request_class",
        "returns",
        "returns_schema",
        "returns_note",
        "errors",
        "notes",
        "pagination",
        "pagination_planned",
        "idempotency_key_from",
        "requires_reconciliation",
        "cost_hint",
        "governor",
        "reversible_by",
        "transport_lane",
        "cacheable",
        "guards",
        "preconditions",
        "audit",
        "renamed_from",
        "rename_reason",
        "completeness_required",
    }
    safety_values = {"safe", "idempotent", "unsafe"}
    classes = set(_load(spec, "spec/runtime/budget.yaml")["classes"])
    capabilities = set(_load(spec, "spec/capabilities.yaml")["capabilities"])

    operations: dict[str, dict[str, Any]] = {}
    for path in services:
        doc = _load(spec, f"spec/services/{path.name}")
        for name, body in (doc.get("operations") or {}).items():
            unknown = sorted(set(body) - known)
            if unknown:
                raise SystemExit(
                    f"spec/services/{path.name}: у операции {name} поля {unknown}, "
                    "а генератор о них не знает. Молча уронить их значило бы "
                    "завести в контракте объявление, которого нет в реализации"
                )
            if body["safety"] not in safety_values:
                raise SystemExit(
                    f"spec/services/{path.name}: у операции {name} безопасность "
                    f"{body['safety']!r} вне словаря {sorted(safety_values)}"
                )
            if body["request_class"] not in classes:
                raise SystemExit(
                    f"spec/services/{path.name}: у операции {name} класс запроса "
                    f"{body['request_class']!r}, а такого класса нет в "
                    "spec/runtime/budget.yaml"
                )
            if body["capability"] not in capabilities:
                raise SystemExit(
                    f"spec/services/{path.name}: операция {name} названа "
                    f"возможностью {body['capability']!r}, которой нет в "
                    "spec/capabilities.yaml"
                )
            if name in operations:
                raise SystemExit(f"операция {name} объявлена дважды")
            operations[name] = body

    extra = (
        "Безопасность операции - половина нормативного входа решения о повторе.\n"
        "Вторая половина, класс ошибки, порождается из errors.yaml. Пока эта\n"
        "половина была рукописной, смена безопасности в спецификации не\n"
        "отражалась нигде - ни в коде, ни в проверке.\n"
        "\n"
        "Повторить небезопасную операцию значит выполнить её дважды: отправить\n"
        "покупателю второе сообщение, поднять лот второй раз, списать деньги\n"
        "второй раз.\n"
    )

    out = [
        HEADER.format(
            title="Операции служб и их свойства.",
            source="spec/services/*.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from dataclasses import dataclass\nfrom enum import StrEnum\nfrom typing import Final",
        )
    ]

    out.append('__all__ = ["Safety", "Operation", "OPERATIONS"]\n')

    out.append("\n\nclass Safety(StrEnum):\n")
    out.append('    """Безопасность операции при повторе.\n\n')
    out.append("    safe - повтор ничего не меняет: операция только читает.\n")
    out.append("    idempotent - повтор с тем же ключом идемпотентности даёт тот же\n")
    out.append("    результат.\n")
    out.append("    unsafe - повтор выполняет действие дважды.\n")
    out.append('    """\n\n')
    for value in sorted(safety_values):
        out.append(f'    {value.upper()} = "{value}"\n')

    out.append("\n\n@dataclass(frozen=True, slots=True)\n")
    out.append("class Operation:\n")
    out.append('    """Свойства одной операции службы.\n\n')
    out.append("    Attributes:\n")
    out.append("        name (str): Идентификатор операции.\n")
    out.append("        capability (str): Возможность, которой операция требует.\n")
    out.append("        safety (Safety): Безопасность при повторе.\n")
    out.append("        request_class (str): Класс запроса для бюджета.\n")
    out.append("        returns (str): Тип результата, как объявлен спецификацией.\n")
    out.append('    """\n\n')
    out.append("    name: str\n")
    out.append("    capability: str\n")
    out.append("    safety: Safety\n")
    out.append("    request_class: str\n")
    out.append("    returns: str\n")

    out.append("\n\n#: Операции служб по идентификатору.\n")
    out.append("OPERATIONS: Final[dict[str, Operation]] = {\n")
    for name in sorted(operations):
        body = operations[name]
        out.append(f'    "{name}": Operation(\n')
        out.append(f'        name="{name}",\n')
        out.append(f'        capability="{body["capability"]}",\n')
        out.append(f"        safety=Safety.{body['safety'].upper()},\n")
        out.append(f'        request_class="{body["request_class"]}",\n')
        out.append(f'        returns="{body["returns"]}",\n')
        out.append("    ),\n")
    out.append("}\n")

    return "".join(out)


def render_extraction(spec: Path) -> str:
    """Порождает словари извлечения: статусы заказа и присутствие контрагента.

    Оба словаря - контракт, а не деталь реализации. Шесть SDK обязаны
    согласиться в том, что класс text-primary означает оплаченный заказ, а класс
    online - присутствующего собеседника. Разойдись они здесь - и один бот
    выдавал бы товар там, где другой ждал бы оплаты.

    Args:
        spec (Path): Корень рабочей копии Funora-spec.

    Returns:
        str: Содержимое модуля.

    Raises:
        ValueError: Если спецификация закрыла перечисление статусов либо
            соответствие носителей статусам пусто.
    """
    doc = _load(spec, "spec/extraction/orders.yaml")
    mapping = doc["status_mapping"]
    entries: list[dict[str, Any]] = mapping["entries"]
    presence: dict[str, bool] = doc["fields"]["counterparty_online"]["vocabulary"]

    if not mapping.get("enum_is_open"):
        raise ValueError(
            "spec/extraction/orders.yaml: перечисление статусов обязано остаться "
            "открытым - наблюдались не все состояния, и закрытое перечисление "
            "отвергало бы остальные как ошибочные"
        )
    if not entries:
        raise ValueError("spec/extraction/orders.yaml: соответствие носителей статусам пусто")
    if not presence:
        raise ValueError("spec/extraction/orders.yaml: словарь присутствия пуст")

    extra = (
        "Носителей статуса два, и оба структурные: цветовой класс ячейки и\n"
        "модификатор строки. Читать надо ОБА. В наблюдении они совпали во всех\n"
        "восьми строках, и это свойство здесь используется как проверка: два\n"
        "независимых носителя ловят переименование любого из них, а один -\n"
        "меняет ответ молча.\n"
        "\n"
        "Перечисления открытые. Носитель, которого нет в словаре, даёт\n"
        "ненаблюдённое значение, а не unknown: unknown означал бы, что состояние\n"
        "прочитано и не опознано, тогда как оно не прочитано вовсе.\n"
    )

    out = [
        HEADER.format(
            title="Словари извлечения: статусы заказа и присутствие контрагента.",
            source="spec/extraction/orders.yaml",
            extra=extra,
        ).replace(
            "from typing import ClassVar, Final",
            "from enum import StrEnum\nfrom typing import Final",
        )
    ]

    out.append("__all__ = [\n")
    for name in (
        "OrderStatus",
        "STATUS_BY_CELL_CLASS",
        "ROW_MARKER_BY_STATUS",
        "PRESENCE_BY_CLASS",
    ):
        out.append('    "' + name + '",\n')
    out.append("]\n")

    out.append("\n\nclass OrderStatus(StrEnum):\n")
    out.append('    """Состояние заказа, каким его показывает список продаж.\n\n')
    out.append("    Значение совпадает с именем состояния в спецификации: оно уходит в\n")
    out.append("    событие и в журнал, где обязано совпадать между всеми реализациями.\n")
    out.append('    """\n\n')
    for entry in entries:
        out.append("    " + _const(entry["status"]) + ' = "' + entry["status"] + '"\n')

    out.append("\n\n#: Статус по цветовому классу ячейки.\n")
    out.append("STATUS_BY_CELL_CLASS: Final[dict[str, OrderStatus]] = {\n")
    for entry in entries:
        out.append(
            '    "'
            + entry["cell_class"]
            + '": OrderStatus.'
            + _const(entry["status"])
            + ",  # "
            + entry["display_ru"]
            + "\n"
        )
    out.append("}\n")

    out.append("\n#: Модификатор строки для состояний, у которых он наблюдался.\n")
    out.append("#:\n")
    out.append("#: Носитель односторонний. Модификатор стоит у оплаченного заказа, а\n")
    out.append("#: закрытый узнаётся по его отсутствию - и отсутствие само по себе не\n")
    out.append("#: свидетельство: под ним с равным успехом лежит переименование класса.\n")
    out.append("#: Поэтому модификатор служит проверкой в одну сторону, а состояние\n")
    out.append("#: берётся из класса ячейки.\n")
    out.append("ROW_MARKER_BY_STATUS: Final[dict[OrderStatus, str]] = {\n")
    for entry in entries:
        if not entry.get("row_class"):
            continue
        out.append(
            "    OrderStatus."
            + _const(entry["status"])
            + ': "'
            + entry["row_class"]
            + '",  # '
            + entry["display_ru"]
            + "\n"
        )
    out.append("}\n")

    out.append("\n#: Присутствие контрагента по классу карточки пользователя.\n")
    out.append("#:\n")
    out.append("#: Словарь закрыт по наблюдению, но не по умолчанию: класса, которого\n")
    out.append("#: здесь нет, достаточно, чтобы признак стал ненаблюдённым. Правило\n")
    out.append("#: «нет offline, значит online» запрещено - переименуй площадка класс, и\n")
    out.append("#: каждый контрагент молча стал бы присутствующим.\n")
    out.append("PRESENCE_BY_CLASS: Final[dict[str, bool]] = {\n")
    for name, value in presence.items():
        out.append('    "' + name + '": ' + str(bool(value)) + ",\n")
    out.append("}\n")

    return "".join(out)


#: Что порождается: имя файла в пакете и функция, которая его строит.
TARGETS: Final[dict[str, Callable[[Path], str]]] = {
    "errors.py": render_errors,
    "capabilities.py": render_capabilities,
    "response_classes.py": render_response_classes,
    "retry.py": render_retry,
    "budget.py": render_budget,
    "events.py": render_events,
    "extraction.py": render_extraction,
    "operations.py": render_operations,
    "contract.py": render_contract,
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
