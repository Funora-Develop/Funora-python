"""Написание и снятие отзыва: адреса и разбор ответа.

ОДНА ТОЧКА НА ТРИ ДЕЙСТВИЯ. Написать отзыв, ПРАВИТЬ уже написанный и ответить на
чужой - всё это один адрес; различает их сама площадка по тому, кто автор и есть
ли оценка. Отдельного адреса у правки нет, и потому повтор не создаёт второго
отзыва, а переписывает тот же самый.

УСПЕХ УСТАНАВЛИВАЕТСЯ ПОЛОЖИТЕЛЬНЫМ ПРИЗНАКОМ, и это главное отличие нашего
разбора от стороннего.

Независимая реализация того же протокола объявляет успехом отсутствие отказа:
смотрит код ответа и в тело не заглядывает. Тело же несёт ПЕРЕРИСОВАННЫЙ виджет
отзыва - готовый положительный признак, который остаётся только прочитать.

Читаем мы его тем же разбором, что и отзыв на странице заказа: тот же класс
.review-container, тот же атрибут data-rating. Разбор наш и наблюдён нами;
чужое здесь - только то, что в ответе лежит именно он.

Наблюдено нами: атрибуты data-order и data-author на странице заказа.
Известно от FunPayAPI (FunPayCardinal, account.py): адреса, имена полей запроса
и ключи ответа.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from ._observed import Observed
from .errors import ProtocolChangedError

__all__ = [
    "ReviewResult",
    "parse_review_response",
    "REVIEW_PATH",
    "REVIEW_REMOVE_PATH",
    "RATING_MIN",
    "RATING_MAX",
]

#: Адрес написания отзыва. Известен от сторонней реализации.
REVIEW_PATH: Final[str] = "/orders/review"

#: Адрес снятия отзыва. Оттуда же.
REVIEW_REMOVE_PATH: Final[str] = "/orders/reviewDelete"

#: Наименьшая наблюдённая оценка.
RATING_MIN: Final[int] = 1

#: Наибольшая наблюдённая оценка.
#:
#: Границы стоят здесь, а не литералами в проверке: они наблюдение, и менять их
#: можно только новым наблюдением. Что площадка сделает с шестёркой либо с нулём,
#: никто не видел, и отправлять непроверенное мы не станем.
RATING_MAX: Final[int] = 5


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Исход написания либо снятия отзыва.

    Attributes:
        applied (bool): Удалось ли ПОДТВЕРДИТЬ исход положительным признаком.

            Ложь означает не «не получилось», а «подтвердить не удалось»:
            запрос ушёл, ответ пришёл, а виджет в нём не разобрался либо оценка
            в нём не та, что отправляли. Различие существенное - первое
            разрешает повторить, второе требует ПОСМОТРЕТЬ.
        message (str): Сообщение площадки человеку, как есть. Не разбирается:
            это текст на локали интерфейса.
        rating (Observed[int]): Оценка, прочитанная В ОТВЕТЕ, а не отправленная
            нами. Её сверка с отправленной и даёт applied. Ноль означает
            отсутствие отзыва - так же, как на странице заказа.
        observed_at (datetime): Момент получения ответа.
    """

    applied: bool
    message: str
    rating: Observed[int]
    observed_at: datetime


def parse_review_response(
    payload: Any, *, expected_rating: int | None, observed_at: datetime
) -> ReviewResult:
    """Разбирает ответ площадки на написание либо снятие отзыва.

    ОЖИДАЕМАЯ ОЦЕНКА - ЭТО ТО, ЧЕМ СВЕРЯЮТ, а не то, что подставляют. Придёт в
    ответе другая - applied станет ложью, и вызывающий узнает, что площадка
    сделала не то, о чём просили.

    Аргументы:
        payload (Any): Разобранное тело ответа.
        expected_rating (int | None): Отправленная оценка либо None при снятии,
            где ожидается отсутствие отзыва.
        observed_at (datetime): Момент получения.

    Возвращает:
        ReviewResult: Исход.

    Raises:
        ProtocolChangedError: Если ответ непригоден для чтения вовсе.
    """
    if not isinstance(payload, dict):
        raise ProtocolChangedError(
            f"ответ на отзыв не объект, а {type(payload).__name__}. Что случилось "
            "с отзывом - неизвестно"
        )

    raw_message = payload.get("msg")
    message = raw_message if isinstance(raw_message, str) else ""

    raw_content = payload.get("content")
    if not isinstance(raw_content, str) or not raw_content.strip():
        # Тела виджета нет - подтверждать нечем. Это НЕ отказ: запрос мог
        # состояться, и объявлять неудачу так же неверно, как объявлять успех.
        return ReviewResult(
            applied=False,
            message=message,
            rating=Observed.missing("review_widget_absent_from_response"),
            observed_at=observed_at,
        )

    # Разбор тот же, что у отзыва на странице заказа: те же селекторы, то же
    # чтение. Второй разбор для той же разметки разошёлся бы с первым молча.
    from ._order import parse_review_block

    rating, _author, _defects = parse_review_block(raw_content)

    expected = 0 if expected_rating is None else expected_rating
    applied = rating.or_none() == expected
    return ReviewResult(
        applied=applied,
        message=message,
        rating=rating,
        observed_at=observed_at,
    )
