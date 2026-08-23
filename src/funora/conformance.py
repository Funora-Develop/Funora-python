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

Второе свойство: ОЖИДАЕМОГО РЕАЛИЗАЦИЯ НЕ ВИДИТ. Она считает и возвращает
посчитанное, а сверяет раннер. Первая редакция клала ожидаемое в случай, и
случай уезжал реализации целиком - то есть проверяемому присылали ответ вместе
с вопросом, и пустая реализация, возвращающая присланное, прошла бы весь набор.

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
from ._gate import check_capability
from ._identity import REGISTRY, identity_of
from ._poll import Deduplicator
from .budget import WAIT_ATTEMPTS, RequestClass
from .capabilities import Capability, CapabilityState
from .contract import RUNNER_PROTOCOL
from .errors import (
    BudgetExhaustedError,
    ConfigurationError,
    ExperimentalCapabilityError,
    FunoraError,
    UnsupportedCapabilityError,
    ValidationError,
)
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


def _document(name: str) -> dict[str, Any]:
    """Читает файл набора СВОИМ разборщиком и сверяет версию протокола.

    Вход приходит ссылкой, а не значением, и это не удобство. Первая редакция
    протокола передавала значение, и раннер портил его по дороге: он написан на
    JavaScript, а JSON.parse там теряет точность за 2^53 и не отличает 1.0 от
    1 - то есть уничтожал ровно те различия, ради которых векторы существуют.

    Значит вектор обязан доезжать нетронутым, а разобрать его должен тот, кто
    будет с ним работать.

    Штамп runner_protocol сверяется здесь, а не только раннером, и это не
    дублирование: раннер и реализация ищут файл набора по РАЗНЫМ корням -
    раннер рядом с собой, реализация по FUNORA_SPEC_DIR. Значит они могут
    читать разные рабочие копии, и версию обязан проверить тот, кто вправду
    открыл файл.

    Args:
        name (str): Имя файла в каталоге spec/conformance.

    Returns:
        dict[str, Any]: Разобранный файл набора.

    Raises:
        ConfigurationError: Если рабочая копия спецификации не найдена.
        ValidationError: Если файл набора написан под другую версию протокола.
    """
    path = _spec_file(name)
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    stamp = parsed.get("runner_protocol")
    if stamp != PROTOCOL:
        raise ValidationError(
            f"файл набора {name} объявляет протокол {stamp!r}, а реализация "
            f"отвечает по версии {PROTOCOL}. Прогнать набор чужой версии молча "
            "нельзя: ось версий заведена ровно затем, чтобы это было видно"
        )
    return parsed


def _vectors() -> dict[str, Any]:
    """Читает файл векторов канонической формы.

    Returns:
        dict[str, Any]: Разобранный файл набора.
    """
    return _document("canonical-form.vectors.json")


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


def _scenario(reference: str, source: str = "resume.vectors.json") -> dict[str, Any]:
    """Достаёт сценарий набора по ссылке вида «scenarios[3]».

    Args:
        reference (str): Ссылка на сценарий.
        source (str): Имя файла векторов в каталоге spec/conformance.

    Returns:
        dict[str, Any]: Сценарий целиком.

    Raises:
        ValidationError: Если ссылка не разбирается либо ведёт в пустоту.
    """
    match = re.fullmatch(r"scenarios\[(\d+)\]", reference)
    if match is None:
        raise ValidationError(f"ссылка на сценарий {reference!r} не разбирается")

    document = _document(source)
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

    # Аптайм объявляет сценарий, и умолчания тут нет. Умолчание в коде уводило
    # величину из-под запрета: проверка «ноль ставить нельзя» сверяла данные, а
    # нулевой аптайм задавался бы не данными. При нуле же показание секундомера
    # совпадает с прошедшим по стенным часам, и реализация, перепутавшая одно с
    # другим, случайно даёт верный ответ - мутация это показала.
    if "uptime_s" not in scenario:
        raise ValidationError(
            "сценарий не объявил аптайма первого процесса. Умолчания тут нет: "
            "аптайм влияет на то, что проверяется, и выбирать его за сценарий "
            "значит проверять не объявленное"
        )
    uptime_base = float(scenario["uptime_s"])
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


