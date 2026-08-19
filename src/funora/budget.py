"""Числа бюджета запросов.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/runtime/budget.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Числа помечены в спецификации провизорными: измерять настоящие пороги
площадки означало бы намеренно их превышать. Поэтому они подобраны
консервативно и будут уточняться наблюдением, а не подбором.

Расходуются отправленные запросы, включая повторы и переходы по
редиректам. Считать только логические операции нельзя: тогда шторм
повторов оказывается бесплатным ровно в тот момент, когда площадке
хуже всего.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "BucketLimits",
    "BUCKETS",
    "MAX_WAIT_MS",
    "COUNTS_RETRIES",
    "COUNTS_REDIRECTS",
    "MAX_REDIRECTS",
    "Scheduling",
    "SCHEDULING",
    "PROVISIONAL",
]


@dataclass(frozen=True, slots=True)
class BucketLimits:
    """Ёмкость и скорость пополнения одного ведра.

    Attributes:
        name (str): Имя ведра.
        capacity (int): Сколько запросов помещается всего.
        refill_per_second (float): Сколько восстанавливается за секунду.
        burst (int): Сколько можно потратить залпом.
    """

    name: str
    capacity: int
    refill_per_second: float
    burst: int


#: Вёдра бюджета. Вложены: запрос расходует сначала общее, потом ведро
#: аккаунта. Порядок нормативен, иначе при нескольких аккаунтах в одном
#: процессе общий предел обходится.
BUCKETS: Final[dict[str, BucketLimits]] = {
    "host": BucketLimits(
        name="host",
        capacity=60,
        refill_per_second=1.0,
        burst=10,
    ),
    "account": BucketLimits(
        name="account",
        capacity=30,
        refill_per_second=0.5,
        burst=5,
    ),
    "write": BucketLimits(
        name="write",
        capacity=60,
        refill_per_second=0.0167,
        burst=5,
    ),
}

#: Сколько ждать освобождения бюджета, прежде чем отказать.
MAX_WAIT_MS: Final[int] = 5000

#: Расходуют ли бюджет повторы.
COUNTS_RETRIES: Final[bool] = True

#: Расходуют ли бюджет переходы по редиректам.
COUNTS_REDIRECTS: Final[bool] = True

#: Предел числа переходов на один запрос.
MAX_REDIRECTS: Final[int] = 5


@dataclass(frozen=True, slots=True)
class Scheduling:
    """Числа расписания опроса.

    Attributes:
        active_interval_ms (int): Интервал при активном аккаунте.
        idle_step_multiplier (float): Во сколько раз растёт интервал в покое.
        max_interval_ms (int): Потолок интервала.
        activity_window_ms (int): Окно, в котором аккаунт считается активным.
        min_floor_ms (int): Нижний предел интервала.
    """

    active_interval_ms: int
    idle_step_multiplier: float
    max_interval_ms: int
    activity_window_ms: int
    min_floor_ms: int


#: Расписание опроса.
#:
#: Нижний предел интервала обычной настройкой не понижается. Это
#: единственное число, которое защищает площадку от слишком уверенного
#: пользователя, а аккаунт пользователя - от него самого.
SCHEDULING: Final[Scheduling] = Scheduling(
    active_interval_ms=3000,
    idle_step_multiplier=1.5,
    max_interval_ms=120000,
    activity_window_ms=300000,
    min_floor_ms=2000,
)

#: Признак того, что числа подобраны, а не измерены.
#:
#: Снимается только тогда, когда пороги станут известны из наблюдений.
#: Измерять их намеренным превышением нельзя.
PROVISIONAL: Final[bool] = True
