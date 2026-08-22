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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ._canonical import canonical_dumps
from ._diff import Event, _fingerprint
from ._poll import Deduplicator
from .contract import RUNNER_PROTOCOL
from .errors import ConfigurationError, FunoraError, ValidationError
from .events import EventType

__all__ = ["PROTOCOL", "answer", "main"]

#: Версия протокола, по которой отвечает эта реализация.
PROTOCOL: Final[int] = RUNNER_PROTOCOL


def _spec_file(name: str) -> Path:
    """Указывает путь к файлу набора в рабочей копии спецификации.

    Args:
        name (str): Имя файла в каталоге spec/conformance.

    Returns:
        Path: Путь к файлу.

    Raises:
        ConfigurationError: Если рабочая копия спецификации не найдена.
    """
    root = os.environ.get("FUNORA_SPEC_DIR")
    if not root:
        raise ConfigurationError(
            "переменная FUNORA_SPEC_DIR не задана: файл набора искать негде. "
            "Протокол передаёт вход ссылкой, и прочесть его обязана реализация"
        )
    return Path(root) / "spec" / "conformance" / name


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
    path = _spec_file("canonical-form.vectors.json")
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


def _scenario(reference: str) -> dict[str, Any]:
    """Достаёт сценарий набора resume по ссылке вида «scenarios[3]».

    Args:
        reference (str): Ссылка на сценарий.

    Returns:
        dict[str, Any]: Сценарий целиком.

    Raises:
        ValidationError: Если ссылка не разбирается либо ведёт в пустоту.
    """
    match = re.fullmatch(r"scenarios\[(\d+)\]", reference)
    if match is None:
        raise ValidationError(f"ссылка на сценарий {reference!r} не разбирается")

    path = _spec_file("resume.vectors.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    try:
        scenario: dict[str, Any] = document["scenarios"][int(match.group(1))]
    except (KeyError, IndexError) as error:
        raise ValidationError(
            f"ссылка {reference!r} ведёт в пустоту: {type(error).__name__}"
        ) from error
    return scenario


def _event(event_id: str, key: str) -> Event:
    """Собирает событие с заданным идентификатором и ключом упорядочивания.

    Гашение смотрит только на эти два поля, остальное конверту нужно для формы.
    Подставлять сюда настоящий отпечаток нельзя: сценарий задаёт тождество
    событий сам, а посчитанный отпечаток сделал бы «a» из разных сценариев
    одним и тем же событием.

    Args:
        event_id (str): Идентификатор события из сценария.
        key (str): Ключ упорядочивания.

    Returns:
        Event: Событие в конверте.
    """
    return Event(
        id=event_id,
        type=EventType.MESSAGE_CREATED,
        account_id="a1",
        ordering_key=key,
        entity_id=event_id,
        observed_at=datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
        origin="structural",
        payload={},
    )


def _run_scenario(scenario: dict[str, Any]) -> list[list[str]]:
    """Прогоняет сценарий гашения и возвращает дошедшее по шагам.

    Время шага задано стенными часами, а перезапуск объявляет аптайм нового
    процесса. Внутренние часы получаются из этих двух величин: показание
    секундомера равно аптайму на момент перезапуска плюс всё, что с тех пор
    прошло по стенным часам.

    Ради того сценарий и написан. Реализация, сохранившая метки показанием
    своего секундомера, здесь разойдётся с ожидаемым: у нового процесса начало
    отсчёта своё, и прочитает он не то, что записал.

    Args:
        scenario (dict[str, Any]): Сценарий: ttl_ms и перечень шагов.

    Returns:
        list[list[str]]: Для каждого шага - идентификаторы событий, прошедших
        сквозь гашение, в порядке предложения. Шаг перезапуска даёт пустой
        перечень.

    Raises:
        ValidationError: Если сценарий начинается с перезапуска: неизвестно, на
            какой момент стенных часов сохранять состояние.
    """
    dedup = Deduplicator(ttl_ms=scenario["ttl_ms"])
    # Аптайм первого процесса ненулевой нарочно. При нуле показание секундомера
    # совпало бы с прошедшим по стенным часам, и реализация, перепутавшая одно с
    # другим, случайно дала бы верный ответ - мутация это показала.
    uptime_base = float(scenario.get("uptime_s", 604800))
    wall_base: int | None = None
    wall_now: int | None = None
    delivered: list[list[str]] = []

    def monotonic(at_ms: int) -> float:
        """Переводит момент стенных часов в показание секундомера процесса.

        Args:
            at_ms (int): Момент по стенным часам, миллисекунды от эпохи.

        Returns:
            float: Показание секундомера, секунды.
        """
        assert wall_base is not None
        return uptime_base + (at_ms - wall_base) / 1000

    for step in scenario["steps"]:
        restart = step.get("restart")
        if restart is not None:
            if wall_now is None:
                raise ValidationError(
                    "сценарий начинается с перезапуска: неизвестно, на какой "
                    "момент стенных часов сохранять состояние"
                )
            state = dedup.snapshot(monotonic(wall_now), wall_ms=wall_now)
            dedup = Deduplicator(ttl_ms=scenario["ttl_ms"])
            uptime_base = float(restart["uptime_s"])
            wall_base = wall_now
            dedup.restore(state, uptime_base, wall_ms=wall_now)
            delivered.append([])
            continue

        wall_now = step["at_ms"]
        if wall_base is None:
            wall_base = wall_now
        now = monotonic(wall_now)

        key = step.get("key", "k")
        offered = tuple(_event(one, key) for one in step["offer"])
        fresh = dedup.filter(offered, now)
        delivered.append([one.id for one in fresh])

        accepted = set(step.get("commit", ()))
        dedup.commit(tuple(one for one in fresh if one.id in accepted), now)

    return delivered


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

        if kind == "resume":
            # Сверяет раннер: реализация возвращает, что дошло на каждом шаге, а
            # ожидаемого не знает - иначе она сверяла бы себя сама.
            return {
                "id": case_id,
                "outcome": "pass",
                "steps": _run_scenario(_scenario(case["vector"])),
            }

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

    Кодировка потоков задаётся явно. По умолчанию Python берёт её у системы, и
    на Windows это оказалась cp1251: раннер получал вопросительные знаки вместо
    кириллицы и показывал отказ там, где реализация права. Транспорт, портящий
    проверяемое, хуже отсутствия транспорта - протокол объявляет UTF-8, и
    полагаться тут на настройки машины нельзя.

    Returns:
        int: Ноль всегда. Решение о коде возврата принимает раннер: он один
        видит весь набор и отличает отказ от пропуска.
    """
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        sys.stdout.write(json.dumps(answer(json.loads(stripped)), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
