"""Поднятие предложений раздела и разбор ответа площадки.

ЧТО ЭТА ОПЕРАЦИЯ ДЕЛАЕТ. Поднимает В ВЫДАЧЕ ВСЕ предложения раздела сразу, а не
одно: запрос несёт игру и раздел, идентификатора предложения в нём нет. Кнопка
на странице так и называется - «Поднять предложения».

ЧЕМ ОНА ОПАСНА. Поднятие тратит суточный предел, восстановить который нельзя.
Повтор при неоднозначном исходе запрещён контрактом: вместо него положена сверка
фактического состояния.

ПЛОЩАДКА ОТВЕЧАЕТ ПРИЗНАКОМ ОТКАЗА, А НЕ УСПЕХА - поле error. Это важно: читать
надо его отрицание, и молчаливое «нет поля error, значит успех» было бы неверно.
Отсутствие поля означает не успех, а непонятный ответ.

Наблюдено 31.08.2026: POST /lots/raise, поля game_id и node_id, ответ
{error, msg, unlock_at, wait}.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from ._observed import Observed
from .errors import ProtocolChangedError

__all__ = ["RaiseResult", "parse_raise", "RAISE_PATH"]

#: Адрес поднятия. Наблюдён записью запроса.
RAISE_PATH: Final[str] = "/lots/raise"


@dataclass(frozen=True, slots=True)
class RaiseResult:
    """Исход поднятия предложений раздела.

    Attributes:
        raised (bool): Состоялось ли поднятие. Читается ОТРИЦАНИЕМ поля error.
        message (str): Сообщение площадки человеку, как есть. Не разбирается:
            это текст на локали интерфейса, и выводить из него причину значило
            бы строить разбор на переводе.
        unlock_at (Observed[str]): Момент следующего поднятия, как его назвала
            площадка. ТЕКСТОМ: формат не установлен, и разбирать его в момент
            времени значило бы гадать о часовом поясе.
        wait_seconds (Observed[int]): Сколько ждать, как назвала площадка.
            ЕДИНИЦА НЕ НАБЛЮДАЛАСЬ: поле называется wait и приходит целым, а
            секунды это или что-то ещё - неизвестно.
        observed_at (datetime): Момент получения ответа.
    """

    raised: bool
    message: str
    unlock_at: Observed[str]
    wait_seconds: Observed[int]
    observed_at: datetime


def parse_raise(payload: Any, *, observed_at: datetime) -> RaiseResult:
    """Разбирает ответ площадки на поднятие.

    ПРИЗНАК ОТКАЗА ОБЯЗАТЕЛЕН. Без него неизвестно, состоялось поднятие или нет,
    а повторить, чтобы выяснить, нельзя: повтор тратит невосполнимый предел.
    Поэтому ответ без error отвергается вслух, а не толкуется как успех.

    Аргументы:
        payload (Any): Разобранное тело ответа.
        observed_at (datetime): Момент получения.

    Возвращает:
        RaiseResult: Исход.

    Raises:
        ProtocolChangedError: Если в ответе нет признака отказа.
    """
    if not isinstance(payload, dict):
        raise ProtocolChangedError(
            f"ответ на поднятие не объект, а {type(payload).__name__}. Что "
            "случилось с предложениями - неизвестно, а повторить, чтобы "
            "выяснить, нельзя: повтор тратит суточный предел"
        )

    error = payload.get("error")
    if not isinstance(error, bool):
        raise ProtocolChangedError(
            "в ответе на поднятие нет признака отказа error. Площадка отвечает "
            "признаком ОТКАЗА, а не успеха, и без него исход неизвестен; "
            "считать отсутствие поля успехом нельзя - повтор здесь тратит "
            "невосполнимый предел"
        )

    raw_wait = payload.get("wait")
    # Логическое исключается отдельно: истина в Python - это единица, и
    # wait=True прочиталось бы как «ждать одну единицу».
    wait = (
        Observed.present(raw_wait)
        if isinstance(raw_wait, int) and not isinstance(raw_wait, bool)
        else Observed.missing("wait_not_in_response")
    )

    raw_unlock = payload.get("unlock_at")
    unlock = (
        Observed.present(raw_unlock)
        if isinstance(raw_unlock, str) and raw_unlock.strip()
        else Observed.missing("unlock_at_not_in_response")
    )

    raw_message = payload.get("msg")
    return RaiseResult(
        raised=not error,
        message=raw_message if isinstance(raw_message, str) else "",
        unlock_at=unlock,
        wait_seconds=wait,
        observed_at=observed_at,
    )
