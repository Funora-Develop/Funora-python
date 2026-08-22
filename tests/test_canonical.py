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
from datetime import UTC, datetime
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


def _materialise(value: Any) -> Any:
    """Превращает обёртку вектора в настоящее значение.

    JSON своего типа для времени не имеет, поэтому момент в векторе несёт ключ
    $instant со строкой RFC 3339. Реализация обязана разобрать её и подставить
    под именем поля - иначе вектор проверял бы сериализацию строки, а не
    момента.

    Args:
        value (Any): Значение из вектора.

    Returns:
        Any: Значение, годное для canonical_dumps.
    """
    if isinstance(value, dict) and set(value) == {"$instant"}:
        return {"observed_at": datetime.fromisoformat(value["$instant"])}
    return value


def _serialize_accept() -> list[tuple[str, Any, str]]:
    """Собирает векторы сериализации, которые обязаны пройти.

    Returns:
        list[tuple[str, Any, str]]: Имя, вход и ожидаемый вывод.
    """
    if not _have_spec():
        return []
    return [
        (v["name"], _materialise(v["input"]), v["expected"])
        for v in _vectors()["serialize"]["accept"]
    ]


def _serialize_reject() -> list[tuple[str, Any]]:
    """Собирает векторы сериализации, которые обязаны быть отвергнуты.

    Returns:
        list[tuple[str, Any]]: Имя и вход.
    """
    if not _have_spec():
        return []
    return [
        (v["name"], _materialise(v["input"])) for v in _vectors()["serialize"]["reject"]
    ]


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


def _rules() -> list[dict[str, Any]]:
    """Читает нормативные правила канонической формы.

    Returns:
        list[dict[str, Any]]: Правила в объявленном порядке.
    """
    import yaml

    path = Path(SPEC_DIR or ".") / "spec" / "canonical-form.yaml"
    return list(yaml.safe_load(path.read_text(encoding="utf-8"))["rules"])


def test_rules_are_numbered_without_gaps() -> None:
    """Проверяет, что правила пронумерованы подряд и без повторов.

    Правила называют по номеру - в отказах, в заметках, в чужих реализациях.
    Пропуск в нумерации означает, что правило удалили, не заметив, что на него
    ссылаются.

    Returns:
        None
    """
    numbers = [rule["id"] for rule in _rules()]
    assert numbers == list(range(1, len(numbers) + 1)), f"нумерация правил разошлась: {numbers}"


def test_every_rule_says_how_it_is_checked() -> None:
    """Проверяет, что каждое правило называет, чем оно проверяется.

    Это та же болезнь, что и в реализации: объявление, которым никто не
    пользуется, выглядит работающим. Правило канонической формы, не названное
    ни одной проверкой, ничем и не держится - что доказано мутацией: правила
    можно было переписать на противоположные, и обе сборки оставались
    зелёными.

    Returns:
        None
    """
    silent = [rule["id"] for rule in _rules() if not str(rule.get("checked_by", "")).strip()]
    assert not silent, f"правила не называют, чем проверяются: {silent}"


def test_named_vectors_exist() -> None:
    """Проверяет, что названный правилом вектор вправду есть в файле векторов.

    Ссылка на несуществующий вектор хуже отсутствия ссылки: она выглядит
    проверкой и ею не является.

    Returns:
        None
    """
    vectors = _vectors()
    known = {
        v["name"]
        for section in ("serialize", "fingerprint")
        for bucket in ("accept", "reject")
        for v in vectors[section][bucket]
    }

    missing: list[str] = []
    for rule in _rules():
        for name in rule.get("vectors") or ():
            if name not in known:
                missing.append(f"правило {rule['id']} -> «{name}»")

    assert not missing, (
        f"правила ссылаются на векторы, которых нет в файле: {missing}. "
        "Ссылка на несуществующий вектор хуже отсутствия ссылки: она выглядит "
        "проверкой и ею не является"
    )


