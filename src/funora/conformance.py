"""Участие в наборе соответствия.

Протокол объявлен в spec/conformance/runner-protocol.yaml. Реализация читает
случаи по одному в строке с ввода и отвечает так же - по одному в строке.
Строки на входе и выходе умеет всякий язык, и никакой общей сборки для этого не
нужно: проверять предстоит шесть реализаций, и привязывать раннер к одной было
бы странно.

Главное свойство протокола - НЕЛЬЗЯ ПРОМОЛЧАТЬ. Случай, которого реализация не
умеет, отвечается пропуском с указанием записи реестра неисполненного. Пропуск
без ссылки протокол считает отказом: набор, который можно тихо пропустить,
показывает согласие там, где его нет, а это хуже отсутствия набора. Отсутствие
видно, ложное согласие нет.

Запуск::

    python -m funora.conformance < cases.jsonl > results.jsonl
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ._canonical import canonical_dumps
from ._diff import _fingerprint
from .contract import RUNNER_PROTOCOL
from .errors import ConfigurationError, FunoraError, ValidationError
from .events import EventType

__all__ = ["PROTOCOL", "answer", "main"]

#: Версия протокола, по которой отвечает эта реализация.
PROTOCOL: Final[int] = RUNNER_PROTOCOL


def _vectors() -> dict[str, Any]:
    """Читает файл набора СВОИМ разборщиком.

    Вход приходит ссылкой, а не значением, и это не удобство. Первая редакция
    протокола передавала значение, и раннер портил его по дороге: он написан на
    JavaScript, а JSON.parse там теряет точность за 2^53 и не отличает 1.0 от
    1 - то есть уничтожал ровно те различия, ради которых векторы существуют.

    Значит вектор обязан доезжать нетронутым, а разобрать его должен тот, кто
    будет с ним работать.

    Returns:
        dict[str, Any]: Разобранный файл набора.

    Raises:
        ConfigurationError: Если рабочая копия спецификации не найдена.
    """
    root = os.environ.get("FUNORA_SPEC_DIR")
    if not root:
        raise ConfigurationError(
            "переменная FUNORA_SPEC_DIR не задана: файл набора искать негде. "
            "Протокол передаёт вход ссылкой, и прочесть его обязана реализация"
        )
    path = Path(root) / "spec" / "conformance" / "canonical-form.vectors.json"
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _resolve(reference: str) -> Any:
    """Достаёт вход вектора по ссылке вида «serialize.accept[7]».

    Args:
        reference (str): Ссылка на вектор.

    Returns:
        Any: Вход вектора, разобранный своим разборщиком.

    Raises:
        ValidationError: Если ссылка не разбирается либо ведёт в пустоту.
    """
    match = re.fullmatch(r"(\w+)\.(\w+)\[(\d+)\]", reference)
    if match is None:
        raise ValidationError(f"ссылка на вектор {reference!r} не разбирается")

    section, bucket, index = match.group(1), match.group(2), int(match.group(3))
    try:
        return _vectors()[section][bucket][index]["input"]
    except (KeyError, IndexError) as error:
        raise ValidationError(
            f"ссылка {reference!r} ведёт в пустоту: {type(error).__name__}"
        ) from error


def _materialise(value: Any) -> Any:
    """Превращает обёртку случая в настоящее значение.

    JSON своего типа для времени не имеет, поэтому момент несёт ключ $instant
    со строкой RFC 3339. Разобрать её обязана реализация - иначе случай
    проверял бы сериализацию строки, а не момента.

    Args:
        value (Any): Вход случая.

    Returns:
        Any: Значение, годное для канонической формы.
    """
    if isinstance(value, dict) and set(value) == {"$instant"}:
        return {"observed_at": datetime.fromisoformat(value["$instant"])}
    return value


def _digest(fields: dict[str, str]) -> str:
    """Считает отпечаток события по четырём полям случая.

    Args:
        fields (dict[str, str]): Поля отпечатка.

    Returns:
        str: Отпечаток.
    """
    return _fingerprint(
        account_id=fields["account_id"],
        event_type=EventType(fields["type"]),
        entity_id=fields["entity_id"],
        revision=fields["entity_revision"],
    )


def answer(case: dict[str, Any]) -> dict[str, Any]:
    """Отвечает на один случай набора.

    Args:
        case (dict[str, Any]): Случай по протоколу: id, suite, kind, input и
            необязательные expected, same_as, why.

    Returns:
        dict[str, Any]: Ответ по протоколу: id, outcome и подробности.
    """
    case_id = case.get("id", "<без идентификатора>")
    kind = case.get("kind")

    try:
        if kind == "serialize":
            got = canonical_dumps(_materialise(_resolve(case["vector"])))
            return _compare(case_id, got, case.get("expected"))

        if kind == "fingerprint":
            got = _digest(_resolve(case["vector"]))
            return _compare(case_id, got, case.get("expected"))

        if kind in ("serialize_refuses", "fingerprint_refuses"):
            worker = canonical_dumps if kind == "serialize_refuses" else _digest
            try:
                produced = worker(_materialise(_resolve(case["vector"])))
            except FunoraError:
                return {"id": case_id, "outcome": "pass"}
            return {
                "id": case_id,
                "outcome": "fail",
                "detail": (
                    f"вход обязан быть отвергнут, а принят и дал {produced!r}. "
                    "Принять невыразимое - значит разойтись с чужой реализацией "
                    "молча"
                ),
            }

    except FunoraError as error:
        return {
            "id": case_id,
            "outcome": "fail",
            "detail": f"{type(error).__name__}: {error}",
        }
    except Exception as error:  # noqa: BLE001
        # Своя поломка - тоже отказ, а не пропуск: пропуск означает объявленное
        # неумение, а тут реализация просто сломалась.
        return {
            "id": case_id,
            "outcome": "fail",
            "detail": f"реализация упала: {type(error).__name__}: {error}",
        }

    # Неизвестный вид - отказ, а не пропуск. Он означает, что реализация
    # отстала от набора, и молчать об этом нельзя.
    return {
        "id": case_id,
        "outcome": "fail",
        "detail": f"вид случая {kind!r} реализации неизвестен - она отстала от набора",
    }


def _compare(case_id: str, got: str, expected: str | None) -> dict[str, Any]:
    """Сверяет полученное с ожидаемым.

    Args:
        case_id (str): Идентификатор случая.
        got (str): Что получилось.
        expected (str | None): Что ожидалось. None означает, что случай
            сверяется с другим случаем по полю same_as, и сверку делает раннер.

    Returns:
        dict[str, Any]: Ответ по протоколу.
    """
    if expected is None:
        return {"id": case_id, "outcome": "pass", "value": got}
    if got == expected:
        return {"id": case_id, "outcome": "pass", "value": got}
    return {
        "id": case_id,
        "outcome": "fail",
        "value": got,
        "detail": f"получено {got!r}, ожидалось {expected!r}",
    }


def main() -> int:
    """Читает случаи с ввода и пишет ответы на вывод.

    Returns:
        int: Ноль всегда. Решение о коде возврата принимает раннер: он один
        видит весь набор и отличает отказ от пропуска.
    """
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        sys.stdout.write(json.dumps(answer(json.loads(stripped)), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
