"""Непринятая партия и позиция, которую разрешено подтвердить после неё.

Партия записывается до вызова пользовательского кода. Повтор читает её, а не
площадку: новый снимок не может восстановить уже исчезнувшее изменение.
Вложенный ASCII JSON сохраняет непрозрачные позиции и payload без NFC;
внешний StateFile по-прежнему имеет каноническую форму.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from ._diff import Delivery, Event
from ._fileio import file_lock
from ._monitoring import validate_market_cursor, validate_market_payload
from ._state import _unique_pairs
from ._watch import PRODUCIBLE
from .errors import ConfigurationError, CursorIncompatibleError, StateSchemaIncompatibleError
from .events import ORDERING_KEY, EventType


@contextmanager
def watch_lease(lock: Lock, path: Path | None) -> Iterator[None]:
    """Один цикл владеет ядром и файлом; остальные получают отказ без ожидания."""
    if not lock.acquire(blocking=False):
        raise ConfigurationError("на этом ядре уже работает watch")
    try:
        with ExitStack() as stack:
            if path is not None:
                try:
                    stack.enter_context(
                        file_lock(path.with_suffix(path.suffix + ".watch.lock"), blocking=False)
                    )
                except OSError as exc:
                    raise ConfigurationError(
                        "не удалось получить исключительное владение файлом watch"
                    ) from exc
            yield
    finally:
        lock.release()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def validate_cursor(value: Any) -> dict[str, Any]:
    """Проверяет курсор партии до сети и вызова обработчиков."""
    if isinstance(value, dict) and "market" in value:
        return validate_market_cursor(value)
    if not isinstance(value, dict) or set(value) != {
        "orders",
        "chats",
        "threads",
        "pending_threads",
    }:
        raise ValueError("неверный состав курсора партии")
    for name in ("orders", "chats"):
        entries = value[name]
        if entries is not None and (
            not isinstance(entries, dict)
            or not all(_text(key) and isinstance(item, str) for key, item in entries.items())
        ):
            raise ValueError("неверный курсор списка")
    threads = value["threads"]
    if not isinstance(threads, dict) or not all(
        _text(key) and isinstance(ids, list) and all(_text(item) for item in ids)
        for key, ids in threads.items()
    ):
        raise ValueError("неверный курсор переписок")
    pending = value["pending_threads"]
    if not isinstance(pending, list) or not all(_text(item) for item in pending):
        raise ValueError("неверная очередь переписок")
    return value


@dataclass(frozen=True)
class PendingBatch:
    """События, ещё не принятые обработчиками, и целевой курсор всей партии."""

    events: tuple[Event, ...]
    cursor: dict[str, Any]
    greeted: bool

    def encode(self) -> str:
        """Снимает независимую копию до передачи изменяемого payload обработчику."""
        records = []
        for event in self.events:
            record = asdict(event)
            record["observed_at"] = event.observed_at.isoformat()
            records.append(record)
        return json.dumps(
            {"version": 1, "events": records, "cursor": self.cursor, "greeted": self.greeted},
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, raw: Any, *, account_id: str) -> PendingBatch:
        """Отвергает повреждённый журнал вместо молчаливого нового чтения."""
        try:
            if not isinstance(raw, str) or not raw.isascii():
                raise ValueError("журнал должен быть ASCII JSON")
            value = json.loads(raw, object_pairs_hook=_unique_pairs)
            if not isinstance(value, dict) or set(value) != {
                "version",
                "events",
                "cursor",
                "greeted",
            }:
                raise ValueError("неверный состав журнала")
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError("неизвестная версия журнала")
            if type(value["greeted"]) is not bool or not isinstance(value["events"], list):
                raise ValueError("неверные поля журнала")
            cursor = validate_cursor(value["cursor"])
            events: list[Event] = []
            seen: set[str] = set()
            for record in value["events"]:
                if not isinstance(record, dict) or set(record) != {
                    "id",
                    "type",
                    "account_id",
                    "ordering_key",
                    "entity_id",
                    "observed_at",
                    "origin",
                    "payload",
                    "delivery",
                }:
                    raise ValueError("неверный состав события")
                if not all(
                    _text(record[key])
                    for key in (
                        "id",
                        "type",
                        "account_id",
                        "ordering_key",
                        "entity_id",
                        "observed_at",
                        "origin",
                    )
                ):
                    raise ValueError("неверное поле события")
                if record["account_id"] != account_id:
                    raise CursorIncompatibleError(
                        "непринятая партия принадлежит другому account_id"
                    )
                kind = EventType(record["type"])
                if kind not in PRODUCIBLE or record["id"] in seen:
                    raise ValueError("неизвестное либо повторное событие")
                seen.add(record["id"])
                key_value = record["entity_id"]
                if "market" in cursor:
                    payload = record["payload"]
                    if (
                        not isinstance(payload, dict)
                        or payload.get("watch_id") not in cursor["market"]
                    ):
                        raise ValueError("событие другого наблюдения")
                    key_value = payload["watch_id"]
                    market_kinds = {
                        EventType.MARKET_OFFER_APPEARED,
                        EventType.MARKET_OFFER_DISAPPEARED,
                        EventType.MARKET_PRICE_CHANGED,
                    }
                    if kind in market_kinds:
                        if payload.get("offer_id") != record["entity_id"]:
                            raise ValueError("событие другого предложения")
                        validate_market_payload(
                            kind, payload, cursor["market"][key_value]["config"]["node_id"]
                        )
                    elif (
                        kind not in {EventType.WATCH_PRIMED, EventType.SNAPSHOT_INCOMPLETE}
                        or record["entity_id"] != key_value
                    ):
                        raise ValueError("чужой вид события в партии рынка")
                elif kind in {
                    EventType.MARKET_OFFER_APPEARED,
                    EventType.MARKET_OFFER_DISAPPEARED,
                    EventType.MARKET_PRICE_CHANGED,
                }:
                    raise ValueError("рыночное событие в личной партии")
                if record["ordering_key"] != ORDERING_KEY[kind].format(
                    **dict.fromkeys(
                        ("order_id", "chat_id", "watch_id", "account_id"),
                        key_value,
                    )
                ):
                    raise ValueError("неверный ключ упорядочивания")
                moment = datetime.fromisoformat(record["observed_at"])
                if moment.tzinfo is None or moment.utcoffset() is None:
                    raise ValueError("момент события без часового пояса")
                delivery = record["delivery"]
                if not isinstance(delivery, dict) or set(delivery) != {"attempt", "coalesced"}:
                    raise ValueError("неверные метаданные доставки")
                if (
                    type(delivery["attempt"]) is not int
                    or delivery["attempt"] < 0
                    or delivery["coalesced"] is not False
                ):
                    raise ValueError("неверный номер попытки либо сжатая партия")
                if not isinstance(record["payload"], dict):
                    raise ValueError("нагрузка должна быть объектом")
                # Запрещены NaN/Infinity и другие непереносимые значения JSON.
                json.dumps(record["payload"], allow_nan=False)
                events.append(
                    Event(
                        **{
                            **record,
                            "type": kind,
                            "observed_at": moment,
                            "delivery": Delivery(**delivery),
                        }
                    )
                )
            return cls(tuple(events), cursor, value["greeted"])
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise StateSchemaIncompatibleError(
                "непринятая партия watch повреждена; её нельзя заменить новым снимком"
            ) from exc
