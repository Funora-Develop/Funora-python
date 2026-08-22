"""Бюджет исходящих запросов.

Модуль не спит и не смотрит на часы: время передаётся снаружи. Иначе бюджет
пришлось бы проверять настоящими секундами, а проверка, идущая минуту, живёт
ровно до первого раза, когда она мешает.

Вёдра вложены, и порядок расхода нормативен: сначала общее ведро сетевой
идентичности, затем ведро аккаунта. Обратный порядок обходится тривиально -
десять аккаунтов в одном процессе уложились бы в свои личные пределы и вместе
превысили бы общий, а площадка видит именно общий: ей видна пара из исходящего
адреса и хоста, а не то, сколько логических аккаунтов мы завели у себя.

Расходуются отправленные запросы, а не логические операции. Повтор и переход по
редиректу - тоже запросы. Считать иначе означало бы сделать шторм повторов
бесплатным ровно в тот момент, когда площадке хуже всего.

Числа взяты из спецификации и помечены там провизорными. Измерять настоящие
пороги нельзя: измерение означало бы намеренное превышение.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from .budget import BUCKETS, MAX_WAIT_MS, BucketLimits
from .errors import BudgetExhaustedError

__all__ = ["TokenBucket", "Budget", "Reservation"]


@dataclass(frozen=True, slots=True)
class Reservation:
    """Результат попытки занять бюджет.

    Attributes:
        granted (bool): Выдан ли бюджет.
        wait_ms (int): Сколько ждать до следующей попытки. Ноль, если выдан.
        bucket (str): Имя ведра, которое отказало. Пустая строка, если выдан.
    """

    granted: bool
    wait_ms: int
    bucket: str


@dataclass
class TokenBucket:
    """Ведро с восполняемым запасом запросов.

    Args:
        limits (BucketLimits): Ёмкость и скорость пополнения.
        tokens (float): Текущий запас. По умолчанию ведро полное.
        updated_at (float): Момент последнего пополнения, монотонные секунды.
        factor (float): Доля от объявленной ёмкости. Меньше единицы после
            ограничения частоты: площадка сказала «слишком быстро», и ёмкость
            урезана до тех пор, пока не наберётся успешных запросов подряд.
    """

    limits: BucketLimits
    tokens: float = field(default=-1.0)
    updated_at: float = 0.0
    factor: float = 1.0

    def __post_init__(self) -> None:
        """Заполняет ведро, если начальный запас не задан.

        Returns:
            None
        """
        if self.tokens < 0:
            self.tokens = float(self.limits.capacity)

    def _refill(self, now: float) -> None:
        """Пополняет ведро по прошедшему времени.

        Args:
            now (float): Текущий момент, монотонные секунды.

        Returns:
            None
        """
        if now <= self.updated_at:
            # Монотонные часы назад не идут, но защита дешевле разбирательства:
            # отрицательный интервал молча выдал бы бесконечный бюджет.
            self.updated_at = now
            return
        elapsed = now - self.updated_at
        self.tokens = min(
            self.limits.capacity * self.factor,
            self.tokens + elapsed * self.limits.refill_per_second,
        )
        self.updated_at = now

    def scale(self, factor: float) -> None:
        """Урезает ёмкость ведра до доли от объявленной.

        Запас подрезается вместе с ёмкостью: ведро, полное по прежней мерке, при
        уменьшенной ёмкости отдало бы залпом больше, чем новая ёмкость
        позволяет, - то есть урезание не подействовало бы до первого исчерпания.

        Args:
            factor (float): Доля от объявленной ёмкости.

        Returns:
            None
        """
        self.factor = factor
        ceiling = self.limits.capacity * factor
        if self.tokens > ceiling:
            self.tokens = ceiling

    def wait_for(self, now: float, cost: float = 1.0) -> int:
        """Сообщает, сколько ждать до появления нужного запаса.

        Args:
            now (float): Текущий момент, монотонные секунды.
            cost (float): Сколько нужно занять.

        Returns:
            int: Миллисекунды ожидания. Ноль, если занять можно прямо сейчас.
        """
        self._refill(now)
        if self.tokens >= cost:
            return 0
        if self.limits.refill_per_second <= 0:
            return MAX_WAIT_MS
        return int(((cost - self.tokens) / self.limits.refill_per_second) * 1000) + 1

    def take(self, now: float, cost: float = 1.0) -> None:
        """Занимает запас без проверки.

        Проверять обязан вызывающий: разделение нужно затем, что при вложенных
        вёдрах занять надо либо во всех сразу, либо ни в одном.

        Args:
            now (float): Текущий момент, монотонные секунды.
            cost (float): Сколько занять.

        Returns:
            None
        """
        self._refill(now)
        self.tokens -= cost


class Budget:
    """Вложенные вёдра бюджета для одной сетевой идентичности.

    Args:
        names (tuple[str, ...]): Имена вёдер в порядке расхода. Порядок
            нормативен: сначала общее, потом ведро аккаунта.
    """

    __slots__ = ("_buckets",)

    def __init__(self, names: tuple[str, ...] = ("host", "account")) -> None:
        self._buckets = tuple(TokenBucket(BUCKETS[name]) for name in names)

    def reserve(self, now: float, cost: float = 1.0) -> Reservation:
        """Пытается занять бюджет во всех вёдрах сразу.

        Занимает либо во всех, либо ни в одном. Частичный расход означал бы, что
        отказавший запрос всё равно потратил чужой запас, и при частых отказах
        бюджет утекал бы в никуда.

        Args:
            now (float): Текущий момент, монотонные секунды.
            cost (float): Стоимость запроса.

        Returns:
            Reservation: Выдан ли бюджет, и сколько ждать, если нет.
        """
        for bucket in self._buckets:
            wait = bucket.wait_for(now, cost)
            if wait:
                return Reservation(granted=False, wait_ms=wait, bucket=bucket.limits.name)

        for bucket in self._buckets:
            bucket.take(now, cost)
        return Reservation(granted=True, wait_ms=0, bucket="")

    def scale(self, factor: float) -> None:
        """Урезает ёмкость всех вёдер до доли от объявленной.

        Нужно реакции на ограничение частоты. Прежде она была объявлена
        спецификацией и не выполнялась нигде: ответ 429 переводился в ошибку и
        уходил в политику повторов, но ёмкость ведра при этом не менялась -
        следующий залп был ровно таким же, каким был до ограничения. Это худший
        из возможных ответов на ограничение.

        Урезается ёмкость, а не запас. Запас восстановится сам по объявленной
        скорости; ёмкость решает, сколько можно взять залпом, и именно она
        отвечает на «слишком быстро».

        Args:
            factor (float): Доля от объявленной ёмкости, от нуля до единицы.

        Returns:
            None

        Raises:
            ValueError: Если доля вне разумных границ. Множитель больше единицы
                означал бы, что ограничение частоты РАЗРЕШАЕТ ходить чаще.
        """
        if not 0 < factor <= 1:
            raise ValueError(
                f"доля ёмкости {factor} вне границ (0, 1]: множитель больше "
                "единицы означал бы, что ограничение частоты разрешает ходить чаще"
            )
        for bucket in self._buckets:
            bucket.scale(factor)

    def require(self, now: float, cost: float = 1.0) -> Reservation:
        """Занимает бюджет или отказывает, если ждать пришлось бы слишком долго.

        Args:
            now (float): Текущий момент, монотонные секунды.
            cost (float): Стоимость запроса.

        Returns:
            Reservation: Всегда выданный либо с ожиданием не дольше предела.

        Raises:
            BudgetExhaustedError: Если ожидание превысило бы предел. Запрос при
                этом не отправляется вовсе - в этом весь смысл: ошибка означает
                решение SDK не ходить, а не ответ площадки.
        """
        reservation = self.reserve(now, cost)
        if reservation.granted or reservation.wait_ms <= MAX_WAIT_MS:
            return reservation
        raise BudgetExhaustedError(
            f"бюджет исчерпан: ведро {reservation.bucket} освободится через "
            f"{reservation.wait_ms} мс, предел ожидания {MAX_WAIT_MS} мс. "
            "Запрос не отправлен"
        )


#: Стоимость одного отправленного запроса.
#:
#: Расходуются именно отправленные запросы, включая повторы и переходы по
#: редиректам, а не логические операции.
REQUEST_COST: Final[float] = 1.0
