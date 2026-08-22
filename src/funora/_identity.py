"""Сетевая идентичность: с какого адреса и через что идут запросы.

Спецификация привязывает бюджет не к клиенту и не к аккаунту, а к сетевой
идентичности - паре из исходящего адреса и целевого хоста. Именно её видит
площадка, и именно по ней применяет ограничения.

Отсюда всё устройство этого файла.

Прокси меняет исходящий адрес, то есть даёт ДРУГУЮ идентичность. У неё своё
ведро токенов, своё состояние остывания и свой счёт ограничений. Держать один
бюджет на все прокси значило бы считать чужие запросы своими, а держать по
бюджету на клиента - наоборот, не считать своих: два клиента одного процесса
через один прокси видны площадке как один источник.

Реестр общий на процесс намеренно. Изоляция возможна, но только явная: это
решение публичного конструктора, и спецификация фиксирует его прямо -
client_default: shared_runtime.

Реакция на ограничение живёт здесь же, а не в политике повторов. Политика
решает про ОДИН запрос: повторить ли его и когда. Ограничение частоты - про
источник целиком: после него медленнее должны идти все запросы этой
идентичности, а не только тот, который получил отказ.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from time import monotonic
from typing import Final

from ._budget import Budget
from .budget import RATE_LIMIT_RESPONSE, RequestClass

__all__ = ["Identity", "IdentityRegistry", "REGISTRY", "identity_of"]

_log = logging.getLogger("funora.identity")

#: Имя идентичности при прямом соединении, без прокси.
DIRECT: Final[str] = "direct"


def identity_of(proxy: str | None, host: str) -> str:
    """Собирает имя сетевой идентичности.

    Имя, а не сам адрес прокси: адрес может нести пароль, а имя уходит в
    журналы и в сообщения об ошибках. Поэтому берётся то, чем прокси назвали, а
    если не назвали - его положение в перечне.

    Args:
        proxy (str | None): Имя прокси либо None при прямом соединении.
        host (str): Целевой хост.

    Returns:
        str: Имя идентичности вида «имя@хост».
    """
    return f"{proxy or DIRECT}@{host}"


@dataclass
class Identity:
    """Одна сетевая идентичность и её состояние.

    Attributes:
        name (str): Имя вида «прокси@хост».
        budget (Budget): Вложенные вёдра токенов этой идентичности.
        capacity_factor (float): Во сколько раз ёмкость урезана относительно
            объявленной. Единица означает полную.
        cooldown_until (float): До какого момента идентичность не используется,
            монотонные секунды. Ноль означает, что она готова.
        limits_seen (int): Сколько ограничений получено в текущем окне.
        window_started_at (float): Начало окна учёта ограничений.
        successes (int): Сколько успешных запросов подряд после последнего
            ограничения. По ним идёт восстановление ёмкости.
    """

    name: str
    budget: Budget = field(default_factory=Budget)
    capacity_factor: float = 1.0
    cooldown_until: float = 0.0
    limits_seen: int = 0
    window_started_at: float = 0.0
    successes: int = 0

    def is_cooling(self, now: float) -> bool:
        """Сообщает, остывает ли идентичность сейчас.

        Args:
            now (float): Текущий момент, монотонные секунды.

        Returns:
            bool: True, если пользоваться ею пока нельзя.
        """
        return now < self.cooldown_until

    def note_limit(self, now: float) -> None:
        """Учитывает полученное ограничение частоты.

        Правило объявлено спецификацией и до сих пор не применялось нигде.
        Ответ 429 переводился в ошибку и уходил в политику повторов, но ёмкость
        ведра при этом не менялась: следующий залп был ровно таким же, каким был
        до ограничения. Это худший из возможных ответов на ограничение.

        Три ступени, каждая строже предыдущей: первое ограничение вдвое режет
        ёмкость и даёт остыть, второе останавливает наблюдение и автоматику,
        третье переводит идентичность в состояние ограниченной.

        Args:
            now (float): Текущий момент, монотонные секунды.

        Returns:
            None
        """
        window_ms = RATE_LIMIT_RESPONSE.window_ms
        if self.window_started_at == 0.0 or (now - self.window_started_at) * 1000 > window_ms:
            self.window_started_at = now
            self.limits_seen = 0

        self.limits_seen += 1
        self.successes = 0
        self.capacity_factor = max(
            RATE_LIMIT_RESPONSE.min_capacity_factor,
            self.capacity_factor * RATE_LIMIT_RESPONSE.capacity_multiplier,
        )
        cooldown_ms = RATE_LIMIT_RESPONSE.cooldown_ms * self.limits_seen
        self.cooldown_until = now + cooldown_ms / 1000
        self.budget.scale(self.capacity_factor)

        # Вторая ступень. Классы monitoring и automation снимаются с очереди до
        # конца остывания: первый отменяется, второй ждёт. Остаются interactive
        # и poll - то, без чего клиент перестаёт быть клиентом.
        #
        # Ступень выражается через классы запросов и потому была невыполнима,
        # пока классов не было: она так и стояла объявленной и не сделанной.
        if self.limits_seen >= 2:
            self.budget.suspend(
                (RequestClass.MONITORING, RequestClass.AUTOMATION),
                until=self.cooldown_until,
            )

        _log.warning(
            "идентичность %s получила ограничение (%d-е в окне): ёмкость урезана "
            "до %.2f от объявленной, остывание %d мс",
            self.name,
            self.limits_seen,
            self.capacity_factor,
            cooldown_ms,
        )

    def note_success(self) -> None:
        """Учитывает успешный запрос и понемногу возвращает ёмкость.

        Восстановление медленнее падения намеренно. Симметричное восстановление
        даёт автоколебания: система отступает, тут же возвращается к прежней
        частоте, получает ограничение снова и так по кругу.

        Returns:
            None
        """
        if self.capacity_factor >= 1.0:
            return

        self.successes += 1
        if self.successes < RATE_LIMIT_RESPONSE.successes_per_step:
            return

        self.successes = 0
        self.capacity_factor = min(
            1.0, self.capacity_factor * RATE_LIMIT_RESPONSE.recovery_multiplier
        )
        self.budget.scale(self.capacity_factor)
        _log.info(
            "идентичность %s восстанавливается: ёмкость %.2f от объявленной",
            self.name,
            self.capacity_factor,
        )


class IdentityRegistry:
    """Реестр сетевых идентичностей, общий на процесс.

    Общий намеренно. Два клиента одного процесса, ходящие через один и тот же
    адрес, видны площадке как один источник; изолированные бюджеты позволили бы
    им вдвоём превысить предел, который каждый по отдельности соблюдает.

    Реестр защищён замком: клиенты могут жить в разных потоках, а ведро токенов
    - это счётчик, который два потока способны уменьшить дважды из одного
    значения.
    """

    __slots__ = ("_by_name", "_lock")

    def __init__(self) -> None:
        self._by_name: dict[str, Identity] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> Identity:
        """Возвращает идентичность по имени, заводя её при первом обращении.

        Args:
            name (str): Имя идентичности.

        Returns:
            Identity: Существующая либо только что заведённая.
        """
        with self._lock:
            identity = self._by_name.get(name)
            if identity is None:
                identity = Identity(name=name)
                self._by_name[name] = identity
            return identity

    def names(self) -> tuple[str, ...]:
        """Перечисляет заведённые идентичности.

        Returns:
            tuple[str, ...]: Имена в порядке заведения.
        """
        with self._lock:
            return tuple(self._by_name)

    def reset(self) -> None:
        """Забывает все идентичности.

        Нужно проверкам: реестр общий на процесс, и состояние одной проверки
        протекло бы в следующую.

        Returns:
            None
        """
        with self._lock:
            self._by_name.clear()

    def healthy(self, names: tuple[str, ...], now: float | None = None) -> str | None:
        """Выбирает идентичность, которая сейчас не остывает.

        Порядок перечня уважается: первый неостывающий и берётся. Перебирать по
        кругу или случайно значило бы размазывать нагрузку ровным слоем по всем
        адресам - а вызывающий назвал их в том порядке, в каком хочет ими
        пользоваться.

        Args:
            names (tuple[str, ...]): Имена идентичностей в порядке предпочтения.
            now (float | None): Момент. По умолчанию текущий.

        Returns:
            str | None: Имя пригодной идентичности либо None, если остывают все.
        """
        moment = monotonic() if now is None else now
        for name in names:
            if not self.get(name).is_cooling(moment):
                return name
        return None


#: Реестр идентичностей, общий на процесс.
#:
#: Спецификация фиксирует это прямо: Client без явно переданного бюджета
#: присоединяется к общему для процесса, изоляция только явная.
REGISTRY: Final[IdentityRegistry] = IdentityRegistry()