def test_vectors_belong_to_the_current_form() -> None:
    """Проверяет, что векторы объявлены той же версией формы, что и пакет.

    Векторы описывают байты, а байты задаются правилами. Вектор, оставшийся от
    прежней версии формы, проверяет прежние правила и при этом выглядит
    работающей проверкой - то есть ровно тем, чего в этом проекте не бывает.

    Расхождение уже случалось: правила подняли до второй версии, а файл
    векторов остался объявленным первой, и не поймало этого ничто.

    Returns:
        None
    """
    from funora.contract import CANONICAL_FORM_VERSION

    declared = _vectors()["canonical_form_version"]
    assert declared == CANONICAL_FORM_VERSION, (
        f"векторы объявлены формой {declared}, а пакет собран формой "
        f"{CANONICAL_FORM_VERSION}. Либо векторы отстали от правил, либо "
        "правила подняли, не тронув векторы"
    )


def test_the_same_moment_gives_the_same_bytes_in_any_timezone() -> None:
    """Проверяет, что часовой пояс машины не меняет вывод.

    Вектор объявлялся обязательным и не существовал: спецификация требовала
    прогнать одну фикстуру под TZ=UTC и под другим поясом с побайтовым
    совпадением, а форматировать момент реализация не умела вовсе.

    Пояс машины виден только через astimezone и strftime, и ошибиться тут легко
    ровно одним способом - вывести местное время вместо UTC. Тогда два воркера
    одного продавца в разных странах дали бы одному наблюдению разные байты.

    Returns:
        None
    """
    import os
    import time

    moment = datetime(2026, 8, 18, 4, 12, 33, 123456, tzinfo=UTC)
    outputs: list[str] = []

    previous = os.environ.get("TZ")
    try:
        for zone in ("UTC", "Asia/Yekaterinburg", "America/Sao_Paulo"):
            os.environ["TZ"] = zone
            if hasattr(time, "tzset"):
                time.tzset()
            outputs.append(canonical_dumps({"observed_at": moment}))
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if hasattr(time, "tzset"):
            time.tzset()

    assert len(set(outputs)) == 1, (
        f"часовой пояс машины изменил вывод: {sorted(set(outputs))}"
    )
    assert outputs[0] == '{"observed_at":"2026-08-18T04:12:33.123Z"}'


def test_a_naive_moment_is_refused() -> None:
    """Проверяет, что момент без пояса отвергается, а не додумывается.

    Счесть его UTC либо местным - домысел ценой до суток разницы: наблюдение
    окажется в будущем либо в прошлом, и отпечаток события разойдётся у двух
    реализаций, работающих в разных поясах.

    Returns:
        None
    """
    with pytest.raises(ValidationError, match="часового пояса"):
        canonical_dumps({"observed_at": datetime(2026, 8, 18, 4, 12, 33)})


def test_serialization_is_idempotent() -> None:
    """Проверяет, что повторная сериализация ничего не меняет.

    Свойство объявлено в файле векторов. Форма, меняющая значение при повторном
    применении, не форма: снимок, прошедший через две реализации, отличался бы
    от снимка, прошедшего через одну.

    Returns:
        None
    """
    for vector in _vectors()["serialize"]["accept"]:
        once = canonical_dumps(_materialise(vector["input"]))
        twice = canonical_dumps(json.loads(once))
        assert once == twice, (
            f"вектор «{vector['name']}»: повторная сериализация дала другое.\n"
            f"  первый раз: {once}\n"
            f"  второй раз: {twice}"
        )


def test_declared_properties_are_all_checked() -> None:
    """Проверяет, что каждое объявленное свойство кем-то проверяется.

    Свойство, объявленное в файле векторов и не проверяемое ничем, - то же
    молчащее объявление, что и всё остальное здесь.

    Returns:
        None
    """
    declared = set(_vectors().get("properties", {})) - {"_"}
    checked = {"timezone_independence", "normalization_idempotence"}

    assert declared == checked, (
        f"объявлены свойства {sorted(declared)}, проверяются {sorted(checked)}. "
        "Разошлись: " + str(sorted(declared ^ checked))
    )
