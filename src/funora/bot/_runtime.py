"""Рантайм бота: наблюдение и разбор очереди исходящих в одном потоке.

Здесь нет ни своего цикла, ни своей политики повторов, ни своего порядка шагов.
Всё это живёт в ядре и достаётся готовым: рантайм только подставляет разбор
очереди в паузу между опросами - через крючок on_idle у драйвера.

Одно правило, ради которого класс и написан: ПЛОЩАДКУ ТРОГАЕТ ОДИН ПОТОК.
Посторонний кладёт задание в очередь и ждёт квитанцию; отправляет тот же поток,
что ведёт наблюдение. Иначе ограничитель исходящих недосчитывает предел, и
узнаёте вы об этом от площадки.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .._client import Client
from .._poll import Schedule
from .._watch import Router
from ..errors import (
    ConfigurationError,
    FunoraError,
    HandlerError,
    UsageError,
    ValidationError,
)
from ._outbox import Outbox, SendCommand, SendTicket

if TYPE_CHECKING:
    from ._delivery import AutoDelivery, DeliveryDecision, DeliveryPlan

__all__ = ["Bot", "MAX_SENDS_PER_IDLE"]

_log = logging.getLogger("funora.bot")

#: Сколько отправок разбирать за одну паузу.
#:
#: Предел нужен, и он не про вежливость. Одна отправка - это чтение страницы
#: диалога плюс сама отправка, а при неоднозначном ответе ещё и сверка: три
#: чтения переписки с паузами 1, 3 и 8 секунд. Три задания подряд в худшем
#: случае растягивают паузу на минуту, и всё это время наблюдение стоит.
MAX_SENDS_PER_IDLE: Final[int] = 3


class Bot:
    """Наблюдение с очередью исходящих.

    Args:
        client (Client): Клиент. Рантайм становится его единственным
            пользователем: звать операции клиента из других потоков нельзя.
        router (Router): Реестр обработчиков событий.
        max_sends_per_idle (int): Сколько отправок разбирать за одну паузу.
    """

    __slots__ = ("_client", "_router", "_outbox", "_limit", "_sent", "_refused")

    def __init__(
        self,
        client: Client,
        router: Router,
        *,
        max_sends_per_idle: int = MAX_SENDS_PER_IDLE,
    ) -> None:
        # Ноль означал бы «не разбирать очередь никогда»: take(0) отдаёт
        # пустой перечень, задания копятся, квитанции не закрываются, и
        # положивший их ждёт вечно. Молча.
        if max_sends_per_idle < 1:
            raise ValidationError(
                f"предел отправок за паузу {max_sends_per_idle} не годится: при "
                "нём очередь не разбирается никогда, задания копятся, а "
                "положивший их ждёт закрытия квитанции, которого не будет"
            )

        self._client = client
        self._router = router
        self._outbox = Outbox()
        self._limit = max_sends_per_idle
        self._sent = 0
        self._refused = 0

    @property
    def outbox(self) -> Outbox:
        """Очередь исходящих.

        Returns:
            Outbox: Очередь, в которую можно класть из любого потока.
        """
        return self._outbox

    @property
    def sent(self) -> int:
        """Сколько заданий отправлено с начала работы.

        Returns:
            int: Число отправок, дошедших до площадки.
        """
        return self._sent

    @property
    def refused(self) -> int:
        """Сколько заданий отвергнуто отказом.

        Returns:
            int: Число заданий, на которых отправка не состоялась.
        """
        return self._refused

    def send(
        self,
        chat_id: str,
        text: str,
        *,
        idempotency_key: str,
        declared_cold: bool = False,
    ) -> SendTicket:
        """Просит отправить сообщение.

        Звать можно из любого потока - в этом весь смысл. Сама отправка
        произойдёт в потоке наблюдения, в ближайшей паузе.

        Args:
            chat_id (str): Числовой идентификатор диалога.
            text (str): Текст сообщения.
            idempotency_key (str): Ключ, по которому повтор узнаётся повтором.
            declared_cold (bool): Признание, что переписка холодная.

        Returns:
            SendTicket: Квитанция, по которой можно дождаться исхода.

        Raises:
            UsageError: Если очередь переполнена.
        """
        return self._outbox.put(
            SendCommand(
                chat_id=chat_id,
                text=text,
                idempotency_key=idempotency_key,
                declared_cold=declared_cold,
            )
        )

    def run(
        self,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
        max_threads_per_step: int = 5,
        on_handler_error: Callable[[HandlerError], None] | None = None,
    ) -> None:
        """Ведёт наблюдение и разбирает очередь исходящих.

        Метод блокирующий. Он и есть главный цикл бота.

        Args:
            account_id (str): Идентификатор аккаунта для отпечатков событий.
            max_iterations (int | None): Сколько шагов сделать.
            schedule (Schedule | None): Расписание опроса.
            state_path (Path | None): Файл состояния.
            max_threads_per_step (int): Сколько переписок дочитывать за шаг.
            on_handler_error (Callable[[HandlerError], None] | None): Что делать
                с отказом обработчика.

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        self._outbox.claim()
        self._client.run(
            self._client.engine.watch(
                self._router,
                account_id=account_id,
                max_iterations=max_iterations,
                schedule=schedule,
                state_path=state_path,
                max_threads_per_step=max_threads_per_step,
            ),
            router=self._router,
            on_handler_error=on_handler_error,
            on_idle=self._drain,
        )

    def _drain(self, pause_ms: int) -> None:
        """Разбирает очередь исходящих.

        Вызывается драйвером в паузе между опросами и потому исполняется в
        потоке наблюдения - том единственном, которому позволено трогать
        площадку.

        Отказ ОДНОГО задания не отменяет остальные и не роняет наблюдение: он
        уходит в квитанцию, и ждать его будет тот, кто задание положил. Уронить
        цикл из-за одного неудачного сообщения значило бы поставить наблюдение в
        зависимость от чужой кнопки.

        Args:
            pause_ms (int): Длительность паузы в миллисекундах. Не используется:
                предел ставится числом заданий, а не временем. Время предсказать
                нельзя - сверка отправки сама спит до двенадцати секунд.

        Returns:
            None
        """
        for ticket in self._outbox.take(self._limit):
            command = ticket.command
            try:
                result = self._client.chats.send_text(
                    command.chat_id,
                    command.text,
                    declared_cold=command.declared_cold,
                )
            except FunoraError as exc:
                self._refused += 1
                _log.warning(
                    "задание %s не отправлено: %s",
                    command.idempotency_key,
                    type(exc).__name__,
                )
                ticket.settle(error=exc)
                continue
            self._sent += 1
            ticket.settle(result=result)

    def deliveries(
        self,
        plan: DeliveryPlan,
        on_hold: Callable[[DeliveryDecision], None] | None = None,
    ) -> AutoDelivery:
        """Собирает автовыдачу, связанную с файлом состояния клиента.

        СОБИРАТЬ ЕЁ РУКАМИ НЕ НАДО, и это не вежливость. Реестр выданного
        обязан переживать перезапуск: обнулившись, он выдаст товар второй раз
        по заказу, который в списке продаж всё ещё оплачен. Связать его с
        файлом состояния можно только через движок, который этот файл открыл.

        Собранная руками автовыдача про файл не знает и знать не может, а
        выглядит рабочей: первый прогон отдаёт товар, второй отдаёт его снова.

        Args:
            plan (DeliveryPlan): Что и кому выдавать.
            on_hold (Callable[[DeliveryDecision], None] | None): Что делать с
                заказом, который сам не выдаётся.

        Returns:
            AutoDelivery: Автовыдача с долговечным реестром.

        Raises:
            ConfigurationError: Если у клиента нет файла состояния. Реестр в
                памяти здесь хуже отсутствия реестра: он выглядит защитой и ею
                не является.
        """
        from ._delivery import AutoDelivery

        engine = self._client.engine
        if engine._ledger is None:
            raise ConfigurationError(
                "автовыдача без файла состояния невозможна: реестр выданного "
                "обнулится при перезапуске, и товар уйдёт второй раз по заказу, "
                "который в списке продаж всё ещё оплачен. Передайте клиенту "
                "state_path"
            )

        return AutoDelivery(
            plan,
            engine.delivered,
            lambda chat, text, key: self.send(chat, text, idempotency_key=key),
            on_hold=on_hold,
            persist=engine.save_delivery,
        )

    def send_now(
        self,
        chat_id: str,
        text: str,
        *,
        declared_cold: bool = False,
    ) -> object:
        """Отправляет сообщение немедленно, минуя очередь.

        Звать МОЖНО ТОЛЬКО из потока наблюдения - из обработчика события. Вызов
        из чужого потока отвергается вслух.

        Отказ громкий, а не молчаливая гонка, потому что гонка здесь не роняет
        ничего и не бросает исключений: она портит счёт ограничителя исходящих.
        Проявится это превышением настоящего предела площадки, и объяснит вам
        это площадка.

        Args:
            chat_id (str): Числовой идентификатор диалога.
            text (str): Текст сообщения.
            declared_cold (bool): Признание, что переписка холодная.

        Returns:
            object: Квитанция отправки - SendResult.

        Raises:
            UsageError: Если вызов идёт не из потока наблюдения.
            FunoraError: Если отправка не состоялась.
        """
        if not self._outbox.is_owner():
            raise UsageError(
                "send_now зовут не из потока наблюдения. Клиент не защищён ни "
                "одной блокировкой: у ограничителя исходящих проверка и запись "
                "не атомарны, и второй поток недосчитывает предел - то есть "
                "превышает настоящий предел площадки. Кладите задание в очередь "
                "методом send: его разберёт тот же поток, что ведёт наблюдение"
            )
        return self._client.chats.send_text(chat_id, text, declared_cold=declared_cold)
