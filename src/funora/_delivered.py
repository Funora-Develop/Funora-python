"""Реестр выданного: что уже отправлено покупателю и по какому заказу.

ЗАЧЕМ ОТДЕЛЬНЫЙ РЕЕСТР, КОГДА ЕСТЬ КУРСОР ЗАКАЗОВ. Курсор защитой не является, и
это установлено, а не предположено. Три причины, каждая записана в самом коде:

Курсор снимается только с полного чтения, а события по прочитанным строкам
порождаются и при неполном. Заказ, выпавший из неполного чтения, в курсор не
попадёт и в следующий раз придёт как новый.

Обрыв тела на передаче по частям даёт правдоподобное «полно» с недостачей строк.
Выпавшие заказы уходят из курсора и возвращаются событием о создании.

Гашение повторов живёт по сроку и гасит по отпечатку события. «Этот заказ
выдан» обязано жить, пока жив заказ, а не пока не истёк срок.

ЗАПИСЬ ИДЁТ ВПЕРЕДИ ОТПРАВКИ. Тот же приём и тот же довод, что у реестра
отправок: «не засчитаем, раз не подтвердилось» означает не считать ровно те
выдачи, которые могли уйти. Повторная выдача необратима.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .errors import StateSchemaIncompatibleError

__all__ = ["Delivery", "DeliveryLedger", "QUEUED_OUTCOME"]

#: Исход, с которым запись о выдаче заводится.
#:
#: Означает «задание поставлено в очередь, чем кончилось - ещё не известно».
#: Запись, оставшаяся с ним после разбора очереди, - повод посмотреть заказ
#: глазами: см. :meth:`DeliveryLedger.unsettled`.
QUEUED_OUTCOME: Final[str] = "queued"


@dataclass(frozen=True, slots=True)
class Delivery:
    """Запись о выдаче.

    Attributes:
        order_id (str): Заказ, по которому выдано.
        offer_id (str): Предложение, которое сочли выданным. Пустая строка
            означает, что выдавал человек и предложение не устанавливалось.
        at_ms (int): Момент выдачи по стенным часам.
        outcome (str): Исход отправки, каким его вернул канал.
    """

    order_id: str
    offer_id: str
    at_ms: int
    outcome: str


class DeliveryLedger:
    """Что уже выдано.

    Реестр не забывает записи по сроку НАМЕРЕННО, в отличие от реестра
    отправок. Тот считает часовые пределы, и старая запись ему не нужна; этот
    отвечает на вопрос «выдавали ли по этому заказу», и ответ на него не
    устаревает никогда.

    Растёт он на запись за заказ. Продавец с сотней заказов в день накопит за
    год тридцать тысяч записей - несколько мегабайт, и это дешевле любой
    повторной выдачи.
    """

    __slots__ = ("_done",)

    def __init__(self) -> None:
        self._done: dict[str, Delivery] = {}

    def seen(self, order_id: str) -> bool:
        """Говорит, выдавали ли уже по этому заказу.

        Аргументы:
            order_id (str): Заказ.

        Возвращает:
            bool: True, если запись о выдаче есть.
        """
        return order_id in self._done

    def get(self, order_id: str) -> Delivery | None:
        """Возвращает запись о выдаче.

        Аргументы:
            order_id (str): Заказ.

        Возвращает:
            Delivery | None: Запись либо None.
        """
        return self._done.get(order_id)

    def record(self, delivery: Delivery) -> None:
        """Записывает выдачу.

        Перезаписи НЕТ: первая запись о заказе побеждает. Вторая означала бы,
        что мы выдали дважды, и затирать след первой выдачи значило бы прятать
        именно то, ради чего реестр заведён.

        Аргументы:
            delivery (Delivery): Запись о выдаче.

        Возвращает:
            None
        """
        self._done.setdefault(delivery.order_id, delivery)

    def settle(self, order_id: str, outcome: str) -> None:
        """Проставляет записи настоящий исход отправки.

        ЗАПИСЬ ЗАВОДИТСЯ ИСХОДОМ ``queued`` И ПРЕЖДЕ ТАК И ОСТАВАЛАСЬ. Поле
        объявлялось «исход отправки, каким его вернул канал», а канал к нему не
        притрагивался никто: успешная выдача и выдача, потерянная между записью
        и отправкой, лежали в реестре одинаковыми.

        Отсюда и был вред: найти потерянные было нечем. Проверка «выдавали ли»
        на исход не смотрит и смотреть не должна - запись о заказе означает
        «больше не выдавать», и это верно при любом исходе. Но человеку,
        который разбирается, чем кончился день, нужно уметь их различать.

        Записи нет - ничего не происходит: settle не заводит записей. Завести
        её здесь значило бы объявить выданным заказ, по которому решения не
        принимали.

        Аргументы:
            order_id (str): Заказ.
            outcome (str): Исход, каким его вернул канал, либо имя отказа.

        Возвращает:
            None
        """
        existing = self._done.get(order_id)
        if existing is None:
            return
        self._done[order_id] = Delivery(
            order_id=existing.order_id,
            offer_id=existing.offer_id,
            at_ms=existing.at_ms,
            outcome=outcome,
        )

    def unsettled(self) -> tuple[str, ...]:
        """Перечисляет заказы, у которых исход так и остался ``queued``.

        ЭТО И ЕСТЬ СПИСОК ПОДОЗРИТЕЛЬНЫХ. Задание поставлено в очередь, а чем
        кончилась отправка, реестр не узнал: процесс мог умереть между записью и
        разбором очереди. Товар при этом покупателю мог не уйти, а повторно он
        не уйдёт уже никогда - запись о заказе стоит.

        Звать стоит при запуске: заказы отсюда - те, по которым стоит посмотреть
        переписку глазами.

        Возвращает:
            tuple[str, ...]: Заказы с незакрытым исходом, в порядке записи.
        """
        return tuple(
            order_id for order_id, one in self._done.items() if one.outcome == QUEUED_OUTCOME
        )

    def snapshot(self) -> dict[str, Any]:
        """Отдаёт состояние обычными значениями для файла состояния.

        Возвращает:
            dict[str, Any]: Состояние, пригодное для записи в файл.
        """
        return {
            "done": [
                {
                    "order_id": one.order_id,
                    "offer_id": one.offer_id,
                    "at_ms": one.at_ms,
                    "outcome": one.outcome,
                }
                for one in self._done.values()
            ]
        }

    def restore(self, payload: dict[str, Any]) -> None:
        """Восстанавливает реестр целиком после проверки всех записей.

        Повреждение даёт StateSchemaIncompatibleError и сохраняет прежний
        реестр. Пропущенная выдача разрешила бы выдать тот же заказ повторно.
        Отсутствующий раздел старого файла означает пустой список; отсутствующие
        offer_id и outcome остаются пустыми строками, сохраняя защиту по order_id.
        """
        if not isinstance(payload, dict):
            raise StateSchemaIncompatibleError("реестр выдач должен быть объектом")
        raw = payload.get("done", [])
        if not isinstance(raw, list):
            raise StateSchemaIncompatibleError("выдачи должны быть списком записей")

        restored: dict[str, Delivery] = {}
        for one in raw:
            if not isinstance(one, dict):
                raise StateSchemaIncompatibleError("непригодная запись выдачи")

            order_id = one.get("order_id")
            # Только строка и только непустая. Число, None и словарь дали бы
            # ключ вида 'None' либо "{'a': 1}" - запись о заказе, которого нет.
            if not isinstance(order_id, str) or not order_id.strip():
                raise StateSchemaIncompatibleError("непригодный идентификатор выданного заказа")

            at_ms = one.get("at_ms")
            # Логическое исключается отдельно: в Python истина - это единица, и
            # метка времени True прочиталась бы как первая миллисекунда эпохи.
            if isinstance(at_ms, bool) or not isinstance(at_ms, int):
                raise StateSchemaIncompatibleError("непригодная метка времени выдачи")

            offer_id = one.get("offer_id", "")
            outcome = one.get("outcome", "")
            if not isinstance(offer_id, str) or not isinstance(outcome, str):
                raise StateSchemaIncompatibleError("непригодное предложение или исход выдачи")
            if order_id in restored:
                raise StateSchemaIncompatibleError("повтор заказа в реестре выдач")
            restored[order_id] = Delivery(
                order_id=order_id,
                offer_id=offer_id,
                at_ms=at_ms,
                outcome=outcome,
            )

        self._done = restored

    def __len__(self) -> int:
        """Возвращает число записей.

        Возвращает:
            int: Сколько заказов уже выдано.
        """
        return len(self._done)
