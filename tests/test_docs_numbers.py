"""Проверяет числовые утверждения документации.

Разбор нашёл в четырёх документах девять устаревших чисел: «из 675 проверок» при
семистах с лишним, «порождает пять» видов событий при семи, «один из
одиннадцати» непорождаемых при девяти, «сегодня там четыре механизма» при
восемнадцати записях. Ни одно не ловилось ничем: число прозой протухает молча.

Отсюда правило - число в документе либо проверяется, либо его там нет. Проверки
ниже читают утверждение из документа и сверяют с прогоном; те числа, которые
растут каждый заход и смысла не несут, из прозы убраны вовсе.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from funora._watch import PRODUCIBLE
from funora.events import EventType

#: Корень репозитория.
ROOT = Path(__file__).resolve().parent.parent

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Числительные, которыми документы называют количества.
WORDS: dict[str, int] = {
    "один": 1,
    "одного": 1,
    "двух": 2,
    "трёх": 3,
    "четыре": 4,
    "четырёх": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
    "семь": 7,
    "семи": 7,
    "восемь": 8,
    "восьми": 8,
    "девять": 9,
    "девяти": 9,
    "десять": 10,
    "десяти": 10,
    "одиннадцати": 11,
    "двенадцати": 12,
    "шестнадцать": 16,
    "шестнадцати": 16,
}


def _read(name: str) -> str:
    """Читает документ реализации.

    Args:
        name (str): Имя файла в docs.

    Returns:
        str: Содержимое документа.
    """
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def _number_after(text: str, before: str, after: str) -> int:
    """Достаёт числительное между двумя кусками текста.

    Args:
        text (str): Документ.
        before (str): Что стоит слева от числительного.
        after (str): Что стоит справа.

    Returns:
        int: Значение числительного.

    Raises:
        AssertionError: Если утверждение не найдено либо слово не числительное.
    """
    match = re.search(re.escape(before) + r"\s*([А-Яа-яЁё]+)\s*" + re.escape(after), text)
    assert match is not None, f"утверждение «{before} ... {after}» пропало из документа"
    word = match.group(1).lower()
    assert word in WORDS, f"«{word}» не числительное - утверждение переписали, поправьте проверку"
    return WORDS[word]


def test_produced_event_count_matches_reality() -> None:
    """Проверяет заявленное число порождаемых видов событий.

    Документ говорил «порождает пять», а порождается семь. Разница не
    косметическая: раздел объясняет, почему подписка на непорождаемый вид
    отвергается, и число там - половина довода.

    Returns:
        None
    """
    claimed = _number_after(_read("architecture.md"), "реализация порождает", ".")
    assert claimed == len(PRODUCIBLE), (
        f"architecture.md обещает {claimed} порождаемых видов, порождается "
        f"{len(PRODUCIBLE)}"
    )


def test_declared_event_count_matches_reality() -> None:
    """Проверяет заявленное число объявленных видов событий.

    Returns:
        None
    """
    claimed = _number_after(_read("architecture.md"), "перечисление объявляет", "видов")
    assert claimed == len(EventType), (
        f"architecture.md обещает {claimed} объявленных видов, объявлено {len(EventType)}"
    )


def test_non_producible_count_matches_reality() -> None:
    """Проверяет заявленное число непорождаемых видов событий.

    Документ говорил «один из одиннадцати», а десятью строками выше - «остальные
    девять». Два числа в одном разделе, и оба про одно и то же.

    Returns:
        None
    """
    claimed = _number_after(_read("limits.md"), "подписывался на один из", ",")
    assert claimed == len(EventType) - len(PRODUCIBLE), (
        f"limits.md обещает {claimed} непорождаемых видов, их "
        f"{len(EventType) - len(PRODUCIBLE)}"
    )


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec").is_dir(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_docs_do_not_count_the_registry_in_prose() -> None:
    """Проверяет, что документ не пересказывает реестр неисполненного числом.

    Реестр растёт каждый заход, и число в прозе устаревает быстрее всего
    остального: «сегодня там четыре механизма» держалось при восемнадцати
    записях. Такому числу в документе места нет - есть ссылка на файл.

    Returns:
        None
    """
    text = _read("limits.md")
    forbidden = re.search(r"[Сс]егодня там\s+([А-Яа-яЁё]+|\d+)\s+механизм", text)
    assert forbidden is None, (
        "limits.md снова пересказывает содержимое реестра числом: "
        f"«{forbidden.group(0) if forbidden else ''}». Реестр растёт, число "
        "устаревает молча - сошлитесь на файл"
    )


def test_docs_name_only_existing_fixtures() -> None:
    """Проверяет, что документы называют существующие снимки.

    observations.md ссылался на снимок orders-trade.states.logged.ru, которого в
    репозитории нет. Ссылка на несуществующий снимок выглядит проверяемым
    фактом и им не является.

    Returns:
        None
    """
    pages = ROOT / "tests" / "fixtures" / "pages"
    available = {path.name.split(".skeleton")[0] for path in pages.glob("*.skeleton.txt")}
    pattern = re.compile(r"`([a-z][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:logged|guest)\.[a-z]{2})`")

    missing: list[str] = []
    for path in sorted((ROOT / "docs").glob("*.md")):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            if name not in available:
                missing.append(f"{path.name}: {name}")

    assert not missing, (
        f"документы называют снимки, которых нет: {missing}. Доступны: "
        f"{sorted(available)}"
    )


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec").is_dir(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_documented_unreachable_errors_match_the_registry() -> None:
    """Проверяет, что таблица недостижимых ошибок сходится с реестром.

    Раздел в limits.md перечисляет классы ошибок, объявленных и не
    возбуждаемых. Перечень живёт в spec/conformance/not-implemented.yaml, и
    таблица - его пересказ. Пересказ расходится с оригиналом молча: класс,
    начавший возбуждаться, исчезнет из реестра и останется в документе, где
    будет обещать вызывающему ветку, которая теперь исполняется.

    Returns:
        None
    """
    import yaml

    registry = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "conformance" / "not-implemented.yaml").read_text(
            encoding="utf-8"
        )
    )
    declared = {
        symbol
        for body in registry["items"].values()
        for symbol in (body.get("symbols") or [])
        if symbol.endswith("Error")
    }

    text = _read("limits.md")
    start = text.index("## Ошибки, которые объявлены и не возбуждаются")
    section = text[start : text.index("\n## ", start + 1)]
    # Только строки таблицы. Пояснение рядом вправе назвать класс, которого в
    # таблице быть не должно: CursorIncompatibleError достижим, а недостижима
    # догрузка истории по курсору.
    rows = "\n".join(line for line in section.splitlines() if line.startswith("|"))
    mentioned = set(re.findall(r"`([A-Z][A-Za-z]*Error)`", rows))

    assert mentioned == declared, (
        f"таблица в limits.md называет {sorted(mentioned)}, а реестр - "
        f"{sorted(declared)}. Разошлись: {sorted(mentioned ^ declared)}"
    )
