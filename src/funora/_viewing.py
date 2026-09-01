"""Что покупатель смотрит прямо сейчас.

РАСКОЛ НАБЛЮДЕНИЯ, И ОН ЗДЕСЬ ГЛАВНОЕ. Подписка на этот объект наблюдена НАМИ -
она лежит в семи наших записях канала, и состав её известен: признак,
идентификатор из восьми цифр, метка из восьми знаков.

Ответ на неё мы не видели НИ РАЗУ. Что приходит внутри, известно от независимой
реализации того же протокола: при пустом признаке покупатель не смотрит ничего,
иначе внутри лежит разметка со ссылкой на лот.

Отсюда устройство записи: разметка сохраняется КАК ЕСТЬ, а ссылка и подпись
читаются из неё отдельными полями. Не разобралась - поля остаются
ненаблюдёнными, а разметка при вызывающем: по ней человек увидит то, чего не
увидел разбор.

ИМЯ ОБЪЕКТА ЗАПИСАНО ДОСЛОВНО и странно выглядит. Что оно означает - неизвестно,
и догадываться незачем: подписка собирается по имени, а не по смыслу.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from selectolax.parser import HTMLParser

from ._observed import Observed

__all__ = ["BuyerViewing", "parse_buyer_viewing", "VIEWING_OBJECT"]

#: Имя объекта подписки. Наблюдено НАМИ в семи записях канала.
#:
#: Записано дословно. Как оно добралось до записи, стоит помнить: прежний
#: образец протокольного знака его не пропускал, и сборщик записал вместо имени
#: мерку. Образец расширили дефисом ПО ЭТОЙ МЕРКЕ, а не наугад.
VIEWING_OBJECT: Final[str] = "c-p-u"


@dataclass(frozen=True, slots=True)
class BuyerViewing:
    """Что покупатель смотрит прямо сейчас.

    Attributes:
        buyer_id (str): Идентификатор покупателя, о котором спрашивали.
        viewing (bool): Смотрит ли он что-нибудь. ЛОЖЬ ЗДЕСЬ - НАБЛЮДЕНИЕ:
            площадка прислала пустой признак, то есть сказала «не смотрит».
            Неудачу чтения выражает отказ.
        lot_href (Observed[str]): Ссылка на лот, прочитанная из разметки.
        lot_text (Observed[str]): Подпись лота на локали интерфейса. Не
            разбирается.
        raw_html (Observed[str]): Разметка блока, КАК ЕСТЬ. Сохраняется затем,
            что ответа этой точки мы не наблюдали: не разберись наши поля - у
            вызывающего останется то, из чего он поймёт сам.
        observed_at (datetime): Момент получения ответа.
    """

    buyer_id: str
    viewing: bool
    lot_href: Observed[str]
    lot_text: Observed[str]
    raw_html: Observed[str]
    observed_at: datetime


def _from_markup(markup: str) -> tuple[Observed[str], Observed[str]]:
    """Читает ссылку и подпись из разметки блока просмотра.

    РАЗБОР ЗДЕСЬ САМЫЙ ПРОСТОЙ ИЗ ВОЗМОЖНЫХ - первая ссылка, - и это не лень.
    Разметки этой мы не видели; строить по чужому описанию точный селектор
    значило бы выдать чужое описание за наблюдение.

    Аргументы:
        markup (str): Разметка блока.

    Возвращает:
        tuple[Observed[str], Observed[str]]: Ссылка и подпись.
    """
    node = HTMLParser(markup).css_first("a[href]")
    if node is None:
        return (
            Observed.missing("viewing_link_absent"),
            Observed.missing("viewing_text_absent"),
        )

    raw_href = (node.attributes or {}).get("href")
    href = (raw_href or "").strip()
    text = (node.text() or "").strip()
    return (
        Observed.present(href) if href else Observed.empty(""),
        Observed.present(text) if text else Observed.empty(""),
    )


def parse_buyer_viewing(obj: Any, *, buyer_id: str, observed_at: datetime) -> BuyerViewing:
    """Разбирает объект «покупатель смотрит» из ответа канала.

    ПУСТОЙ ПРИЗНАК - НАБЛЮДЕНИЕ, А НЕ НЕУДАЧА. Площадка сказала «не смотрит», и
    показать это продавцу можно. Неудачу чтения выражает отказ, а не ложь в
    поле.

    Аргументы:
        obj (Any): Объект из ответа канала.
        buyer_id (str): Идентификатор покупателя, о котором спрашивали.
        observed_at (datetime): Момент получения.

    Возвращает:
        BuyerViewing: Что смотрит покупатель.
    """
    empty = BuyerViewing(
        buyer_id=buyer_id,
        viewing=False,
        lot_href=Observed.missing("not_viewing"),
        lot_text=Observed.missing("not_viewing"),
        raw_html=Observed.missing("not_viewing"),
        observed_at=observed_at,
    )

    if not isinstance(obj, dict):
        return empty

    data = obj.get("data")
    # Пустой признак означает «не смотрит ничего». Наблюдено это не нами -
    # известно от сторонней реализации, - и потому обращение осторожное: всё,
    # что не похоже на разметку, читается как «не смотрит», а не как поломка.
    if not isinstance(data, dict):
        return empty

    raw_html = data.get("html")
    if isinstance(raw_html, dict):
        raw_html = raw_html.get("desktop")
    if not isinstance(raw_html, str) or not raw_html.strip():
        return empty

    href, text = _from_markup(raw_html)
    return BuyerViewing(
        buyer_id=buyer_id,
        viewing=True,
        lot_href=href,
        lot_text=text,
        raw_html=Observed.present(raw_html),
        observed_at=observed_at,
    )
