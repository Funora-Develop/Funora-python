"""Снимок выдачи раздела и сравнение двух снимков.

ЗАЧЕМ ОТДЕЛЬНО ОТ [_market.py]. Тот читает страницу: строки в порядке показа,
со всеми полями. Здесь - то, что СРАВНИВАЕТСЯ: предложения по идентификатору,
отпечаток запроса и полнота.

Разделены они не для порядка. Порядок строк меняется от поднятия чужого лота, и
сравнение по позициям давало бы поток ложных изменений каждую минуту.
Сравнивать надо по идентификатору - а идентификатор чужого предложения стал
наблюдаем только 31.08.2026, с формата скелета v9. До него этого модуля не могло
существовать.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Порождения событий рынка. Контракт объявляет у
market.offer.appeared обязательным поле price типа Money, а Money требует кода
валюты, которого страница не даёт. Событие об исчезновении вдобавок требует
счётчика подряд идущих отсутствий - его ведёт тот, кто хранит снимки, а не
чистое сравнение.

Поэтому здесь выдаётся РАЗНИЦА как данные, а не события. Разница честна: по ней
видно, что появилось, что пропало и у чего сменилась цена. Объявленные события
остаются в spec/conformance/not-implemented.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Final

from ._market import MarketPage
from ._result import Completeness
from .errors import UsageError

__all__ = [
    "MarketSnapshot",
    "SnapshotEntry",
    "MarketDiff",
    "PriceChange",
    "snapshot_of",
    "compare",
]

#: Сколько знаков отпечатка запроса хранится.
#:
#: Отпечаток служит равенству, а не тайне: шестнадцати шестнадцатеричных знаков
#: хватает, чтобы два разных запроса не совпали случайно.
_FINGERPRINT_LENGTH: Final[int] = 16


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    """Одно предложение внутри снимка.

    Attributes:
        offer_id (str): Идентификатор предложения. По нему идёт сравнение.
        price_text (str): Цена, как показана, без знака валюты.
        currency_symbol_text (str): Знак валюты. Входит в сравнение вместе с
            ценой: смена знака при том же числе - это смена цены.
        seller_href (str): Ссылка на профиль продавца.
        position (int): Место в выдаче, считая с нуля.
    """

    offer_id: str
    price_text: str
    currency_symbol_text: str
    seller_href: str
    position: int


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Состояние выдачи раздела в один момент.

    Attributes:
        query_fingerprint (str): Отпечаток запроса. Сравнивать можно только
            снимки с одинаковым отпечатком.
        node_id (str): Раздел, чья выдача снята.
        taken_at (datetime): Момент снятия.
        completeness (Completeness): Полнота снимка.
        reason (str | None): Причина неполноты. None означает полный снимок.
        rows_total (int): Сколько строк нашлось.
        rows_accepted (int): Сколько строк собрано целиком.
        offers (dict[str, SnapshotEntry]): Предложения по идентификатору.
    """

    query_fingerprint: str
    node_id: str
    taken_at: datetime
    completeness: Completeness
    reason: str | None
    rows_total: int
    rows_accepted: int
    offers: dict[str, SnapshotEntry]

    @property
    def is_complete(self) -> bool:
        """Говорит, полон ли снимок.

        Возвращает:
            bool: True, если полнота complete.
        """
        return self.completeness is Completeness.COMPLETE


@dataclass(frozen=True, slots=True)
class PriceChange:
    """Смена цены у предложения, оставшегося на месте.

    Attributes:
        offer_id (str): Предложение.
        before (SnapshotEntry): Каким было.
        after (SnapshotEntry): Каким стало.
    """

    offer_id: str
    before: SnapshotEntry
    after: SnapshotEntry


@dataclass(frozen=True, slots=True)
class MarketDiff:
    """Разница между двумя снимками одной выдачи.

    Attributes:
        appeared (tuple[SnapshotEntry, ...]): Предложения, которых в прежнем
            снимке не было.
        absent (tuple[SnapshotEntry, ...]): Предложения, которых нет в новом.

            ИМЕННО ОТСУТСТВУЮЩИЕ, А НЕ ИСЧЕЗНУВШИЕ. Одно отсутствие не значит
            исчезновения: предложение могло не попасть в чтение. Контракт
            требует у события об исчезновении счётчика подряд идущих
            отсутствий, и вести его - дело того, кто хранит снимки.
        price_changed (tuple[PriceChange, ...]): У кого сменилась цена либо знак
            валюты.
        seller_changed (tuple[PriceChange, ...]): У кого сменился продавец.
        absences_trusted (bool): Можно ли делать выводы об отсутствии.

            Ложь означает, что один из снимков неполон, и список absent
            заполнен не будет: неполный снимок не отличает «пропало» от «не
            прочитали».
    """

    appeared: tuple[SnapshotEntry, ...]
    absent: tuple[SnapshotEntry, ...]
    price_changed: tuple[PriceChange, ...]
    seller_changed: tuple[PriceChange, ...]
    absences_trusted: bool