def _requests(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Разворачивает запросы трассы из перечня либо порождающего правила.

    Правило нужно там, где запросов сотня и перечень был бы нечитаем: двадцать
    аккаунтов по пять запросов пишутся одной строкой, а читаются так же.

    Args:
        scenario (dict[str, Any]): Сценарий набора rate-budget.

    Returns:
        list[dict[str, Any]]: Запросы в порядке появления.
    """
    listed: list[dict[str, Any]] | None = scenario.get("requests")
    if listed is not None:
        return listed

    rule = scenario["generate"]
    return [
        {
            "at_ms": rule["at_ms"],
            "class": rule["class"],
            "account": f"аккаунт-{account}",
        }
        for account in range(rule["accounts"])
        for _ in range(rule["per_account"])
    ]


def _run_trace(scenario: dict[str, Any]) -> list[int | None]:
    """Прогоняет трассу запросов по виртуальным часам.

    Часы виртуальные и двигает их вызывающий. Бюджет не спит: он отвечает,
    сколько ждать, - иначе набор шёл бы столько же, сколько занимает настоящее
    ожидание, и проверял бы заодно точность таймера.

    Бюджет берётся у СЕТЕВОЙ ИДЕНТИЧНОСТИ, а не заводится под каждый аккаунт, и
    в этом проверяемое: ограничение накладывает площадка, и накладывает она его
    на пару из исходящего адреса и целевого хоста. Двадцать клиентов, каждый со
    своим бюджетом, дают двадцатикратную нагрузку с одного адреса при формально
    соблюдённых правилах.

    Args:
        scenario (dict[str, Any]): Сценарий: запросы и признак ожидания.

    Returns:
        list[int | None]: Метка отправки каждого запроса в миллисекундах
        виртуальных часов. None означает, что запрос не ушёл вовсе.
    """
    # Реестр общий на процесс, и остатки чужого сценария сделали бы трассу
    # зависимой от порядка прогона. Набор обязан давать один ответ всегда.
    REGISTRY.reset()
    budget = REGISTRY.get(identity_of(None, "funpay.com")).budget

    attempts = WAIT_ATTEMPTS if scenario.get("waits", True) else 1
    now_ms = 0
    sent: list[int | None] = []

    for request in _requests(scenario):
        now_ms = max(now_ms, int(request.get("at_ms", 0)))
        request_class = RequestClass(request.get("class", "interactive"))
        cost = float(request.get("cost", 1))

        moment: int | None = None
        for attempt in range(attempts):
            try:
                reservation = budget.require(now_ms / 1000, cost=cost, request_class=request_class)
            except BudgetExhaustedError:
                # Отказ по классу отменяемому либо ожидание дольше предела.
                # И то и другое означает, что запрос не отправлен вовсе.
                break
            if reservation.granted:
                moment = now_ms
                break
            if attempt + 1 == attempts:
                break
            now_ms += reservation.wait_ms

        sent.append(moment)

    return sent


def _capability(name: str) -> Capability:
    """Находит возможность по идентификатору из спецификации.

    Args:
        name (str): Идентификатор вида «orders.list».

    Returns:
        Capability: Возможность.

    Raises:
        ValidationError: Если такой возможности в реализации нет. Молча
            пропустить нельзя: набор объявляет перечень возможностей
            нормативным, и отсутствующая означает не пробел набора, а пробел
            реализации.
    """
    for one in Capability:
        if one.value == name:
            return one
    raise ValidationError(f"возможность «{name}» объявлена спецификацией, а реализации неизвестна")


def _decision(case: dict[str, Any]) -> str:
    """Решает, разрешён ли вызов, и возвращает решение словом.

    Возвращается ИМЯ КЛАССА отказа, а не «отклонено». Вызывающий пишет except по
    классу, и две реализации, отклоняющие одно и то же разными классами,
    заставляют писать разный except - то есть у переносимого кода переносимости
    не остаётся. Отдельно важно различие двух отказов: экспериментальную
    возможность включают и зовут, отсутствующую - не зовут вовсе.

    Args:
        case (dict[str, Any]): Случай с полями capability, state, opted_in.

    Returns:
        str: «разрешено» либо имя класса отказа.
    """
    capability = _capability(case["capability"])
    state = CapabilityState(case["state"])
    try:
        check_capability(capability, state=state, opted_in=bool(case.get("opted_in")))
    except FunoraError as refusal:
        return type(refusal).__name__
    return "разрешено"


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
            return {"id": case_id, "outcome": "pass", "value": got}

        if kind == "fingerprint":
            got = _digest(_resolve(case["vector"]))
            return {"id": case_id, "outcome": "pass", "value": got}

        if kind == "rate_budget":
            trace = _scenario(case["vector"], "rate-budget.vectors.json")
            if trace.get("concurrent"):
                # Пропуск СО ССЫЛКОЙ. Одновременное поступление решает общая
                # очередь с приоритетами, а эталонная реализация ходит на
                # площадку по одному запросу за раз. Пройти сценарий по порядку
                # поступления значило бы показать согласие там, где его нет.
                return {
                    "id": case_id,
                    "outcome": "skip",
                    "not_implemented": trace["requires"],
                }
            return {"id": case_id, "outcome": "pass", "sent": _run_trace(trace)}

        if kind == "capability_decision":
            return {"id": case_id, "outcome": "pass", "value": _decision(case)}

        if kind == "capability_initial":
            # Берётся то, что реализация подставляет ПРИ ВЫЗОВЕ без состояния, а
            # не то, что лежит в порождённой таблице. Совпадение таблицы со
            # спецификацией проверено отдельно; здесь проверяется, что вызов эту
            # таблицу читает.
            #
            # Состояние выводится ИЗ ПОВЕДЕНИЯ, а не читается из таблицы. Три
            # состояния вызов пропускает и возвращает сами себя; два отклоняют,
            # и каждое своим классом - отображение в обе стороны однозначно.
            try:
                state = check_capability(
                    _capability(case["capability"]), state=None, opted_in=False
                ).value
            except UnsupportedCapabilityError:
                state = "unsupported"
            except ExperimentalCapabilityError:
                state = "experimental"
            return {"id": case_id, "outcome": "pass", "value": state}

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
            except FunoraError as refusal:
                # Возвращается ИМЯ КЛАССА, а не просто «отвергнуто». Иначе случай
                # судил бы себя сам: раннер канонической формы не считает и о
                # том, обязан ли вход быть отвергнут, знать не может. Заодно имя
                # держит согласие классов между реализациями - два SDK,
                # отвергающие дробное число разными классами, заставляют
                # вызывающего писать разный except.
                return {
                    "id": case_id,
                    "outcome": "pass",
                    "value": type(refusal).__name__,
                }
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
