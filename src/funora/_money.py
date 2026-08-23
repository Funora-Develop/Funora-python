"""Денежная сумма как доменный тип.

Тип объявлен спецификацией давно и не был построен ни разу: цена заказа
отдавалась текстом, а арифметики не существовало вовсе. Вызывающий складывал
цены своим кодом - в котором нет ни проверки валют, ни защиты от плавающей
точки, ради которых тип и объявлен.

Чего здесь НЕТ и почему. Сборки money из наблюдённого текста - «1500 ₽» - тут
не будет: она требует вывести код валюты из символа, а символ в снимках
замаскирован и не наблюдался ни разу. Придуманная таблица символов молча
приписала бы чужую валюту чужому заказу, и заметил бы это не разработчик, а
продавец. Записано в spec/conformance/not-implemented.yaml.

Значит сумму собирает вызывающий - из того, что прочитал сам, - а этот тип даёт
ему то, чего у него не было: безопасную арифметику.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from .errors import CurrencyMismatchError, ValidationError

__all__ = ["Money"]

#: Код валюты по ISO 4217: ровно три заглавные латинские буквы.
_CURRENCY: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class Money:
    """Денежная сумма в минорных единицах.

    Целое число, а не дробное. Дробное представление не воспроизводится
    одинаково в разных языках: 0.1 + 0.2 даёт 0.30000000000000004 всюду, где
    считают двоичной дробью, и сумма двух заказов расходится с той, что видит
    площадка.

    Attributes:
        amount_minor (int): Сумма в минорных единицах. Знак допустим: возвраты
            и корректировки бывают отрицательными.
        currency (str): Код валюты по ISO 4217, три заглавные латинские буквы.
        scale (int): Сколько минорных единиц в мажорной, степенью десяти.
            Двойка означает копейки: 123410 при scale 2 это 1234.10.
    """

    amount_minor: int
    currency: str
    scale: int = 2

    def __post_init__(self) -> None:
        """Проверяет, что сумма выразима.

        Returns:
            None

        Raises:
            ValidationError: Если код валюты не по ISO 4217, разрядность
                отрицательна либо сумма не целая.
        """
        if not _CURRENCY.match(self.currency):
            raise ValidationError(
                f"код валюты {self.currency!r} не по ISO 4217: нужны ровно три "
                "заглавные латинские буквы. Символ валюты кодом не является - "
                "один и тот же знак носят несколько валют"
            )
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise ValidationError(
                f"сумма {self.amount_minor!r} не целая. Минорные единицы целы по "
                "определению, а дробное представление не воспроизводится одинаково "
                "в разных языках"
            )
        if self.scale < 0:
            raise ValidationError(f"разрядность {self.scale} отрицательна")

    def _same_kind(self, other: Money) -> None:
        """Проверяет, что суммы одного рода.

        Args:
            other (Money): Вторая сумма.

        Returns:
            None

        Raises:
            CurrencyMismatchError: Если валюты разные.
            ValidationError: Если валюта одна, а разрядность разная.
        """
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"арифметика между {self.currency} и {other.currency} запрещена. "
                "Курс - величина, меняющаяся ежеминутно и здесь неизвестная; "
                "сложить их значило бы выдать выдуманное число за сумму"
            )
        if self.scale != other.scale:
            raise ValidationError(
                f"одна валюта {self.currency} с разной разрядностью: {self.scale} и "
                f"{other.scale}. Складывать копейки с сотыми копейки нельзя - "
                "разойдётся на два порядка"
            )

    def __add__(self, other: Money) -> Money:
        """Складывает две суммы одной валюты.

        Args:
            other (Money): Вторая сумма.

        Returns:
            Money: Сумма.

        Raises:
            CurrencyMismatchError: Если валюты разные.
        """
        self._same_kind(other)
        return Money(self.amount_minor + other.amount_minor, self.currency, self.scale)

    def __sub__(self, other: Money) -> Money:
        """Вычитает сумму той же валюты.

        Args:
            other (Money): Вычитаемое.

        Returns:
            Money: Разность. Отрицательная разность допустима.

        Raises:
            CurrencyMismatchError: Если валюты разные.
        """
        self._same_kind(other)
        return Money(self.amount_minor - other.amount_minor, self.currency, self.scale)

    def __mul__(self, times: int) -> Money:
        """Умножает сумму на целое число.

        Умножение только на целое. Умножение на дробное - это доля, а доля
        требует правила округления, которого спецификация не задаёт: округлять
        вверх, вниз либо к ближайшему - три разных ответа на один вопрос, и
        выбрать за вызывающего нельзя.

        Args:
            times (int): Множитель, например число единиц товара.

        Returns:
            Money: Произведение.

        Raises:
            ValidationError: Если множитель не целый.
        """
        if isinstance(times, bool) or not isinstance(times, int):
            raise ValidationError(
                f"множитель {times!r} не целый. Дробный множитель - это доля, а "
                "доля требует правила округления, которого контракт не задаёт"
            )
        return Money(self.amount_minor * times, self.currency, self.scale)

    def __lt__(self, other: Money) -> bool:
        """Сравнивает суммы одной валюты.

        Args:
            other (Money): Вторая сумма.

        Returns:
            bool: True, если эта сумма меньше.

        Raises:
            CurrencyMismatchError: Если валюты разные. Сравнение здесь не
                безобиднее сложения: «дешевле» между валютами так же требует
                курса.
        """
        self._same_kind(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        """Сравнивает суммы одной валюты.

        Args:
            other (Money): Вторая сумма.

        Returns:
            bool: True, если эта сумма не больше.
        """
        self._same_kind(other)
        return self.amount_minor <= other.amount_minor

    def __str__(self) -> str:
        """Показывает сумму человеку.

        Returns:
            str: Мажорные единицы с разделителем и код валюты.
        """
        if self.scale == 0:
            return f"{self.amount_minor} {self.currency}"
        units, minor = divmod(abs(self.amount_minor), 10**self.scale)
        sign = "-" if self.amount_minor < 0 else ""
        return f"{sign}{units}.{minor:0{self.scale}d} {self.currency}"
