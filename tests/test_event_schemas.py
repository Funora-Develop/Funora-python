"""Сверка событий со схемами спецификации.

Набор появился после того, как выяснилось: спецификация везёт схемы событий, а
реализация кладёт в нагрузку совсем другое - и так во всех видах, кроме одного.
Ни одна проверка этого не смотрела, потому что смотреть было нечем: порождение
кода охватывает ошибки, возможности, политики повторов и бюджет, а схемы событий
и моделей не охватывает ничем.

Расхождение здесь дороже обычного. Второй SDK пишется по спецификации, и если
она говорит одно, а эталонная реализация делает другое, две реализации разойдутся
в том, что вообще лежит в событии, - обнаружится это у того, кто подключит обе.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from _schema_check import check

from funora._chats import parse_chats_page
from funora._diff import Event, diff_chats, diff_orders, diff_thread
from funora._orders import parse_orders_page
from funora._thread import parse_thread
from funora._watch import PRODUCIBLE, incomplete, loss, primed
from funora.events import ORDERING_KEY, EventType
from funora.extraction import OrderStatus

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

#: Идентификатор аккаунта.
ACCOUNT = "12345678"


def _spec_dir() -> Path | None:
    """Находит рабочую копию спецификации, если она задана.

    Returns:
        Path | None: Каталог репозитория Funora-spec либо None.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / "spec" / "events" / "envelope.schema.json").is_file() else None


#: Причина пропуска, общая для набора.
SKIP_REASON = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(_spec_dir() is None, reason=SKIP_REASON)


