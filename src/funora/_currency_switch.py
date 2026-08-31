"""Смена валюты показа и разбор ответа площадки.

ЧТО ДЕЛАЕТ ЭТА ОПЕРАЦИЯ И ЧЕМ ОПАСНА. Она меняет валюту, в которой площадка
показывает суммы, - и меняет ГЛОБАЛЬНО: после неё каждая страница отдаёт другие
числа.

Дороже всего это для снимков рынка. Сравнение двух снимков, снятых по разные
стороны от смены, объявит сменившейся КАЖДУЮ цену - без единой ошибки, без строки
в журнале, без следа.

ДВЕ ВЕТКИ ОТВЕТА, И РАЗЛИЧАТЬ ИХ ОБЯЗАТЕЛЬНО. Либо валюта сменена сразу, либо
возвращается окно подтверждения - и тогда смены НЕ БЫЛО.

ПОДТВЕРЖДЕНИЕ НЕ ДАЁТСЯ ЗА ПОЛЬЗОВАТЕЛЯ. В запросе есть поле подтверждения, и
сюда всегда уходит отрицание. Вторая ветка отдаётся исходом, а не проглатывается:
решать, соглашаться ли, вправе только человек.

КУРС ИЗ ОКНА НЕ РАЗБИРАЕТСЯ. Сторонняя реализация достаёт его регулярным
выражением из абзаца окна - то есть из ТЕКСТА НА ЛОКАЛИ ИНТЕРФЕЙСА. Смена языка
аккаунта ломает такой разбор молча, а локаль привязана к аккаунту, а не к адресу.

Текст окна отдаётся как есть.

Наблюдено нами: переключатель в шапке, пункты которого несут код ISO 4217.
Известно от FunPayAPI (FunPayCardinal, account.py, get_exchange_rate): адрес,
имена полей запроса и ключи ответа.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from ._observed import Observed
from .errors import ProtocolChangedError

__all__ = ["CurrencySwitch", "parse_currency_switch", "SWITCH_CURRENCY_PATH", "NEVER_CONFIRMED"]

#: Адрес смены валюты. Известен от сторонней реализации.
SWITCH_CURRENCY_PATH: Final[str] = "/account/switchCurrency"

#: Что уходит в поле подтверждения. ВСЕГДА отрицание.
#:
#: Стоит константой, а не литералом в сборке запроса, чтобы согласие за
#: пользователя нельзя было дать случайной правкой одного знака. Сторонняя
#: реализация шлёт то же самое и никогда иного.
NEVER_CONFIRMED: Final[str] = "false"


@dataclass(frozen=True, slots=True)
class CurrencySwitch:
    """Исход смены валюты показа.

    Attributes:
        switched (bool): Сменила ли площадка валюту. Ложь при возвращённом окне
            означает, что смены не было.
        confirmation_required (bool): Вернула ли площадка окно подтверждения.
            Истина означает: смены НЕ БЫЛО, и площадка хочет согласия. Давать
            его вправе только человек.
        confirmation_text (Observed[str]): Текст окна, КАК ЕСТЬ. Не разбирается:
            внутри лежит курс обмена, и вывести его можно только из текста на
            локали интерфейса.
        requested (str): Код валюты, о котором просили.
        observed_at (datetime): Момент получения ответа.
    """

    switched: bool
    confirmation_required: bool
    confirmation_text: Observed[str]
    requested: str
    observed_at: datetime


def parse_currency_switch(payload: Any, *, requested: str, observed_at: datetime) -> CurrencySwitch:
    """Разбирает ответ площадки на смену валюты.

    ВЕТКА «СМЕНЕНО» УЗНАЁТСЯ ПОЛОЖИТЕЛЬНО: ключ адреса присутствует и пуст.
    Присутствие непустого адреса означает не успех, а что-то третье, о чём мы
    ничего не знаем, - и объявлять успехом его нельзя.

    Аргументы:
        payload (Any): Разобранное тело ответа.
        requested (str): Код валюты, о котором просили.
        observed_at (datetime): Момент получения.

    Возвращает:
        CurrencySwitch: Исход.

    Raises:
        ProtocolChangedError: Если ответ непригоден для чтения.
    """
    if not isinstance(payload, dict):
        raise ProtocolChangedError(f"ответ смены валюты не объект, а {type(payload).__name__}")

    raw_modal = payload.get("modal")
    if isinstance(raw_modal, str) and raw_modal.strip():
        # Окно есть - значит смены НЕ БЫЛО. Подтверждать за пользователя мы не
        # станем, и потому это конечный исход, а не промежуточный шаг.
        return CurrencySwitch(
            switched=False,
            confirmation_required=True,
            confirmation_text=Observed.present(raw_modal),
            requested=requested,
            observed_at=observed_at,
        )

    if "url" not in payload:
        raise ProtocolChangedError(
            "в ответе смены валюты нет ни окна подтверждения, ни ключа адреса. "
            "Сменилась валюта или нет - неизвестно, а угадывать здесь дорого: "
            "после смены каждая страница отдаёт другие числа"
        )

    raw_url = payload["url"]
    # Пустой адрес - положительный признак того, что делать больше нечего.
    # Непустой означает что-то третье; успехом его объявлять нельзя.
    if isinstance(raw_url, str) and raw_url.strip():
        raise ProtocolChangedError(
            f"ответ смены валюты несёт непустой адрес {raw_url!r}. Наблюдались "
            "две ветки - смена и окно подтверждения, - и эта не из них"
        )

    return CurrencySwitch(
        switched=True,
        confirmation_required=False,
        confirmation_text=Observed.missing("no_confirmation_window"),
        requested=requested,
        observed_at=observed_at,
    )
