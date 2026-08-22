"""Проверяет, что формат снимков сходится с объявленным.

Формат структурного скелета - общая проверочная база: по снимкам сверяется, что
объявленный селектор вправду присутствует на наблюдённой странице. Вторая
реализация обязана строить скелет ТОГО ЖЕ вида, иначе она не сможет ни принять
чужие снимки, ни отдать свои.

Прежде версия формата была литералом в _skeleton.py, а описание жило только в
README фикстур. Теперь формат объявлен в spec/conformance/skeleton-format.yaml и
порождается; проверки ниже следят, чтобы порождённое и код не разошлись.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from funora._skeleton import SKELETON_FORMAT, SUPPORTED_SKELETON_FORMATS, _char_class
from funora.skeleton_format import (
    ACCEPTED_SKELETON_FORMATS,
    CHARACTER_CLASSES,
)
from funora.skeleton_format import SKELETON_FORMAT as DECLARED

#: Каталог со снимками страниц.
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"

#: По одному знаку на каждый объявленный класс.
SAMPLES: dict[str, str] = {
    "d": "5",
    "a": "x",
    "c": "ж",
    "s": " ",
    "p": "!",
    "o": "漢",
}


def test_implementation_uses_the_declared_format() -> None:
    """Проверяет, что реализация не держит собственной версии формата.

    Returns:
        None
    """
    assert SKELETON_FORMAT == DECLARED
    assert SUPPORTED_SKELETON_FORMATS == ACCEPTED_SKELETON_FORMATS


def test_current_format_is_readable_by_itself() -> None:
    """Проверяет, что снятое этой версией ею же и читается.

    Иначе снимок, только что построенный, был бы отвергнут при первом же
    чтении - и заметил бы это не набор проверок, а человек, снявший страницу.

    Returns:
        None
    """
    assert SKELETON_FORMAT in SUPPORTED_SKELETON_FORMATS


@pytest.mark.parametrize(("expected", "sample"), sorted(SAMPLES.items()))
def test_character_classes_match_the_contract(expected: str, sample: str) -> None:
    """Проверяет, что классы знаков совпадают с объявленными.

    Подпись текста складывается из классов. Две реализации с разными наборами
    дадут разные подписи одному тексту, и снимок одной перестанет годиться
    другой - при том, что обе будут считать себя правыми.

    Args:
        expected (str): Объявленный класс.
        sample (str): Знак этого класса.

    Returns:
        None
    """
    assert expected in CHARACTER_CLASSES, f"класс «{expected}» пропал из контракта"
    assert _char_class(sample) == expected, (
        f"знак {sample!r} отнесён к классу {_char_class(sample)!r}, а контракт "
        f"ждёт {expected!r}"
    )


def test_every_declared_class_is_reachable() -> None:
    """Проверяет, что объявлен ровно тот набор классов, который выводится.

    Класс, объявленный и не выводимый ни одним знаком, - обещание, которого
    реализация не исполняет. Лишний в коде и не объявленный - расхождение в
    другую сторону: подпись получит букву, которой второй SDK не знает.

    Returns:
        None
    """
    assert set(CHARACTER_CLASSES) == set(SAMPLES), (
        f"контракт объявляет {sorted(CHARACTER_CLASSES)}, а проверка знает "
        f"{sorted(SAMPLES)}"
    )


def test_stored_snapshots_declare_a_readable_format() -> None:
    """Проверяет, что все хранимые снимки объявляют читаемую версию.

    Returns:
        None
    """
    import json

    for path in sorted(PAGES.glob("*.provenance.json")):
        declared = json.loads(path.read_text(encoding="utf-8")).get("format")
        assert declared in SUPPORTED_SKELETON_FORMATS, (
            f"{path.name} объявляет формат {declared!r}, который читать нечем"
        )