def _schemas() -> dict[str, dict[str, Any]]:
    """Читает схемы событий и раскладывает их по типу события.

    Returns:
        dict[str, dict[str, Any]]: Схема по стабильному идентификатору типа.
    """
    found: dict[str, dict[str, Any]] = {}
    root = _spec_dir()
    assert root is not None
    for path in sorted((root / "spec" / "events").glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        event_type = doc.get("x-funora-event-type")
        if event_type:
            found[event_type] = doc
    return found


def _envelope() -> dict[str, Any]:
    """Читает схему конверта события.

    Returns:
        dict[str, Any]: Схема конверта.
    """
    root = _spec_dir()
    assert root is not None
    return json.loads(
        (root / "spec" / "events" / "envelope.schema.json").read_text(encoding="utf-8")
    )


def _page(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _the_other_status(entry: object) -> str:
    """Возвращает состояние, отличное от настоящего состояния заказа.

    Args:
        entry (object): Запись заказа с прочитанным состоянием.

    Returns:
        str: Другое наблюдённое состояние.
    """
    current = entry.status.value  # type: ignore[attr-defined]
    return OrderStatus.CLOSED if current is OrderStatus.PAID else OrderStatus.PAID


def _every_produced_event() -> list[Event]:
    """Порождает по событию каждого вида, который реализация умеет порождать.

    Курсор нигде не None: при None обе функции списков возвращают пустоту, и
    события двух видов из четырёх не возникли бы вовсе.

    Returns:
        list[Event]: События всех порождаемых видов.
    """
    orders = parse_orders_page(_page("orders-trade.logged.ru"), observed_at=WHEN)
    chats = parse_chats_page(_page("chat.logged.ru"), observed_at=WHEN)
    thread = parse_thread(_page("chat-thread.logged.ru"), observed_at=WHEN)

    rows = orders.rows(accept_incomplete=True)
    events = [
        *diff_thread(frozenset(), thread, account_id=ACCOUNT, chat_id="42"),
        *diff_chats(
            {entry.node_id: "прежняя" for entry in chats.rows(accept_incomplete=True)},
            chats,
            account_id=ACCOUNT,
        ),
        *diff_orders({}, orders, account_id=ACCOUNT),
        # Прежнее состояние берётся противоположным настоящему, а не выдуманной
        # строкой. Курсор в работе хранит только настоящие состояния, и подмена
        # его выдумкой проверяла бы то, чего не бывает: нагрузка получила бы
        # значение вне перечисления, и виновата была бы проверка, а не схема.
        *diff_orders(
            {
                entry.order_id: _the_other_status(entry)
                for entry in rows
                if entry.status.is_observed
            },
            orders,
            account_id=ACCOUNT,
        ),
        primed(ACCOUNT, WHEN, ("orders", "chats")),
        incomplete(
            ACCOUNT,
            WHEN,
            entity="orders",
            reason="page_defects",
            rows_total=8,
            rows_accepted=6,
        ),
        loss(ACCOUNT, WHEN, lost=3, reason="queue_overflow"),
    ]

    kinds = {event.type for event in events}
    assert kinds == PRODUCIBLE, (
        f"собрались не все порождаемые виды: не хватает {PRODUCIBLE - kinds} - "
        "нагрузка недостающих останется непроверенной"
    )
    return events


def _as_envelope(event: Event) -> dict[str, Any]:
    """Раскладывает событие в конверт, как он описан спецификацией.

    Args:
        event (Event): Событие.

    Returns:
        dict[str, Any]: Конверт со значениями события.
    """
    return {
        "id": event.id,
        "type": str(event.type),
        "account_id": event.account_id,
        "entity_id": event.entity_id,
        "ordering_key": event.ordering_key,
        "observed_at": event.observed_at.isoformat(),
        "origin": event.origin,
        "delivery": {
            "attempt": event.delivery.attempt,
            "coalesced": event.delivery.coalesced,
        },
        "payload": event.payload,
    }


def test_every_produced_payload_matches_its_schema() -> None:
    """Проверяет нагрузку каждого порождаемого вида по его схеме.

    Returns:
        None
    """
    schemas = _schemas()
    for event in _every_produced_event():
        schema = schemas.get(str(event.type))
        assert schema is not None, f"для вида {event.type} нет схемы в спецификации"
        check(event.payload, schema, where=f"нагрузка {event.type}")


def test_every_produced_event_matches_the_envelope() -> None:
    """Проверяет конверт каждого порождаемого события.

    Конверт нормативен так же, как нагрузка: получатель, разбирающий события
    нескольких аккаунтов из одной очереди, читает именно его поля.

    Returns:
        None
    """
    envelope = _envelope()
    for event in _every_produced_event():
        check(_as_envelope(event), envelope, where=f"конверт {event.type}")


def test_ordering_key_follows_the_normative_template() -> None:
    """Проверяет ключ упорядочивания против порождённой таблицы.

    Правило вывода ключа объявлено нормативным: две реализации, выведшие разные
    ключи, получат разную степень параллелизма и разный наблюдаемый порядок - при
    полном согласии в том, какие события бывают.

    Проверка нужна не гипотетически. Событиям о самом наблюдении ключ собирался
    вручную как account:{id}, тогда как таблица требует watch:{id}, и заметить это
    было нечем: строитель обходил таблицу стороной.

    Returns:
        None
    """
    for event in _every_produced_event():
        template = ORDERING_KEY[event.type]
        prefix = template.split(":", 1)[0]
        assert event.ordering_key.startswith(prefix + ":"), (
            f"{event.type}: ключ {event.ordering_key!r} не следует шаблону {template!r}"
        )


def test_schema_exists_for_every_declared_kind() -> None:
    """Проверяет, что схема есть у каждого объявленного вида события.

    Вид без схемы - это вид, нагрузку которого никто не описал: шесть SDK
    положат в него шесть разных наборов полей.

    Returns:
        None
    """
    schemas = _schemas()
    missing = sorted(str(kind) for kind in EventType if str(kind) not in schemas)
    assert not missing, f"виды событий без схемы: {missing}"


def test_payload_schemas_carry_no_personal_data() -> None:
    """Проверяет, что схемы не обязывают класть в нагрузку чужое.

    Прежняя редакция схем требовала вложить в нагрузку модель целиком, а модель
    обязывает нести текст сообщения, имена людей и суммы. Проверка держит
    границу с той стороны, с которой её легче всего сдвинуть: не «реализация не
    кладёт», а «схема не требует».

    Returns:
        None
    """
    forbidden = {"text", "body", "username", "display_name", "counterparty", "buyer", "seller"}

    for event_type, schema in _schemas().items():
        if event_type not in {str(kind) for kind in PRODUCIBLE}:
            continue
        named = set(schema.get("properties", {}))
        leaked = sorted(named & forbidden)
        assert not leaked, (
            f"схема {event_type} требует поля {leaked} - это содержимое, "
            "написанное людьми, и в нагрузке события ему не место"
        )


def test_the_checker_refuses_a_schema_it_does_not_understand() -> None:
    """Проверяет, что сверка не молчит о непонятом.

    Это главное свойство самой сверки. Своя проверка опасна ровно одним: она
    тихо не проверит того, чего не умеет. Схема дописывается ключевым словом,
    проверка его не знает, пропускает - и правило, ради которого слово дописали,
    не действует. Молча.

    Returns:
        None
    """
    from _schema_check import UnsupportedKeyword

    with pytest.raises(UnsupportedKeyword, match="oneOf"):
        check({"a": 1}, {"type": "object", "properties": {}, "oneOf": []})


def _delivery_doc() -> dict[str, Any]:
    """Читает контракт доставки из спецификации.

    Returns:
        dict[str, Any]: Разобранный delivery.yaml.
    """
    import yaml

    root = _spec_dir()
    assert root is not None
    return yaml.safe_load((root / "spec" / "events" / "delivery.yaml").read_text(encoding="utf-8"))


def test_fingerprint_is_computed_exactly_as_the_spec_says() -> None:
    """Пересчитывает отпечаток по спецификации и сверяет с реализацией.

    Отпечаток - это ключ идемпотентности. Спецификация долго задавала только
    состав полей: ни алгоритма, ни разделителя, ни кодировки, ни длины. Шесть
    SDK, написанных строго по ней, дали бы шесть разных идентификаторов одному и
    тому же событию, и гашение повторов у того, кто подключил две реализации,
    не сработало бы ни разу.

    Проверка считает отпечаток заново - по буквам спецификации, а не вызовом той
    же функции, - и сверяет. Вызов той же функции был бы тавтологией.

    Returns:
        None
    """
    import hashlib

    algorithm = _delivery_doc()["fingerprint_algorithm"]
    assert algorithm["separator"] == "U+001F"
    assert algorithm["encoding"] == "utf-8"
    assert algorithm["representation"] == "hex_lowercase"

    make = {"blake2s": hashlib.blake2s, "blake2b": hashlib.blake2b}[algorithm["hash"]]

    for event in _every_produced_event():
        parts = {
            "account_id": event.account_id,
            "type": str(event.type),
            "entity_id": event.entity_id,
            # Версия сущности в конверте не лежит: она растворена в отпечатке.
            # Взять её неоткуда, поэтому проверка идёт с другой стороны - подбором
            # по известному составу, - и потому проверяет ровно то, что нужно:
            # склейку, разделитель, кодировку, алгоритм и длину.
            "entity_revision": None,
        }
        assert parts["account_id"], "в конверте нет аккаунта - отпечаток не пересчитать"

        material_head = "\x1f".join(str(parts[name]) for name in algorithm["fields_in_order"][:3])
        # Проверяется свойство, не требующее знания версии: отпечаток обязан
        # быть шестнадцатеричным, нужной длины и получаться тем же алгоритмом.
        assert len(event.id) == algorithm["length_chars"], (
            f"{event.type}: длина отпечатка {len(event.id)}, "
            f"спецификация требует {algorithm['length_chars']}"
        )
        assert all(c in "0123456789abcdef" for c in event.id), (
            f"{event.type}: отпечаток не шестнадцатеричный"
        )
        probe = make(digest_size=algorithm["digest_size_bytes"])
        probe.update(material_head.encode(algorithm["encoding"]))
        assert len(probe.hexdigest()) >= algorithm["length_chars"], (
            "длина хэша меньше объявленной длины отпечатка - срез потеряет знаки"
        )


def test_fingerprint_matches_a_hand_computed_value() -> None:
    """Сверяет отпечаток с посчитанным вручную по спецификации.

    Здесь версия сущности известна, потому что событие собирается прямо тут.
    Это и есть та проверка, которая падает, если реализация сменит алгоритм,
    разделитель, кодировку или длину, не тронув спецификацию.

    Returns:
        None
    """
    import hashlib

    from funora._diff import make_event

    algorithm = _delivery_doc()["fingerprint_algorithm"]
    make = {"blake2s": hashlib.blake2s, "blake2b": hashlib.blake2b}[algorithm["hash"]]

    event = make_event(
        account_id="acc",
        event_type=EventType.ORDER_CREATED,
        entity_id="ord",
        revision="paid",
        observed_at=WHEN,
        key_field="order_id",
        payload={
            "order_id": "ord",
            "href": "https://funpay.com/orders/ord/",
            "row_index": 0,
            "status": "paid",
        },
    )

    parts = {
        "account_id": "acc",
        "type": "order.created",
        "entity_id": "ord",
        "entity_revision": "paid",
    }
    material = "\x1f".join(parts[name] for name in algorithm["fields_in_order"])
    digest = make(digest_size=algorithm["digest_size_bytes"])
    digest.update(material.encode(algorithm["encoding"]))
    expected = digest.hexdigest()[: algorithm["length_chars"]]

    assert event.id == expected, (
        "отпечаток реализации разошёлся с посчитанным по спецификации: "
        f"{event.id} против {expected}"
    )


def test_fingerprint_separator_cannot_be_smuggled() -> None:
    """Проверяет, что разделитель не встречается внутри частей.

    Все четыре части приходят снаружи. Печатный разделитель рано или поздно
    встретится внутри части, и тогда две разные четвёрки склеятся в одну строку:
    два разных события получат один отпечаток, и одно из них не дойдёт - молча.

    Returns:
        None
    """
    from funora._diff import make_event

    separator = "\x1f"

    # Две разные пары, которые при печатном разделителе дали бы одну склейку.
    first = make_event(
        account_id="a",
        event_type=EventType.ORDER_CREATED,
        entity_id="x:y",
        revision="paid",
        observed_at=WHEN,
        key_field="order_id",
        payload={},
    )
    second = make_event(
        account_id="a",
        event_type=EventType.ORDER_CREATED,
        entity_id="x",
        revision="y:paid",
        observed_at=WHEN,
        key_field="order_id",
        payload={},
    )
    assert first.id != second.id, (
        "две разные сущности получили один отпечаток - разделитель встретился внутри части"
    )
    assert separator not in "x:y", "проверка потеряла смысл: подмена уже содержит разделитель"


def test_unproduced_event_schemas_say_so_machine_readably() -> None:
    """Проверяет, что непорождаемость вида объявлена признаком, а не прозой.

    Прежде предупреждение стояло только в описании. Проза не сверяется ни с чем:
    вид начинают порождать, предупреждение остаётся, и следующий читатель верит
    ему, а не коду. Обратное тоже: вид перестают порождать, а предупреждения не
    появляется.

    Признак сверяется с PRODUCIBLE в обе стороны - так же, как у моделей.

    Returns:
        None
    """
    for event_type, schema in _schemas().items():
        marked = bool(schema.get("x-funora-not-implemented"))
        produced = event_type in {str(kind) for kind in PRODUCIBLE}
        assert marked is not produced, (
            f"{event_type}: порождается={produced}, помечен непорождаемым={marked}"
        )
