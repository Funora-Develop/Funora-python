r"""Числа бюджета запросов.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/runtime/budget.yaml в репозитории Funora-spec.
Перестроить: .venv\Scripts\python.exe tools/codegen.py

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
from enum import StrEnum
from typing import Final

__all__ = [
    "BucketLimits",
    "BUCKETS",
    "MAX_WAIT_MS",
    "WAIT_ATTEMPTS",
    "WAIT_GUARD_MS",
    "BURST_WINDOW_MS",
    "RequestClass",
    "ON_REFUSAL",
    "FLOOR_SHARE",
    "DEMAND_WINDOW_MS",
    "COUNTS_RETRIES",
    "COUNTS_REDIRECTS",
    "MIN_HEALTH_INTERVAL_MS",
    "OUTBOUND_MESSAGES_PER_HOUR",
    "OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR",
    "OUTBOUND_MIN_INTERVAL_PER_CHAT_MS",
    "OUTBOUND_WINDOW_MS",
    "OUTBOUND_WARMING_EVENTS",
    "COLD_OUTREACH_QUOTA_PER_HOUR",
    "COLD_OUTREACH_WINDOW_MS",
    "MAX_QUEUE_DEPTH_PER_KEY",
    "MAX_CONCURRENT_HANDLERS",
    "HANDLER_TIMEOUT_MS",
    "MAX_CONNECTIONS_PER_HOST",
    "MAX_RESPONSE_BYTES",
    "MAX_DECOMPRESSED_BYTES",
    "MAX_REDIRECTS",
    "RateLimitResponse",
    "RATE_LIMIT_RESPONSE",
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

#: Ограничитель исходящих сообщений. Не ведро токенов.
#:
#: Три предела из четырёх ведром невыразимы: множество различных
#: адресатов, пауза на отдельную переписку и условная квота. Ведро
#: отвечает «сколько ждать», а здесь ждать нельзя - пределы часовые
#: при пределе ожидания в пять секунд.
OUTBOUND_MESSAGES_PER_HOUR: Final[int] = 30
OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR: Final[int] = 15
OUTBOUND_MIN_INTERVAL_PER_CHAT_MS: Final[int] = 30000
OUTBOUND_WINDOW_MS: Final[int] = 3600000
COLD_OUTREACH_QUOTA_PER_HOUR: Final[int] = 3
COLD_OUTREACH_WINDOW_MS: Final[int] = 86400000

#: События, которые ГРЕЮТ переписку. Только входящие.
#:
#: Счётчик непрочитанного сюда не входит нарочно: он меняется и от
#: НАШЕЙ отправки, и ограничитель на нём отменял бы сам себя.
OUTBOUND_WARMING_EVENTS: Final[tuple[str, ...]] = ("message.created",)
MAX_WAIT_MS: Final[int] = 5000

#: Сколько попыток занять бюджет делается всего.
#:
#: Одна пауза и одна повторная попытка. Цикл ожидания превратил бы
#: предел ожидания в пожелание: каждая итерация ждала бы «не дольше
#: предела», а вызов снаружи стал бы неотличим от зависшего процесса.
WAIT_ATTEMPTS: Final[int] = 2

#: Сколько миллисекунд прибавляется к вычисленной паузе.
#:
#: Пауза округляется вверх и строго больше точной величины. Вровень
#: привело бы повторную попытку ровно на границу, где запаса ещё нет
#: из-за последнего бита деления, - и вызов отказал бы, прождав всё
#: положенное.
WAIT_GUARD_MS: Final[int] = 1

#: Окно, за которое считается право на залп.
#:
#: Ёмкость и залп ограничивают разное. Ёмкость - запас: она
#: копится в простое. Залп - темп: сколько можно отправить подряд,
#: не переводя дыхания, независимо от накопленного.
#:
#: Без второго предела клиент, простоявший минуту, выпускает
#: шестьдесят запросов в одну секунду - и первым от собственного
#: залпа страдает сам аккаунт.
BURST_WINDOW_MS: Final[int] = 1000


class RequestClass(StrEnum):
    """Класс запроса.

    Определяет, кого вытесняют при нехватке ёмкости. Проставляет его
    служба, а не пользователь: пользователь не знает, чем его вызов
    мешает соседнему.
    """

    INTERACTIVE = "interactive"
    AUTOMATION = "automation"
    POLL = "poll"
    MONITORING = "monitoring"


#: Что делать с запросом, которого ёмкость не пускает.
#:
#: "wait" - ждать пополнения, "refuse" - отказать немедленно.
#: Отказать можно только тому, кого спецификация объявила
#: отменяемым: ответ покупателю, не отправленный из-за собственного
#: мониторинга продавца, - худший исход, какой этот раздел даёт.
ON_REFUSAL: Final[dict[RequestClass, str]] = {
    RequestClass.INTERACTIVE: "wait",
    RequestClass.AUTOMATION: "wait",
    RequestClass.POLL: "wait",
    RequestClass.MONITORING: "refuse",
}

#: Гарантированная доля ёмкости для каждого класса.
FLOOR_SHARE: Final[dict[RequestClass, float]] = {
    RequestClass.INTERACTIVE: 0.3,
    RequestClass.AUTOMATION: 0.3,
    RequestClass.POLL: 0.25,
    RequestClass.MONITORING: 0.15,
}

#: Сколько класс считается претендующим после обращения.
#:
#: Порог складывается только из долей претендующих. Вытеснять
#: некого, когда никто не претендует, и запрещать циклу обновлений
#: брать больше своей доли на пустой площадке значило бы наказывать
#: его за чужое бездействие.
DEMAND_WINDOW_MS: Final[int] = 60000

#: Расходуют ли бюджет повторы.
COUNTS_RETRIES: Final[bool] = True

#: Расходуют ли бюджет переходы по редиректам.
COUNTS_REDIRECTS: Final[bool] = True

#: Предел числа переходов на один запрос.

#: Раньше какого срока проверка сессии возвращает кэш, миллисекунды.
MIN_HEALTH_INTERVAL_MS: Final[int] = 60000

#: Сколько событий помещается в очередь одного ключа упорядочивания, штук.
MAX_QUEUE_DEPTH_PER_KEY: Final[int] = 128

#: Сколько обработчиков выполняется одновременно, штук.
MAX_CONCURRENT_HANDLERS: Final[int] = 8

#: Сколько ждать обработчик, прежде чем счесть его зависшим, миллисекунды.
HANDLER_TIMEOUT_MS: Final[int] = 30000

#: Сколько соединений с одним хостом держать одновременно, штук.
MAX_CONNECTIONS_PER_HOST: Final[int] = 4

#: Предел размера полученного тела, байты.
MAX_RESPONSE_BYTES: Final[int] = 8388608

#: Предел размера тела после распаковки, байты.
MAX_DECOMPRESSED_BYTES: Final[int] = 33554432

#: Предел числа переходов при ручном следовании, штук.
MAX_REDIRECTS: Final[int] = 5


@dataclass(frozen=True, slots=True)
class RateLimitResponse:
    """Как источник отвечает на ограничение частоты.

    Восстановление медленнее падения намеренно. Симметричное
    восстановление даёт автоколебания: система отступает, тут же
    возвращается к прежней частоте, получает ограничение снова и так
    по кругу.

    Attributes:
        capacity_multiplier (float): На сколько умножается ёмкость.
        min_capacity_factor (float): Ниже этой доли ёмкость не падает.
        cooldown_ms (int): Остывание за первое ограничение, мс.
        window_ms (int): Окно учёта ограничений, мс.
        successes_per_step (int): Успехов подряд на шаг восстановления.
        recovery_multiplier (float): Во сколько раз растёт ёмкость.
    """

    capacity_multiplier: float
    min_capacity_factor: float
    cooldown_ms: int
    window_ms: int
    successes_per_step: int
    recovery_multiplier: float


#: Реакция на ограничение частоты.
#:
#: Раздел долго называл ступени именами и не давал ни одного числа:
#: реализация не могла его выполнить, даже захотев.
RATE_LIMIT_RESPONSE: Final[RateLimitResponse] = RateLimitResponse(
    capacity_multiplier=0.5,
    min_capacity_factor=0.125,
    cooldown_ms=60000,
    window_ms=900000,
    successes_per_step=20,
    recovery_multiplier=1.1,
)


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
