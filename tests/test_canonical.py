"""Прогоняет векторы канонической формы и отпечатка.

Векторы лежат в спецификации, а не здесь: их обязана прогонять каждая
реализация, а не только эта. До появления файла проверить согласие двух
реализаций было нечем - правила канонической формы можно было переписать на
противоположные, и обе сборки остались бы зелёными.

Отсюда же устройство проверок. Они не описывают ожидаемое своими словами, а
берут его из файла: проверка, повторяющая правило вторым голосом, расходится с
первым молча.
"""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from funora._canonical import canonical_dumps
from funora._diff import _fingerprint
from funora.errors import ValidationError
from funora.events import EventType

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")

#: Причина пропуска, если спецификации рядом нет.
NO_SPEC = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"


def _vectors() -> dict[str, Any]:
    """Читает файл векторов.

    Returns:
        dict[str, Any]: Разобранное содержимое файла векторов.
    """
    path = Path(SPEC_DIR or ".") / "spec" / "conformance" / "canonical-form.vectors.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _have_spec() -> bool:
    """Сообщает, доступна ли рабочая копия спецификации.

    Returns:
        bool: True, если файл векторов на месте.
    """
    if not SPEC_DIR:
        return False
    return (Path(SPEC_DIR) / "spec" / "conformance" / "canonical-form.vectors.json").is_file()


pytestmark = pytest.mark.skipif(not _have_spec(), reason=NO_SPEC)


def _serialize_accept() -> list[tuple[str, Any, str]]:
    """Собирает векторы сериализации, которые обязаны пройти.

    Returns:
        list[tuple[str, Any, str]]: Имя, вход и ожидаемый вывод.
    """
    if not _have_spec():
        return []
    return [
        (v["name"], v["input"], v["expected"]) for v in _vectors()["serialize"]["accept"]
    ]


def _serialize_reject() -> list[tuple[str, Any]]:
    """Собирает векторы сериализации, которые обязаны быть отвергнуты.

    Returns:
        list[tuple[str, Any]]: Имя и вход.
    """
    if not _have_spec():
        return []
    return [(v["name"], v["input"]) for v in _vectors()["serialize"]["reject"]]


def _fingerprint_accept() -> list[tuple[str, dict[str, str], str | None, str | None]]:
    """Собирает векторы отпечатка.

    Returns:
        list[tuple[str, dict[str, str], str | None, str | None]]: Имя, поля,
        ожидаемое значение и имя вектора, с которым надо совпасть.
    """
    if not _have_spec():
        return []
    return [
        (v["name"], v["input"], v.get("expected"), v.get("same_as"))
        for v in _vectors()["fingerprint"]["accept"]
    ]


@pytest.mark.parametrize(("name", "value", "expected"), _serialize_accept())
def test_serialization_matches_the_vector(name: str, value: Any, expected: str) -> None:
    """Проверяет, что каноническая запись совпадает с вектором дословно.

    Args:
        name (str): Имя вектора.
        value (Any): Вход.
        expected (str): Ожидаемая запись.

    Returns:
        None
    """
    assert canonical_dumps(value) == expected, f"вектор «{name}»"


@pytest.mark.parametrize(("name", "value"), _serialize_reject())
def test_inexpressible_values_are_refused(name: str, value: Any) -> None:
    """Проверяет, что невыразимое отвергается вслух, а не подменяется.

    Подмена хуже отказа: она даёт две реализации, которые расходятся в байтах,
    и обе при этом считают, что всё в порядке.

    Args:
        name (str): Имя вектора.
        value (Any): Вход.

    Returns:
        None
    """
    with pytest.raises(ValidationError):
        canonical_dumps(value)


def _digest(fields: dict[str, str]) -> str:
    """Считает отпечаток по полям вектора.

    Args:
        fields (dict[str, str]): Четыре поля отпечатка.

    Returns:
        str: Отпечаток.
    """
    return _fingerprint(
        account_id=fields["account_id"],
        event_type=EventType(fields["type"]),
        entity_id=fields["entity_id"],
        revision=fields["entity_revision"],
    )


@pytest.mark.parametrize(("name", "fields", "expected", "same_as"), _fingerprint_accept())
def test_fingerprint_matches_the_vector(
    name: str, fields: dict[str, str], expected: str | None, same_as: str | None
) -> None:
    """Проверяет отпечаток против вектора.

    Вектор либо называет ожидаемое значение дословно, либо требует совпадения
    с другим вектором - так записана проверка нормализации: важно не то, чему
    равен отпечаток, а то, что составная и разложенная записи дают один и тот
    же.

    Args:
        name (str): Имя вектора.
        fields (dict[str, str]): Четыре поля отпечатка.
        expected (str | None): Ожидаемое значение, если задано.
        same_as (str | None): Имя вектора, с которым надо совпасть.

    Returns:
        None
    """
    got = _digest(fields)

    if expected is not None:
        assert got == expected, f"вектор «{name}»"

    if same_as is not None:
        other = next(
            v for v in _vectors()["fingerprint"]["accept"] if v["name"] == same_as
        )
        assert got == _digest(other["input"]), (
            f"вектор «{name}» обязан совпасть с «{same_as}», а не совпал. "
            "Значит нормализация Unicode не применяется, и две реализации, "
            "читающие одну страницу разными стеками, дадут одному событию два "
            "разных идентификатора"
        )


def test_fingerprint_refuses_the_separator_inside_a_part() -> None:
    """Проверяет, что разделитель полей не проходит внутрь части.

    Иначе две разные четвёрки склеиваются в одну строку, и два разных события
    получают один отпечаток - молча.

    Returns:
        None
    """
    for vector in _vectors()["fingerprint"]["reject"]:
        with pytest.raises(ValidationError):
            _digest(vector["input"])


def test_normalization_is_not_a_no_op() -> None:
    """Проверяет, что вектор нормализации вправду несёт разложенную запись.

    Без этого предыдущая проверка проходила бы сама собой: вектор с уже
    нормализованной строкой совпадает с образцом при любой реализации, включая
    ту, которая не нормализует вовсе.

    Returns:
        None
    """
    vector = next(
        v for v in _vectors()["fingerprint"]["accept"] if v.get("same_as") is not None
    )
    raw = vector["input"]["account_id"]
    assert raw != unicodedata.normalize("NFC", raw), (
        "вектор нормализации записан уже нормализованным - он не проверяет ничего"
    )
