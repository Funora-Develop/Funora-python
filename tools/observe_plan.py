"""Печатает план наблюдений: что снимать, зачем и какой командой.

План живёт в спецификации - spec/conformance/observations-needed.yaml, - и там
же сверяется с реестром неисполненного. Этот скрипт его только читает и
превращает в готовые к запуску строки.

Зачем отдельный скрипт, а не чтение файла глазами. Порядок в плане значим:
снимать всё разом значит идти на площадку залпом, а залп сразу после входа -
худший возможный первый след. Скрипт печатает по одному шагу и говорит, что уже
снято, чтобы не ходить за тем же дважды.

Запуск:
    python tools/observe_plan.py
    python tools/observe_plan.py --next     только ближайший шаг
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

#: Куда сборщик кладёт снятое.
OBSERVATIONS = Path(__file__).resolve().parent.parent / "observations"

#: Где лежат уже перенесённые снимки.
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pages"


def _spec_root() -> Path:
    """Находит рабочую копию спецификации.

    Returns:
        Path: Корень Funora-spec.

    Raises:
        SystemExit: Если переменная не задана либо указывает не туда.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        raise SystemExit(
            "переменная FUNORA_SPEC_DIR не задана: план наблюдений живёт в "
            "спецификации, и читать его неоткуда"
        )
    root = Path(raw)
    if not (root / "spec" / "conformance" / "observations-needed.yaml").is_file():
        raise SystemExit(f"в {root} нет spec/conformance/observations-needed.yaml")
    return root


def _already_seen() -> set[str]:
    """Собирает имена снимков, которые уже лежат на диске.

    Смотрит и в observations, и в перенесённые фикстуры: снятое, но не
    перенесённое, - тоже снятое, и просить его заново незачем.

    Returns:
        set[str]: Имена снимков без расширения.
    """
    seen: set[str] = set()
    for folder in (OBSERVATIONS, FIXTURES):
        if not folder.is_dir():
            continue
        for one in folder.glob("*.skeleton.txt"):
            seen.add(one.name.removesuffix(".skeleton.txt"))
    return seen


def _matches(expected: str, seen: set[str]) -> bool:
    """Решает, снят ли уже пункт плана.

    Сверка по ИМЕНИ ожидаемого снимка, а не по пути. Путь для этого не годится:
    пустой список продаж и список с продажами лежат по одному адресу, и сверка
    путей объявила бы пустой список уже снятым. Первая редакция так и сделала -
    пометила снятыми четыре пункта из восьми, включая самый нужный.

    Args:
        expected (str): Имя снимка, которого ждёт пункт плана.
        seen (set[str]): Имена снимков на диске.

    Returns:
        bool: True, если такой снимок уже есть.
    """
    return expected in seen


def main(argv: list[str] | None = None) -> int:
    """Печатает план.

    Args:
        argv (list[str] | None): Аргументы командной строки.

    Returns:
        int: Код возврата.
    """
    parser = argparse.ArgumentParser(
        prog="observe_plan",
        description="План наблюдений: что снимать, зачем и какой командой.",
    )
    parser.add_argument(
        "--next", action="store_true", help="показать только ближайший неснятый шаг"
    )
    args = parser.parse_args(argv)

    import yaml

    root = _spec_root()
    doc: dict[str, Any] = yaml.safe_load(
        (root / "spec" / "conformance" / "observations-needed.yaml").read_text(encoding="utf-8")
    )
    seen = _already_seen()

    items = sorted(doc["items"].items(), key=lambda pair: (pair[1].get("priority", 99), pair[0]))
    pending = [(name, one) for name, one in items if not _matches(one["expect_file"], seen)]
    done = [name for name, one in items if _matches(one["expect_file"], seen)]

    print()
    print("ПЛАН НАБЛЮДЕНИЙ FUNORA")
    print(f"  всего в плане: {len(items)} | уже снято: {len(done)} | осталось: {len(pending)}")
    if done:
        print(f"  снято: {', '.join(done)}")
    print()
    print("Ключ кладётся в переменную окружения, а не в аргументы командной строки:")
    print("аргументы видны в списке процессов и попадают в историю оболочки.")
    print()
    print('  $env:FUNORA_GOLDEN_KEY = "..."      (PowerShell)')
    print()

    if not pending:
        print("Всё, что было в плане, снято. Дальше - описывать разметку в контракте.")
        return 0

    shown = pending[:1] if args.next else pending
    for number, (name, one) in enumerate(shown, start=1):
        print("-" * 78)
        print(f"ШАГ {number}. {name}   (важность {one.get('priority')})")
        print()
        print(f"  снимок:    {one['expect_file']}")
        print(f"  путь:      {one['path']}")
        print(f"  состояние: {' '.join(str(one['account_state']).split())}")
        print()
        print("  зачем:")
        for line in str(one["why"]).strip().splitlines():
            print(f"    {line.strip()}")
        print()
        print(f"  закроет записи реестра: {', '.join(one.get('unblocks', []))}")
        print()
        if one.get("do_not_provoke"):
            print("  НЕ ДОБИВАТЬСЯ НАРОЧНО. Снимать, только если случилось само.")
        else:
            print("  команда:")
            print(f"    funora-observe {one['path']}")
        print()

    print("-" * 78)
    print("После съёмки:")
    for line in str(doc["what_to_check_before_moving"]).strip().splitlines():
        print(f"  {line.strip()}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
