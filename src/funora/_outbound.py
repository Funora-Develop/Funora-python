"""Ограничитель исходящих сообщений.

НЕ ВЕДРО ТОКЕНОВ, и это не вкусовщина. Три предела из четырёх ведром невыразимы:
множество РАЗЛИЧНЫХ адресатов, пауза на отдельную переписку и условная квота.
Ведро же отвечает на вопрос «сколько ждать», а здесь ждать нельзя вовсе: пределы
часовые при объявленном пределе ожидания в пять секунд.

УСТРОЙСТВО - ОДИН ЖУРНАЛ, а не четыре счётчика. Четыре предела - это четыре
запроса к одной записи. Четыре счётчика пришлось бы четырьмя способами обнулять,
сохранять и восстанавливать, и разошлись бы они молча.

ЗАЧЕМ ЭТО ВООБЩЕ. Отсутствие метода массовой рассылки не мешает написать цикл в
пять строк, а наказание по пункту 1.9 публичных правил площадки получает
продавец. Ограничитель - единственное, что стоит между тем и другим.

СЧИТАЕТСЯ ПОПЫТКА, А НЕ УСПЕХ, и запись делается ВПЕРЕДИ запроса. Иначе
неоднозначный исход не учитывался бы вовсе: форма отказа канала не наблюдалась, а
транспортный отказ объявлен способным иметь последствия. «Не засчитаем, раз не
подтвердилось» означало бы не считать ровно те отправки, которые могли уйти.

ДВЕ МЕТКИ У КАЖДОЙ ЗАПИСИ. Стенная - чтобы пережить перезапуск; монотонная -
чтобы перевод часов не сбрасывал квоту, пока процесс работает. После перезапуска
монотонной нет, и остаётся стенная: это честная половина, и она объявлена в
контракте прямо.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from .budget import (
    COLD_OUTREACH_QUOTA_PER_HOUR,
    COLD_OUTREACH_WINDOW_MS,
    OUTBOUND_MESSAGES_PER_HOUR,
    OUTBOUND_MIN_INTERVAL_PER_CHAT_MS,
    OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR,
    OUTBOUND_WARMING_EVENTS,
    OUTBOUND_WINDOW_MS,
)

__all__ = ["OutboundGovernor", "OutboundRefusal", "Sending"]

#: Имена пределов в объявленном порядке проверки.
#:
#: Порядок НОРМАТИВЕН. На решении «пропустить или отказать» он не сказывается -
#: отказ есть отказ, - а на имени упёршегося предела сказывается, и имя есть
#: часть ответа: по нему вызывающий решает, ждать полминуты или не писать до
#: завтра.
LIMIT_ORDER: Final[tuple[str, ...]] = (
    "cold_outreach_not_declared",
    "min_interval_per_chat",
    "cold_outreach_quota",
    "messages_per_hour",
    "unique_recipients_per_hour",
)


@dataclass(frozen=True, slots=True)
class Sending:
    """Одна попытка отправки.

    Attributes:
        chat_id (str): В какую переписку.
        wall_ms (int): Момент по стенным часам, миллисекунды от эпохи.
        monotonic_s (float | None): Показание монотонных часов. None у записи,
            восстановленной после перезапуска: у неё монотонной метки нет и быть
            не может - отсчёт монотонных часов свой в каждом запуске.
        cold (bool): Было ли обращение холодным.
    """

    chat_id: str
    wall_ms: int
    monotonic_s: float | None
    cold: bool


@dataclass(frozen=True, slots=True)
class OutboundRefusal:
    """Отказ ограничителя.

    Attributes:
        limit (str): Какой предел упёрся. Имя из объявленного перечня.
        retry_after_ms (int): Через сколько предел освободится. Ноль означает,
            что ожидание не поможет: исправлять надо вызов.
        detail (str): Пояснение для человека.
    """

    limit: str
    retry_after_ms: int
    detail: str


@dataclass(slots=True)
class OutboundGovernor:
    """Журнал исходящих и четыре предела над ним.

    Attributes:
        durable (bool): Есть ли долговечный реестр. Без него отправка отказывает:
            пределы часовые, а память обнуляется перезапуском, и тридцать
            сообщений в час превратились бы в тридцать НА ЗАПУСК.
    """

    durable: bool = True
    _sent: list[Sending] = field(default_factory=list, repr=False)
    _incoming: dict[str, int] = field(default_factory=dict, repr=False)

    def _age_ms(self, one: Sending, *, now_ms: int, now_s: float) -> int:
        """Возвращает возраст записи в миллисекундах.

        Считается по МОНОТОННЫМ часам, если они у записи есть. Перевод системных
        часов во время работы тогда квоту не трогает. После перезапуска
        монотонной метки нет, и остаётся стенная.

        Args:
            one (Sending): Запись.
            now_ms (int): Текущий момент по стенным часам.
            now_s (float): Текущее показание монотонных часов.

        Returns:
            int: Возраст в миллисекундах. Никогда не отрицателен: запись с
            меткой из будущего считается свежей, а не просроченной - часы,
            подведённые назад, не должны обнулять квоту.
        """
        if one.monotonic_s is not None:
            return max(0, int((now_s - one.monotonic_s) * 1000))
        return max(0, now_ms - one.wall_ms)

    def _within(self, window_ms: int, *, now_ms: int, now_s: float) -> list[Sending]:
        """Возвращает записи, попадающие в окно.

        Args:
            window_ms (int): Ширина окна.
            now_ms (int): Текущий момент по стенным часам.
            now_s (float): Текущее показание монотонных часов.

        Returns:
            list[Sending]: Записи внутри окна.
        """
        return [
            one for one in self._sent if self._age_ms(one, now_ms=now_ms, now_s=now_s) < window_ms
        ]

    def is_warm(self, chat_id: str, *, now_ms: int) -> bool:
        """Говорит, тёплая ли переписка.

        Тепло требует ПОЛОЖИТЕЛЬНОГО свидетельства - наблюдённого входящего
        сообщения в окне. Переписка считается холодной, пока не доказано
        обратное.

        Ошибка здесь стоит по-разному в две стороны: лишний отказ - неудобство,
        лишняя отправка - наказание продавцу.

        Args:
            chat_id (str): Переписка.
            now_ms (int): Текущий момент по стенным часам.

        Returns:
            bool: True, если входящее сообщение наблюдалось в окне.
        """
        seen = self._incoming.get(chat_id)
        return seen is not None and 0 <= now_ms - seen < COLD_OUTREACH_WINDOW_MS

    def note_event(self, event_type: str, chat_id: str, *, at_ms: int) -> bool:
        """Отмечает событие и говорит, согрело ли оно переписку.

        ГРЕЮЩИЕ ВИДЫ БЕРУТСЯ ИЗ КОНТРАКТА, а не пишутся здесь литералом. Перечень
        закрыт и состоит сегодня из одного вида - создания сообщения.

        Счётчик непрочитанного в него не входит нарочно: он меняется и от НАШЕЙ
        отправки, и ограничитель на нём отменял бы сам себя - первая отправка в
        холодную переписку делала бы её тёплой, и квота холодных не сработала бы
        ни разу.

        Args:
            event_type (str): Вид события.
            chat_id (str): Переписка.
            at_ms (int): Момент по стенным часам.

        Returns:
            bool: True, если событие греет переписку.
        """
        if event_type not in OUTBOUND_WARMING_EVENTS:
            return False
        self.note_incoming(chat_id, at_ms=at_ms)
        return True

    def note_incoming(self, chat_id: str, *, at_ms: int) -> None:
        """Отмечает ВХОДЯЩЕЕ сообщение, греющее переписку.

        Зовётся после того, как сторона сообщения уже установлена: греет только
        входящее. Своё сообщение греть не может - см. note_event.

        Args:
            chat_id (str): Переписка.
            at_ms (int): Момент по стенным часам.
        """
        known = self._incoming.get(chat_id)
        if known is None or at_ms > known:
            self._incoming[chat_id] = at_ms

    def check(
        self, chat_id: str, *, now_ms: int, now_s: float, declared_cold: bool = False
    ) -> OutboundRefusal | None:
        """Решает, можно ли отправить прямо сейчас.

        Пределы проверяются в ОБЪЯВЛЕННОМ порядке: от ошибки вызывающего к
        истории, и в истории - от узкого к широкому. Так названный предел
        оказывается самым близким к разрешению из упёршихся.

        Args:
            chat_id (str): Переписка.
            now_ms (int): Текущий момент по стенным часам.
            now_s (float): Показание монотонных часов. Приходит СНАРУЖИ, как и
                момент наблюдения у разбора: ограничитель часов не читает,
                иначе его нельзя ни проверить, ни повторить.
            declared_cold (bool): Объявил ли вызывающий, что пишет первым.

        Returns:
            OutboundRefusal | None: Отказ либо None, если можно.
        """
        if not self.durable:
            return OutboundRefusal(
                limit="no_durable_ledger",
                retry_after_ms=0,
                detail=(
                    "долговечного реестра отправок нет, и без него пределы обходятся "
                    "перезапуском процесса: часовая квота стала бы квотой на запуск"
                ),
            )

        cold = not self.is_warm(chat_id, now_ms=now_ms)

        # 1. Ошибка вызывающего. Она о самом вызове, а не об истории.
        if cold and not declared_cold:
            return OutboundRefusal(
                limit="cold_outreach_not_declared",
                retry_after_ms=0,
                detail=(
                    "по этой переписке не наблюдалось входящего сообщения в окне, "
                    "то есть обращение холодное. Холодное обращение требует явного "
                    "признака: ожидание тут не поможет, признак ставит вызывающий"
                ),
            )

        # 2. Пауза на отдельную переписку - самый узкий предел.
        same = [one for one in self._sent if one.chat_id == chat_id]
        if same:
            youngest = min(self._age_ms(one, now_ms=now_ms, now_s=now_s) for one in same)
            if youngest < OUTBOUND_MIN_INTERVAL_PER_CHAT_MS:
                return OutboundRefusal(
                    limit="min_interval_per_chat",
                    retry_after_ms=OUTBOUND_MIN_INTERVAL_PER_CHAT_MS - youngest,
                    detail=(
                        f"в эту переписку писали {youngest} мс назад при пределе "
                        f"{OUTBOUND_MIN_INTERVAL_PER_CHAT_MS} мс"
                    ),
                )

        # 3. Квота холодных обращений.
        if cold:
            chilly = [
                one
                for one in self._within(COLD_OUTREACH_WINDOW_MS, now_ms=now_ms, now_s=now_s)
                if one.cold
            ]
            if len(chilly) >= COLD_OUTREACH_QUOTA_PER_HOUR:
                return OutboundRefusal(
                    limit="cold_outreach_quota",
                    retry_after_ms=self._frees_in(
                        chilly, COLD_OUTREACH_WINDOW_MS, now_ms=now_ms, now_s=now_s
                    ),
                    detail=(
                        f"холодных обращений в окне {len(chilly)} при квоте "
                        f"{COLD_OUTREACH_QUOTA_PER_HOUR}"
                    ),
                )

        recent = self._within(OUTBOUND_WINDOW_MS, now_ms=now_ms, now_s=now_s)

        # 4. Число сообщений в час.
        if len(recent) >= OUTBOUND_MESSAGES_PER_HOUR:
            return OutboundRefusal(
                limit="messages_per_hour",
                retry_after_ms=self._frees_in(
                    recent, OUTBOUND_WINDOW_MS, now_ms=now_ms, now_s=now_s
                ),
                detail=f"сообщений в окне {len(recent)} при пределе {OUTBOUND_MESSAGES_PER_HOUR}",
            )

        # 5. Число различных адресатов в час.
        #
        # Предел не трогает переписку, которая в окне УЖЕ есть: писать тому, кому
        # писал, - не новый адресат.
        recipients = {one.chat_id for one in recent}
        if chat_id not in recipients and len(recipients) >= OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR:
            return OutboundRefusal(
                limit="unique_recipients_per_hour",
                retry_after_ms=self._frees_in(
                    recent, OUTBOUND_WINDOW_MS, now_ms=now_ms, now_s=now_s
                ),
                detail=(
                    f"различных адресатов в окне {len(recipients)} при пределе "
                    f"{OUTBOUND_UNIQUE_RECIPIENTS_PER_HOUR}"
                ),
            )

        return None

    def _frees_in(
        self, records: list[Sending], window_ms: int, *, now_ms: int, now_s: float
    ) -> int:
        """Считает, через сколько окно освободит одно место.

        Args:
            records (list[Sending]): Записи внутри окна.
            window_ms (int): Ширина окна.
            now_ms (int): Текущий момент по стенным часам.
            now_s (float): Текущее показание монотонных часов.

        Returns:
            int: Миллисекунды до выхода самой старой записи из окна.
        """
        if not records:
            return 0
        oldest = max(self._age_ms(one, now_ms=now_ms, now_s=now_s) for one in records)
        return max(0, window_ms - oldest)

    def record(self, chat_id: str, *, now_ms: int, now_s: float) -> None:
        """Записывает ПОПЫТКУ отправки.

        Зовётся ВПЕРЕДИ запроса, а не после ответа. Форма отказа канала не
        наблюдалась, и «не засчитаем, раз не подтвердилось» означало бы не
        считать ровно те отправки, которые могли уйти.

        Args:
            chat_id (str): Переписка.
            now_ms (int): Момент по стенным часам.
            now_s (float): Показание монотонных часов.
        """
        self._sent.append(
            Sending(
                chat_id=chat_id,
                wall_ms=now_ms,
                monotonic_s=now_s,
                cold=not self.is_warm(chat_id, now_ms=now_ms),
            )
        )

    def forget_expired(self, *, now_ms: int, now_s: float) -> None:
        """Выбрасывает записи, вышедшие из самого широкого окна.

        Реестр иначе растёт без предела: он переживает перезапуск, а значит
        живёт столько же, сколько аккаунт.

        Args:
            now_ms (int): Текущий момент по стенным часам.
            now_s (float): Показание монотонных часов.
        """
        widest = max(OUTBOUND_WINDOW_MS, COLD_OUTREACH_WINDOW_MS)
        self._sent = [
            one for one in self._sent if self._age_ms(one, now_ms=now_ms, now_s=now_s) < widest
        ]
        self._incoming = {
            chat: at
            for chat, at in self._incoming.items()
            if 0 <= now_ms - at < COLD_OUTREACH_WINDOW_MS
        }

    def snapshot(self) -> dict[str, Any]:
        """Отдаёт состояние обычными значениями для файла состояния.

        Монотонные метки НАРУЖУ НЕ УХОДЯТ. Отсчёт их свой в каждом запуске, и
        сохранённая монотонная метка после перезапуска означала бы не то, что
        значила при записи: запись либо не истекала бы никогда, либо реестр
        выбрасывался бы разом.

        Returns:
            dict[str, Any]: Состояние, пригодное для записи в файл.
        """
        return {
            "sent": [
                {"chat_id": one.chat_id, "at_ms": one.wall_ms, "cold": one.cold}
                for one in self._sent
            ],
            "incoming": dict(self._incoming),
        }

    def restore(self, payload: dict[str, Any]) -> None:
        """Восстанавливает состояние из файла.

        У восстановленных записей монотонной метки НЕТ - и не подставляется:
        подставленная означала бы, что запись сделана сейчас, и квота обнулялась
        бы перезапуском ровно так, как этого нельзя допустить.

        Args:
            payload (dict[str, Any]): Прочитанное из файла состояния.
        """
        self._sent = [
            Sending(
                chat_id=str(one["chat_id"]),
                wall_ms=int(one["at_ms"]),
                monotonic_s=None,
                cold=bool(one.get("cold", True)),
            )
            for one in payload.get("sent", [])
            if isinstance(one, dict) and "chat_id" in one and "at_ms" in one
        ]
        self._incoming = {
            str(chat): int(at)
            for chat, at in (payload.get("incoming") or {}).items()
            if isinstance(at, int)
        }
