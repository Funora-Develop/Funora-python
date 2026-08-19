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

#: Признак того, что числа подобраны, а не измерены.
#:
#: Снимается только тогда, когда пороги станут известны из наблюдений.
#: Измерять их намеренным превышением нельзя.
PROVISIONAL: Final[bool] = True
