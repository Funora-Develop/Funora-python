"""Описание наблюдений и чистое продвижение истории публичной выдачи."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

from ._diff import Event, make_event
from ._money import Money
from ._snapshot import MarketSnapshot, fingerprint_of
from ._watch import incomplete, primed
from .budget import MARKET_ABSENCES, MARKET_INTERVAL_MS
from .errors import ValidationError
from .events import EventType
from .operations import OPERATIONS


@dataclass(frozen=True, slots=True)
class MarketWatch:
    """Одна выдача /lots/: устойчивое имя, раздел и интервал между чтениями.

    interval_ms принадлежит политике клиента, а не лимитам FunPay.
    Допуск набора проверяет monitoring.plan; фактические запросы - Budget.
    """

    watch_id: str
    node_id: str
    interval_ms: int = MARKET_INTERVAL_MS

    def __post_init__(self) -> None:
        if not isinstance(self.watch_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", self.watch_id
        ):
            raise ValidationError("watch_id: нужны 1-128 латинских букв, цифр, _, . или -")
        if not isinstance(self.node_id, str) or not re.fullmatch(r"[0-9]{1,128}", self.node_id):
            raise ValidationError("node_id: нужны 1-128 цифр ASCII")
        if type(self.interval_ms) is not int or self.interval_ms <= 0:
            raise ValidationError("interval_ms должен быть положительным целым числом")
        try:
            finite = math.isfinite(self.interval_ms / 1000)
        except OverflowError:
            finite = False
        if not finite:
            raise ValidationError("interval_ms не представим часами runtime")

    @property
    def requests_per_second(self) -> Fraction:
        """Прогноз одного тика из профиля операции адаптера, без округления."""
        return Fraction(OPERATIONS["market.snapshot"].cost_hint * 1000, self.interval_ms)


@dataclass(frozen=True, slots=True)
class MonitoringLimit:
    """Прогноз для сетевого ведра, включая уже зарегистрированные наблюдения."""

    bucket: str
    requests_per_second: Fraction
    available_per_second: Fraction


@dataclass(frozen=True, slots=True)
class MonitoringPlan:
    """Проверка набора без HTTP и расхода токенов; это не резервирование."""

    admitted: bool
    requests_per_second: Fraction
    limits: tuple[MonitoringLimit, ...]
    rejected_watch_ids: tuple[str, ...]
    reason: str | None = None


def validate_watches(watches: tuple[MarketWatch, ...]) -> None:
    if not watches or any(not isinstance(one, MarketWatch) for one in watches):
        raise ValidationError("нужен непустой набор MarketWatch")
    if len({one.watch_id for one in watches}) != len(watches):
        raise ValidationError("watch_id не должны повторяться")


def initial_cursor(watches: tuple[MarketWatch, ...]) -> dict[str, Any]:
    return {
        "market": {
            one.watch_id: {
                "config": asdict(one),
                "sequence": 0,
                "baseline": False,
                "offers": {},
                "absences": {},
            }
            for one in watches
        }
    }


def validate_market_cursor(value: Any) -> dict[str, Any]:
    """Повреждение истории не превращается в холодный старт."""
    if not isinstance(value, dict) or set(value) != {"market"}:
        raise ValueError("неверный состав курсора рынка")
    watches = value["market"]
    if not isinstance(watches, dict) or not watches:
        raise ValueError("пустой набор наблюдений")
    for watch_id, record in watches.items():
        if not isinstance(record, dict) or set(record) != {
            "config",
            "sequence",
            "baseline",
            "offers",
            "absences",
        }:
            raise ValueError("неверный состав истории")
        config = record["config"]
        if not isinstance(config, dict) or set(config) != {"watch_id", "node_id", "interval_ms"}:
            raise ValueError("неверный состав наблюдения")
        try:
            watch = MarketWatch(**config)
        except ValidationError as exc:
            raise ValueError("неверное наблюдение") from exc
        if (
            watch.watch_id != watch_id
            or type(record["sequence"]) is not int
            or record["sequence"] < 0
        ):
            raise ValueError("неверный владелец или номер снимка")
        if type(record["baseline"]) is not bool:
            raise ValueError("неверный признак полного основания")
        offers, absences = record["offers"], record["absences"]
        if (
            not isinstance(offers, dict)
            or not isinstance(absences, dict)
            or set(absences) - set(offers)
        ):
            raise ValueError("неверный состав предложений или отсутствий")
        for offer_id, entry in offers.items():
            if (
                not isinstance(offer_id, str)
                or not offer_id
                or not isinstance(entry, dict)
                or set(entry) != {"price", "seller_id"}
            ):
                raise ValueError("неверное предложение")
            if entry["seller_id"] is not None and (
                not isinstance(entry["seller_id"], str)
                or not re.fullmatch(r"[0-9]{1,128}", entry["seller_id"])
            ):
                raise ValueError("неверный продавец")
            money = entry["price"]
            if money is not None:
                if not isinstance(money, dict) or set(money) != {
                    "amount_minor",
                    "currency",
                    "scale",
                }:
                    raise ValueError("неверный состав цены")
                try:
                    Money(**money)
                except ValidationError as exc:
                    raise ValueError("неверная цена") from exc
        if any(
            type(count) is not int or not 1 <= count < MARKET_ABSENCES
            for count in absences.values()
        ):
            raise ValueError("неверный счётчик отсутствий")
        if record["sequence"] == 0 and (offers or absences or record["baseline"]):
            raise ValueError("состояние до первого снимка не пусто")
    return value


def observe_market(
    cursor: dict[str, Any],
    watch: MarketWatch,
    snapshot: MarketSnapshot,
    *,
    account_id: str,
) -> tuple[dict[str, Any], tuple[Event, ...]]:
    """Положительные наблюдения дополняют историю; отсутствие требует полноты."""
    if snapshot.node_id != watch.node_id or snapshot.query_fingerprint != fingerprint_of(
        watch.node_id
    ):
        raise ValidationError("снимок принадлежит другой выдаче")
    # Новая независимая позиция; прежняя остаётся до подтверждения доставки.
    target = json.loads(json.dumps(cursor, ensure_ascii=True, allow_nan=False))
    record = target["market"][watch.watch_id]
    sequence = record["sequence"] + 1
    events: list[Event] = []
    if sequence == 1:
        events.append(primed(account_id, snapshot.taken_at, ("market",), watch_id=watch.watch_id))
    if not snapshot.is_complete:
        events.append(
            incomplete(
                account_id,
                snapshot.taken_at,
                entity="market",
                entity_ref=watch.node_id,
                reason=snapshot.reason or "incomplete",
                rows_total=snapshot.rows_total,
                rows_accepted=snapshot.rows_accepted,
                watch_id=watch.watch_id,
            )
        )
        record["absences"] = {}

    def emit(kind: EventType, offer_id: str, payload: dict[str, Any]) -> None:
        events.append(
            make_event(
                account_id=account_id,
                event_type=kind,
                entity_id=offer_id,
                revision=json.dumps(
                    [watch.watch_id, sequence], ensure_ascii=True, separators=(",", ":")
                ),
                observed_at=snapshot.taken_at,
                key_field="watch_id",
                key_value=watch.watch_id,
                payload={"watch_id": watch.watch_id, "offer_id": offer_id, **payload},
            )
        )

    offers = record["offers"]
    for offer_id, entry in snapshot.offers.items():
        previous = offers.get(offer_id)
        price = asdict(entry.price.value) if entry.price.is_observed else None
        seller = re.fullmatch(r"(?:https://funpay\.com)?/users/([0-9]{1,128})/", entry.seller_href)
        seller_id = seller[1] if seller else None
        if previous is None:
            if record["baseline"] and price is not None and seller_id is not None:
                emit(
                    EventType.MARKET_OFFER_APPEARED,
                    offer_id,
                    {
                        "seller_id": seller_id,
                        "price": price,
                        "query_fingerprint": snapshot.query_fingerprint,
                    },
                )
        elif price is not None and previous["price"] is not None and price != previous["price"]:
            emit(
                EventType.MARKET_PRICE_CHANGED,
                offer_id,
                {"before": previous["price"], "latest": price, "aggregate": None},
            )
        offers[offer_id] = {
            "price": price if price is not None else previous["price"] if previous else None,
            "seller_id": seller_id
            if seller_id is not None
            else previous["seller_id"]
            if previous
            else None,
        }
        record["absences"].pop(offer_id, None)

    if snapshot.is_complete:
        for offer_id in sorted(set(offers) - set(snapshot.offers)):
            count = record["absences"].get(offer_id, 0) + 1
            if count >= MARKET_ABSENCES:
                emit(
                    EventType.MARKET_OFFER_DISAPPEARED,
                    offer_id,
                    {
                        "query_fingerprint": snapshot.query_fingerprint,
                        "consecutive_absences": count,
                        "filters_affect_visibility": False,
                    },
                )
                del offers[offer_id]
                record["absences"].pop(offer_id, None)
            else:
                record["absences"][offer_id] = count
        record["baseline"] = True
    record["sequence"] = sequence
    return target, tuple(events)


def validate_market_transition(
    before: dict[str, Any],
    after: dict[str, Any],
    events: tuple[Event, ...],
) -> str:
    """Одна непринятая партия подтверждает ровно один следующий снимок."""
    previous: dict[str, Any] = before["market"]
    latest = after.get("market", {})
    if set(previous) != set(latest) or any(
        one["config"] != latest[key]["config"] for key, one in previous.items()
    ):
        raise ValueError("другой набор наблюдений")
    changed = [key for key in previous if previous[key] != latest[key]]
    if len(changed) != 1:
        raise ValueError("партия должна продвигать один снимок")
    watch_id = changed[0]
    if latest[watch_id]["sequence"] != previous[watch_id]["sequence"] + 1:
        raise ValueError("пропущен номер снимка")
    if any(event.payload.get("watch_id") != watch_id for event in events):
        raise ValueError("партия и снимок принадлежат разным наблюдениям")
    return watch_id


def validate_market_payload(kind: EventType, payload: dict[str, Any], node_id: str) -> None:
    """Восстановленная цена и ссылка должны соблюдать тот же контракт, что новые."""
    fields = {
        EventType.MARKET_OFFER_APPEARED: {"seller_id", "price", "query_fingerprint"},
        EventType.MARKET_OFFER_DISAPPEARED: {
            "query_fingerprint",
            "consecutive_absences",
            "filters_affect_visibility",
        },
        EventType.MARKET_PRICE_CHANGED: {"before", "latest", "aggregate"},
    }[kind]
    if set(payload) != fields | {"watch_id", "offer_id"}:
        raise ValueError("неверный состав рыночного события")
    if "query_fingerprint" in fields and payload["query_fingerprint"] != fingerprint_of(node_id):
        raise ValueError("событие другой выдачи")
    if kind is EventType.MARKET_OFFER_APPEARED and (
        not isinstance(payload["seller_id"], str)
        or not re.fullmatch(r"[0-9]{1,128}", payload["seller_id"])
    ):
        raise ValueError("неверный продавец события")
    if kind is EventType.MARKET_PRICE_CHANGED and payload["aggregate"] is not None:
        raise ValueError("сжатая партия не поддерживается")
    if kind is EventType.MARKET_OFFER_DISAPPEARED and (
        type(payload["consecutive_absences"]) is not int
        or payload["consecutive_absences"] < 1
        or payload["filters_affect_visibility"] is not False
    ):
        raise ValueError("неверное подтверждение отсутствия")
    for name in fields & {"price", "before", "latest"}:
        money = payload[name]
        if not isinstance(money, dict) or set(money) != {"amount_minor", "currency", "scale"}:
            raise ValueError("неверный состав цены события")
        try:
            Money(**money)
        except ValidationError as exc:
            raise ValueError("неверная цена события") from exc
