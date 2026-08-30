"""Журнал правок цены: что стояло у лота до того, как его тронул бот.

ЗАЧЕМ. Контракт требует у lots.update_price аудита before_state, и требование
это единственное в своём роде: больше ни одной операции аудит не предписан.
Довод у него простой и невесёлый. Правку цены нельзя отменить обращением к
площадке - у неё нет ни истории цен, ни отката. Единственный способ вернуть как
было - знать, как было. Знать это обязаны МЫ.

ЗАПИСЬ ИДЁТ ВПЕРЕДИ СОХРАНЕНИЯ. Тот же приём и тот же довод, что у реестра
выданного: «запишем, когда подтвердится» означает не записать ровно те правки,
которые могли уйти. Процесс, упавший между отправкой и подтверждением, оставил
бы лот с новой ценой и без следа прежней.

ЖУРНАЛ ОГРАНИЧЕН, И ПРЕДЕЛ НАЗВАН ВСЛУХ. Реестр выданного растёт на запись за
заказ и не забывает ничего; здесь так нельзя. Переоценщик, ходящий раз в минуту,
за год напишет полмиллиона записей. Поэтому старые вытесняются - но счётчик
вытесненных хранится и отдаётся: молчаливое усечение читалось бы как «правок
было столько», а их было больше.

ПЕРВАЯ ЗАПИСЬ О ЛОТЕ НЕ ВЫТЕСНЯЕТСЯ. Она отвечает на вопрос «какая цена стояла
до того, как бот вмешался вообще», и ответ на него не устаревает - в отличие от
любой промежуточной.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "PriceChange",
    "PriceAudit",
    "DEFAULT_JOURNAL_LIMIT",
    "UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT",
]

#: Имя послабления и имя отметки в состоянии здоровья. Одно на оба места
#: нарочно: два имени одного послабления расходятся молча.
UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT: Final[str] = "unsafe_price_changes_without_audit"

#: Сколько правок журнал держит сверх первых записей о каждом лоте.
#:
#: Число выбрано не измерением, а прикидкой: пятьсот записей - это сотня
#: килобайт в файле состояния и заведомо больше, чем успевает набежать между
#: двумя взглядами человека на журнал.
DEFAULT_JOURNAL_LIMIT: Final[int] = 500


@dataclass(frozen=True, slots=True)
class PriceChange:
    """Запись о правке цены.

    Attributes:
        offer_id (str): Предложение, которому меняли цену.
        node_id (str): Раздел, в котором оно лежит.
        price_before (str): Цена, стоявшая в поле до правки.
        price_after (str): Цена, которую отправили.
        revision_before (str): Отпечаток лота до правки.
        at_ms (int): Момент записи по стенным часам. Момент ЗАПИСИ, а не
            подтверждения: запись идёт впереди сохранения, и подтверждения на
            этот момент ещё нет.
    """

    offer_id: str
    node_id: str
    price_before: str
    price_after: str
    revision_before: str
    at_ms: int


class PriceAudit:
    """Журнал правок цены.

    Args:
        limit (int): Сколько записей держать сверх первой записи о каждом лоте.
            Ноль и отрицательное означают «не вытеснять».
    """

    __slots__ = ("_first", "_journal", "_dropped", "_limit", "durable")

    def __init__(self, limit: int = DEFAULT_JOURNAL_LIMIT) -> None:
        #: Переживает ли журнал перезапуск. Ставится движком, когда файл
        #: состояния есть либо когда вызывающий назвал послабление вслух.
        #: Ложь означает отказ правки цены, а не молчаливую правку без следа.
        self.durable: bool = False
        #: Первая правка каждого лота. Не вытесняется никогда.
        self._first: dict[str, PriceChange] = {}
        #: Все правки по порядку, включая первые.
        self._journal: list[PriceChange] = []
        #: Сколько записей вытеснено пределом.
        self._dropped: int = 0
        self._limit = limit

    def record(self, change: PriceChange) -> None:
        """Записывает правку.

        Аргументы:
            change (PriceChange): Что и на что меняли.

        Возвращает:
            None
        """
        self._first.setdefault(change.offer_id, change)
        self._journal.append(change)
        if self._limit > 0:
            while len(self._journal) > self._limit:
                self._journal.pop(0)
                self._dropped += 1

    def original(self, offer_id: str) -> PriceChange | None:
        """Возвращает первую известную правку лота.

        Она и отвечает на вопрос «как было до бота»: промежуточные цены ставил
        тот же бот, а эта стояла до него.

        Аргументы:
            offer_id (str): Предложение.

        Возвращает:
            PriceChange | None: Первая запись либо None, если лот не трогали.
        """
        return self._first.get(offer_id)

    def history(self, offer_id: str | None = None) -> tuple[PriceChange, ...]:
        """Возвращает записи журнала по порядку.

        Аргументы:
            offer_id (str | None): Отобрать по предложению либо None - все.

        Возвращает:
            tuple[PriceChange, ...]: Записи от старой к новой.
        """
        if offer_id is None:
            return tuple(self._journal)
        return tuple(one for one in self._journal if one.offer_id == offer_id)

    @property
    def dropped(self) -> int:
        """Сколько записей вытеснено пределом.

        Возвращает:
            int: Число вытесненных. Ноль означает, что вытеснения не было.
        """
        return self._dropped

    def snapshot(self) -> dict[str, Any]:
        """Отдаёт состояние обычными значениями для файла состояния.

        Возвращает:
            dict[str, Any]: Состояние, пригодное для записи в файл.
        """
        return {
            "journal": [_flatten(one) for one in self._journal],
            "first": [_flatten(one) for one in self._first.values()],
            "dropped": self._dropped,
        }

    def restore(self, payload: dict[str, Any]) -> None:
        """Восстанавливает состояние из файла.

        Собирается в стороне и подставляется целиком: битая запись не рушит
        остальные и не оставляет журнал восстановленным наполовину. Довод тот
        же, что у реестра выданного, и цена ошибки та же - потерянный след
        правки, которую нечем отменить.

        Аргументы:
            payload (dict[str, Any]): Прочитанное из файла состояния.

        Возвращает:
            None
        """
        journal = _records(payload.get("journal"))
        first: dict[str, PriceChange] = {}
        for one in _records(payload.get("first")):
            first.setdefault(one.offer_id, one)
        # Записи журнала тоже кандидаты в первые: файл мог быть записан прежней
        # редакцией, у которой раздела first не было вовсе, и терять из-за
        # этого «как было до бота» нельзя.
        for one in journal:
            first.setdefault(one.offer_id, one)

        dropped = payload.get("dropped")
        # Логическое исключается отдельно: истина в Python - это единица, и
        # счётчик True прочитался бы как одна вытесненная запись.
        if isinstance(dropped, bool) or not isinstance(dropped, int) or dropped < 0:
            dropped = 0

        self._journal = journal
        self._first = first
        self._dropped = dropped

    def __len__(self) -> int:
        """Возвращает число записей в журнале.

        Возвращает:
            int: Сколько правок хранится. Вытесненные не считаются.
        """
        return len(self._journal)


def _flatten(change: PriceChange) -> dict[str, Any]:
    """Раскладывает запись обычными значениями.

    Аргументы:
        change (PriceChange): Запись.

    Возвращает:
        dict[str, Any]: Поля записи.
    """
    return {
        "offer_id": change.offer_id,
        "node_id": change.node_id,
        "price_before": change.price_before,
        "price_after": change.price_after,
        "revision_before": change.revision_before,
        "at_ms": change.at_ms,
    }


def _records(raw: Any) -> list[PriceChange]:
    """Собирает записи из прочитанного, пропуская непригодные.

    Аргументы:
        raw (Any): Прочитанное из файла.

    Возвращает:
        list[PriceChange]: Пригодные записи по порядку.
    """
    if not isinstance(raw, list):
        return []

    out: list[PriceChange] = []
    for one in raw:
        if not isinstance(one, dict):
            continue

        offer_id = one.get("offer_id")
        if not isinstance(offer_id, str) or not offer_id.strip():
            continue

        at_ms = one.get("at_ms")
        if isinstance(at_ms, bool) or not isinstance(at_ms, int):
            continue

        out.append(
            PriceChange(
                offer_id=offer_id,
                node_id=_text(one.get("node_id")),
                price_before=_text(one.get("price_before")),
                price_after=_text(one.get("price_after")),
                revision_before=_text(one.get("revision_before")),
                at_ms=at_ms,
            )
        )
    return out


def _text(value: Any) -> str:
    """Возвращает строку либо пустую строку.

    Приведения к строке НЕТ нарочно: число, обращённое в текст, выглядело бы
    прочитанной ценой, а прочитано оно не было.

    Аргументы:
        value (Any): Прочитанное значение.

    Возвращает:
        str: Значение, если это строка, иначе пустая строка.
    """
    return value if isinstance(value, str) else ""