def fingerprint_of(node_id: str) -> str:
    """Считает отпечаток запроса.

    В отпечаток входит один раздел. Фильтры площадки - сервер и наличие -
    наблюдались только в исходном, пустом состоянии; класть в отпечаток
    ненаблюдённое значило бы объявить о нём знание.

    Аргументы:
        node_id (str): Раздел.

    Возвращает:
        str: Отпечаток.
    """
    return sha256(f"node={node_id}".encode()).hexdigest()[:_FINGERPRINT_LENGTH]


def snapshot_of(page: MarketPage, *, node_id: str) -> MarketSnapshot:
    """Собирает снимок из прочитанной страницы.

    ПРЕДЛОЖЕНИЯ БЕЗ ИДЕНТИФИКАТОРА В СНИМОК НЕ ПОПАДАЮТ. Сравнивать их не по
    чему, а положив их под выдуманным ключом, мы породили бы исчезновение на
    ровном месте: в следующем снимке выдуманный ключ будет другим.

    Их потеря при этом видна: rows_accepted берётся у страницы и остаётся
    больше, чем число предложений в снимке.

    Аргументы:
        page (MarketPage): Прочитанная страница.
        node_id (str): Раздел, у которого она прочитана.

    Возвращает:
        MarketSnapshot: Снимок, пригодный для сравнения.
    """
    offers: dict[str, SnapshotEntry] = {}
    for one in page.offers(accept_incomplete=True):
        if not one.offer_id.is_observed:
            continue
        offers[one.offer_id.value] = SnapshotEntry(
            offer_id=one.offer_id.value,
            price_text=one.price_text.or_none() or "",
            currency_symbol_text=one.currency_symbol_text.or_none() or "",
            seller_href=one.seller_href.or_none() or "",
            position=one.row_index,
        )

    return MarketSnapshot(
        query_fingerprint=fingerprint_of(node_id),
        node_id=node_id,
        taken_at=page.observed_at,
        completeness=page.completeness,
        reason=page.reason,
        rows_total=page.rows_total,
        rows_accepted=page.rows_accepted,
        offers=offers,
    )


def compare(before: MarketSnapshot, after: MarketSnapshot) -> MarketDiff:
    """Сравнивает два снимка одной выдачи.

    ОТПЕЧАТКИ ОБЯЗАНЫ СОВПАДАТЬ. Снимок раздела с фильтром и снимок того же
    раздела без фильтра описывают разные множества; сравнив их, получишь
    исчезновение всего, что отфильтровано.

    ОТСУТСТВИЯ СЧИТАЮТСЯ ТОЛЬКО ПО ДВУМ ПОЛНЫМ СНИМКАМ. Неполный не отличает
    «пропало» от «не прочитали», и разница между ними - это разница между
    «конкурент ушёл, можно поднять цену» и «мы плохо прочитали страницу».

    Появления при этом считаются и по неполным: предложение, которого раньше не
    было, а теперь есть, вправду есть - неполнота могла его скрыть прежде, но
    не могла выдумать сейчас.

    Аргументы:
        before (MarketSnapshot): Прежний снимок.
        after (MarketSnapshot): Новый снимок.

    Возвращает:
        MarketDiff: Разница.

    Raises:
        UsageError: Если отпечатки запросов не совпадают.
    """
    if before.query_fingerprint != after.query_fingerprint:
        raise UsageError(
            f"снимки сняты разными запросами: {before.query_fingerprint} и "
            f"{after.query_fingerprint}. Сравнивать их нельзя - разные запросы "
            "описывают разные множества, и разница вышла бы исчезновением "
            "всего, что не попало во второй"
        )

    appeared = tuple(entry for key, entry in after.offers.items() if key not in before.offers)

    trusted = before.is_complete and after.is_complete
    absent = (
        tuple(entry for key, entry in before.offers.items() if key not in after.offers)
        if trusted
        else ()
    )

    price_changed: list[PriceChange] = []
    seller_changed: list[PriceChange] = []
    for key, now in after.offers.items():
        was = before.offers.get(key)
        if was is None:
            continue
        if (was.price_text, was.currency_symbol_text) != (
            now.price_text,
            now.currency_symbol_text,
        ):
            price_changed.append(PriceChange(offer_id=key, before=was, after=now))
        if was.seller_href != now.seller_href:
            seller_changed.append(PriceChange(offer_id=key, before=was, after=now))

    return MarketDiff(
        appeared=appeared,
        absent=absent,
        price_changed=tuple(price_changed),
        seller_changed=tuple(seller_changed),
        absences_trusted=trusted,
    )
