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

from .budget import (
    BUCKETS,
    BURST_WINDOW_MS,
    DEMAND_WINDOW_MS,
    FLOOR_SHARE,
    MAX_WAIT_MS,
    ON_REFUSAL,
    WAIT_GUARD_MS,
    BucketLimits,
    RequestClass,
)
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

    #: Право на залп: сколько ещё можно отправить, не переводя дыхания.
    #:
    #: Второй предел, независимый от запаса. Ведро, полное до краёв, всё равно
    #: не выпустит больше burst запросов подряд: запас копится в простое, а
    #: право на залп восстанавливается равномерно, burst единиц за окно.
    allowance: float = 0.0

    def __post_init__(self) -> None:
        """Заполняет ведро, если начальный запас не задан.

        Returns:
            None
        """
        if self.tokens < 0:
            self.tokens = float(self.limits.capacity)
        if self.allowance == 0.0:
            self.allowance = float(self.limits.burst)

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
        self.allowance = min(
            float(self.limits.burst),
            self.allowance + elapsed * self.limits.burst / (BURST_WINDOW_MS / 1000),
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

    def wait_for(self, now: float, cost: float = 1.0, floor: float = 0.0) -> int:
        """Сообщает, сколько ждать до появления нужного запаса.

        Порог ``floor`` - это доля ёмкости, которую запрос обязан оставить
        после себя. Он и есть правило допуска по классу: чем менее защищён
        класс, тем больше он обязан оставить, и тем раньше он уступает.

        Пауза округляется вверх и строго больше точной величины: к целой части
        прибавляется WAIT_GUARD_MS. Вровень привело бы повторную попытку ровно
        на границу, где запаса ещё нет из-за последнего бита деления, - и вызов
        отказал бы, прождав всё положенное. Величина объявлена спецификацией, а
        не выбрана здесь: трасса меток отправки сравнивается между реализациями,
        и округление обязано совпадать.

        Args:
            now (float): Текущий момент, монотонные секунды.
            cost (float): Сколько нужно занять.
            floor (float): Доля ёмкости, которая обязана остаться после займа.

        Returns:
            int: Миллисекунды ожидания. Ноль, если занять можно прямо сейчас.
        """
        self._refill(now)
        needed = cost + self.limits.capacity * self.factor * floor

        # Ждать приходится дольшего из двух пределов: запрос проходит, только
        # когда хватает и запаса, и права на залп.
        by_tokens = 0
        if self.tokens < needed:
            if self.limits.refill_per_second <= 0:
                return MAX_WAIT_MS
            by_tokens = (
                int(((needed - self.tokens) / self.limits.refill_per_second) * 1000) + WAIT_GUARD_MS
            )

        by_burst = 0
        if self.allowance < cost:
            per_second = self.limits.burst / (BURST_WINDOW_MS / 1000)
            by_burst = int(((cost - self.allowance) / per_second) * 1000) + WAIT_GUARD_MS

        return max(by_tokens, by_burst)

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
        self.allowance -= cost


class Budget:
    """Вложенные вёдра бюджета для одной сетевой идентичности.

    Args:
        names (tuple[str, ...]): Имена вёдер в порядке расхода. Порядок
            нормативен: сначала общее, потом ведро аккаунта.
    """

    __slots__ = ("_buckets", "_demanded_at", "_suspended_until")

    def __init__(self, names: tuple[str, ...] = ("host", "account")) -> None:
        self._buckets = tuple(TokenBucket(BUCKETS[name]) for name in names)
        #: Когда каждый класс последний раз просил бюджет.
        #:
        #: Порог складывается только из долей претендующих. Без этого доля
        #: превратилась бы из пола в потолок: цикл обновлений на пустой
        #: площадке уступал бы тем, кто не пришёл.
        self._demanded_at: dict[RequestClass, float] = {}

        #: До какого момента класс снят с очереди.
        #:
        #: Вторая ступень реакции на ограничение частоты. Снятие держится до
        #: конца остывания идентичности.
        self._suspended_until: dict[RequestClass, float] = {}

    def suspend(self, classes: tuple[RequestClass, ...], *, until: float) -> None:
        """Снимает классы запросов с очереди до названного момента.

        Вторая ступень реакции на ограничение частоты. Площадка сказала
        «слишком быстро» второй раз в окне, и урезания ёмкости оказалось мало:
        снимаются те классы, без которых клиент остаётся клиентом - наблюдение
        за рынком и автоматика.

        Снятие держится до конца остывания идентичности и снимается вместе с
        ним. Само по себе оно не истекает раньше: истекающее раньше означало бы,
        что клиент вернулся к прежнему темпу, ничего не дождавшись.

        Args:
            classes (tuple[RequestClass, ...]): Какие классы снять.
            until (float): До какого момента, монотонные секунды.

        Returns:
            None
        """
        for request_class in classes:
            self._suspended_until[request_class] = max(
                self._suspended_until.get(request_class, 0.0), until
            )

    def is_suspended(self, request_class: RequestClass, now: float) -> bool:
        """Сообщает, снят ли класс с очереди сейчас.

        Args:
            request_class (RequestClass): Класс запроса.
            now (float): Текущий момент, монотонные секунды.

        Returns:
            bool: True, если класс снят и запрос по нему сейчас не пройдёт.
        """
        return now < self._suspended_until.get(request_class, 0.0)

    def _floor_for(self, request_class: RequestClass, now: float) -> float:
        """Считает порог допуска для класса по нынешнему спросу.

        Порог - сумма долей тех классов, которые защищены сильнее И вправду
        претендуют на ёмкость. Претендующим считается класс, обращавшийся за
        бюджетом в последние DEMAND_WINDOW_MS.

        Условие про спрос принципиально. Доля - это пол, а не потолок: она
        обещает, что менее защищённый не съест последнюю долю более
        защищённого. Обещание имеет смысл, только когда более защищённому есть
        что съесть. Вытеснять некого, когда никто не претендует, и запрещать
        циклу обновлений брать больше четверти ведра на пустой площадке значило
        бы наказывать его за чужое бездействие.

        Args:
            request_class (RequestClass): Класс, для которого считается порог.
            now (float): Текущий момент, монотонные секунды.

        Returns:
            float: Доля ёмкости, которая обязана остаться после займа.
        """
        deadline = now - DEMAND_WINDOW_MS / 1000
        floor = 0.0
        for other in RequestClass:
            if other is request_class:
                break
            if self._demanded_at.get(other, float("-inf")) >= deadline:
                floor += FLOOR_SHARE[other]
        return floor

    def reserve(
        self,
        now: float,
        cost: float = 1.0,
        request_class: RequestClass = RequestClass.INTERACTIVE,
    ) -> Reservation:
        """Пытается занять бюджет во всех вёдрах сразу.

        Занимает либо во всех, либо ни в одном. Частичный расход означал бы, что
        отказавший запрос всё равно потратил чужой запас, и при частых отказах
        бюджет утекал бы в никуда.

        Класс запроса задаёт порог: сколько ёмкости обязано остаться после
        займа. Доля - это ПОЛ, а не потолок; она не ограничивает класс сверху, а
        обещает, что менее защищённый не съест последнюю долю более защищённого.
        Прежде класс объявлялся у каждой операции и до бюджета не доходил вовсе:
        собственный мониторинг продавца вытеснял ответы покупателям на общих
        основаниях - ровно то, ради чего доли и придуманы.

        Умолчание - interactive. Не потому, что оно безобидно, а потому, что
        оно самое защищённое: вызов, забывший объявить класс, не должен из-за
        забывчивости уступить мониторингу.

        Args:
            now (float): Текущий момент, монотонные секунды.
            cost (float): Стоимость запроса.
            request_class (RequestClass): Класс запроса.

        Returns:
            Reservation: Выдан ли бюджет, и сколько ждать, если нет.
        """
        self._demanded_at[request_class] = now

        # Снятый класс не проходит вовсе, сколько бы ни было в ведре. Ждать он
        # обязан до конца остывания, а не до появления токена.
        #
        # Округление то же, что и у ожидания запаса: пауза строго больше точной
        # величины. Здесь константа прежде стояла литералом - то есть правило
        # выполнялось по совпадению, и правка объявленного числа обошла бы это
        # место стороной.
        if self.is_suspended(request_class, now):
            return Reservation(
                granted=False,
                wait_ms=int((self._suspended_until[request_class] - now) * 1000) + WAIT_GUARD_MS,
                bucket="suspended",
            )

        floor = self._floor_for(request_class, now)
        for bucket in self._buckets:
            wait = bucket.wait_for(now, cost, floor)
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

    def require(
        self,
        now: float,
        cost: float = 1.0,
        request_class: RequestClass = RequestClass.INTERACTIVE,
    ) -> Reservation:
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
        reservation = self.reserve(now, cost, request_class)
        if reservation.granted:
            return reservation

        # Отменяемому классу отказывают сразу, не дожидаясь предела ожидания.
        # Ждать наблюдению бессмысленно: к моменту пополнения оно устареет, а
        # место в очереди займёт прямо сейчас. Прочим отказать нельзя - их
        # никто не повторит за пользователя.
        if ON_REFUSAL[request_class] == "refuse":
            raise BudgetExhaustedError(
                f"бюджет исчерпан для класса {request_class}: ведро "
                f"{reservation.bucket} освободится через {reservation.wait_ms} мс. "
                "Класс объявлен отменяемым и уступает всем прочим. Запрос не отправлен"
            )

        if reservation.wait_ms <= MAX_WAIT_MS:
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
