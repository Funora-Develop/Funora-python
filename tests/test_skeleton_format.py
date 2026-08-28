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
from selectolax.parser import HTMLParser

from funora._skeleton import (
    SKELETON_FORMAT,
    SUPPORTED_SKELETON_FORMATS,
    _char_class,
    skeletonize,
)
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
        f"знак {sample!r} отнесён к классу {_char_class(sample)!r}, а контракт ждёт {expected!r}"
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
        f"контракт объявляет {sorted(CHARACTER_CLASSES)}, а проверка знает {sorted(SAMPLES)}"
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


def test_a_json_attribute_keeps_its_keys_and_masks_its_values() -> None:
    """Проверяет правило формата v8: ключи дословно, значения подписями.

    Заведено ради одного места, и место это перекрывает семь операций записи:
    защитный токен площадки лежит в объекте JSON атрибута data-app-data. Пока
    атрибут маскировался целиком, в снимке не было ни одного ключа этого
    объекта, и разбор, достающий оттуда токен, проверить было НЕ НА ЧЕМ.

    Токен от правила читаемым не становится и не должен. Читаемым становится
    путь до него.

    Returns:
        None
    """
    import json as _json

    html = (
        "<html><body data-app-data='"
        '{"csrf-token":"a1b2c3d4e5f6a7b8","userId":12345678,"locale":"ru",'
        '"nested":{"deep":"тайна"}}'
        "'></body></html>"
    )
    skeleton = skeletonize(html)

    raw = HTMLParser(skeleton).css_first("body").attributes.get("data-app-data")
    assert raw is not None, "атрибут пропал из скелета"
    parsed = _json.loads(raw)

    # Ключи всех уровней на месте - по ним и пишется разбор.
    assert set(parsed) == {"csrf-token", "userId", "locale", "nested"}
    assert set(parsed["nested"]) == {"deep"}

    # Значения - все до одного - замаскированы.
    for secret in ("a1b2c3d4e5f6a7b8", "12345678", "тайна"):
        assert secret not in skeleton, f"«{secret}» уцелел в скелете"
    assert parsed["csrf-token"].startswith("T"), parsed["csrf-token"]
    assert parsed["nested"]["deep"].startswith("T"), parsed["nested"]["deep"]

    # Число маскируется подписью своей записи, а не остаётся числом:
    # восьмизначное число - это идентификатор человека, а не количество.
    assert isinstance(parsed["userId"], str), parsed["userId"]


def test_a_json_attribute_with_a_human_key_is_masked_whole() -> None:
    """Требует отменять правило, если хоть один ключ пришёл из данных.

    Ключом бывает и то, что написал человек - в словаре, собранном из данных.
    Сборщик наблюдений на этом уже обжёгся, записав вместе с ключами настоящие
    суммы операций.

    Returns:
        None
    """
    for value in (
        '{"Иван Петров":"x"}',
        '{"nested":{"1031.40 рублей":"x"}}',
        '{"<div class":"x"}',
    ):
        skeleton = skeletonize(f"<html><body data-app-data='{value}'></body></html>")
        raw = HTMLParser(skeleton).css_first("body").attributes.get("data-app-data")
        assert raw is not None and raw.startswith("T"), f"значение {value} сохранило ключи: {raw!r}"
        for secret in ("Иван", "Петров", "1031.40", "<div"):
            assert secret not in skeleton, f"«{secret}» уцелел в скелете при {value}"


def test_only_an_object_falls_under_the_rule() -> None:
    """Требует применять правило только к объекту, а не ко всему похожему.

    Строка, случайно разбираемая как число либо как перечень, значением-объектом
    не является, и распространять на неё правило значило бы расширить его
    наугад.

    Returns:
        None
    """
    for value in ("[1, 2, 3]", "12345", '"строка"', "не объект"):
        skeleton = skeletonize(f"<html><body data-app-data='{value}'></body></html>")
        raw = HTMLParser(skeleton).css_first("body").attributes.get("data-app-data")
        assert raw is not None and raw.startswith("T"), (
            f"значение {value!r} прошло как объект: {raw!r}"
        )


def test_the_skeleton_stays_parseable_after_the_rule() -> None:
    """Требует, чтобы скелет с объектом в атрибуте оставался разбираемым.

    До формата v8 маскированное значение кавычек не содержало никогда.
    Атрибут-объект их содержит, и неэкранированный он сделал бы снимок
    неразбираемым - то есть бесполезным ровно для того, ради чего заводился.

    Returns:
        None
    """
    html = (
        '<html><body data-app-data=\'{"csrf-token":"abc"}\'>'
        "<div class='after-json'>т</div></body></html>"
    )
    skeleton = skeletonize(html)
    tree = HTMLParser(skeleton)

    # Узел ПОСЛЕ атрибута-объекта обязан найтись: если кавычка порвала атрибут,
    # разбор потеряет всё, что за ним.
    assert tree.css_first(".after-json") is not None, (
        "узел за атрибутом-объектом потерян: кавычка порвала разметку"
    )
    assert "&quot;" in skeleton, "кавычка не экранирована"
