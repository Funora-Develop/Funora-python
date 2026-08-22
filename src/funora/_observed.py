"""Значение вместе с обстоятельствами его наблюдения.

Тип существует ради одного различия, и это различие стоит денег продавца.
Значение ``None`` одинаково выглядит для «поле пустое» и «поля на странице не
было», а решения по этим случаям противоположные. Пустое поле - наблюдение:
описание заказа действительно пустое, перечитывать нечего. Отсутствующее поле -
подозрение на изменённую разметку, и перечитать стоит.

Склеив их в ``None``, мы получили бы худший вид отказа: правдоподобный ответ, о
неверности которого узнать неоткуда.

Отсюда три правила, каждое из которых неудобно нарочно.

Чтение ``.value`` у ненаблюдённого значения бросает исключение, а не возвращает
``None``. Подставить умолчание может только вызывающий: лишь он знает, чем
грозит его задаче отсутствие именно этого поля.

Приведение к булеву значению запрещено. Три состояния не сводятся к двум без
потери ровно того различия, ради которого тип введён, а запись ``if entry.status``
выглядит настолько естественно, что молча проглотила бы его.

Строковое представление у ненаблюдённого значения показывает причину, а не
пустоту. Значение уходит в журналы и в сообщения, и «» там неотличимо от честно
пустой строки.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, NoReturn, TypeVar

from .errors import UnobservedFieldError

__all__ = ["Confidence", "Presence", "Observed"]

#: Тип наблюдаемого значения.
T = TypeVar("T")

#: Тип значения, подставляемого вызывающим.
D = TypeVar("D")


class Confidence(StrEnum):
    """Уверенность правила извлечения.

    Словарь взят из spec/extraction/rules.yaml. Уровня ``assumed`` здесь нет
    намеренно: спецификация запрещает предположения в контракте, потому что
    предположение в правиле извлечения даёт молчаливый отказ.
    """

    OBSERVED = "observed"
    INFERRED = "inferred"


class Presence(StrEnum):
    """Что случилось с полем при разборе.

    Значения различают три исхода, а не два. Разница между EMPTY и NOT_OBSERVED
    определяет, имеет ли смысл перечитывать страницу.
    """

    #: Место значения найдено, значение непустое.
    PRESENT = "present"

    #: Место значения найдено, значение пустое. Это наблюдение, а не пробел.
    EMPTY = "empty"

    #: Места значения не нашлось. Читать нечего.
    NOT_OBSERVED = "not_observed"


@dataclass(frozen=True, slots=True)
class Observed(Generic[T]):
    """Значение поля вместе с обстоятельствами его наблюдения.

    Собирается тремя именованными конструкторами, а не напрямую: прямой вызов
    позволил бы собрать несогласованную запись, например наблюдённое значение
    без уверенности.

    Attributes:
        presence (Presence): Что случилось с полем при разборе.
        confidence (Confidence | None): Уверенность правила извлечения. None,
            если поле не наблюдалось: уверенность в отсутствии не определена.
        reason (str | None): Машиночитаемая причина отсутствия. Заполнена только
            при NOT_OBSERVED.
    """

    presence: Presence
    confidence: Confidence | None
    reason: str | None
    _value: T | None

    @classmethod
    def present(cls, value: T, confidence: Confidence = Confidence.OBSERVED) -> Observed[T]:
        """Собирает наблюдённое непустое значение.

        Непустоту обещает сам тип, и до сих пор обещание держалось на честном
        слове: конструктор принимал что угодно. Собранное здесь пустое значение
        отбирает у вызывающего единственный способ отличить «поле есть, и оно
        пусто» от «поле есть», - а различать он должен по состоянию, а не
        заглядывая внутрь.

        Пустота проверяется только у строк и последовательностей. У логического
        поля False - полноценное значение, а не пустота, и у числового ноль
        тоже: там пустоты не бывает.

        Args:
            value (T): Значение.
            confidence (Confidence): Уверенность правила извлечения.

        Returns:
            Observed[T]: Запись в состоянии PRESENT.

        Raises:
            ValueError: Если строка или последовательность пуста. Это не
                состояние площадки, а ошибка в разборе: для пустого значения
                есть empty().
        """
        if isinstance(value, str | tuple | list | frozenset | set | dict) and not value:
            raise ValueError(
                "Observed.present получил пустое значение; "
                "для пустого наблюдения есть Observed.empty"
            )
        return cls(Presence.PRESENT, confidence, None, value)

    @classmethod
    def empty(cls, value: T, confidence: Confidence = Confidence.OBSERVED) -> Observed[T]:
        """Собирает наблюдённое пустое значение.

        Args:
            value (T): Пустое значение своего типа, например пустая строка.
            confidence (Confidence): Уверенность правила извлечения.

        Returns:
            Observed[T]: Запись в состоянии EMPTY.
        """
        return cls(Presence.EMPTY, confidence, None, value)

    @classmethod
    def missing(cls, reason: str) -> Observed[T]:
        """Собирает запись о том, что места значения на странице не нашлось.

        Args:
            reason (str): Машиночитаемая причина, например ``selector_no_match``
                или ``status_mapping_not_observed``. Уходит в журнал и в текст
                исключения, поэтому обязана быть понятна без контекста.

        Returns:
            Observed[T]: Запись в состоянии NOT_OBSERVED.
        """
        return cls(Presence.NOT_OBSERVED, None, reason, None)

    @property
    def is_observed(self) -> bool:
        """Сообщает, нашлось ли место значения на странице.

        Returns:
            bool: True при PRESENT и EMPTY, False при NOT_OBSERVED.
        """
        return self.presence is not Presence.NOT_OBSERVED

    @property
    def value(self) -> T:
        """Возвращает наблюдённое значение.

        Returns:
            T: Значение. При EMPTY возвращается пустое значение своего типа:
            место найдено, и это тоже наблюдение.

        Raises:
            UnobservedFieldError: Если поле не наблюдалось. Вернуть здесь None
                значило бы стереть разницу между «пусто» и «не было», ради
                которой тип и заведён. Подставить умолчание может только
                вызывающий, для этого есть get и or_none.
        """
        if self.presence is Presence.NOT_OBSERVED:
            raise UnobservedFieldError(
                f"поле не наблюдалось на странице (причина: {self.reason}). "
                "Подставьте значение через get(...) либо получите None через "
                "or_none(), если отсутствие поля для вашей задачи допустимо"
            )
        return self._value  # type: ignore[return-value]

    def get(self, default: D) -> T | D:
        """Возвращает значение либо подставленное вызывающим.

        Подстановка умолчания и есть явное признание того, что поле могло не
        наблюдаться.

        Args:
            default (D): Что вернуть, если поле не наблюдалось.

        Returns:
            T | D: Значение либо default.
        """
        if self.presence is Presence.NOT_OBSERVED:
            return default
        return self._value  # type: ignore[return-value]

    def or_none(self) -> T | None:
        """Возвращает значение либо None, если поле не наблюдалось.

        Returns:
            T | None: Значение либо None.
        """
        return self._value

    def __bool__(self) -> NoReturn:
        """Запрещает приведение к булеву значению.

        Три состояния не сводятся к двум без потери того различия, ради которого
        тип введён. Запись ``if entry.description`` выглядит настолько
        естественно, что проглотила бы его молча.

        Raises:
            TypeError: Всегда.
        """
        raise TypeError(
            "Observed нельзя привести к булеву значению: состояний три, а не два. "
            "Спросите is_observed, если нужен факт наблюдения, либо сравните "
            "presence с нужным значением"
        )

    def __str__(self) -> str:
        """Возвращает представление для человека.

        Отсутствие показывается словами, а не пустотой: значение уходит в
        журналы и сообщения, где пустая строка неотличима от честно пустого
        поля.

        Returns:
            str: Значение, пустая строка либо пометка об отсутствии с причиной.
        """
        if self.presence is Presence.NOT_OBSERVED:
            return f"<не наблюдалось: {self.reason}>"
        return str(self._value)
