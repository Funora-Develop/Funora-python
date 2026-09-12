"""Разбор формы правки предложения и сборка запроса сохранения.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ, а не пара строк в движке. Здесь единственное место в
проекте, где цена ошибки - ЧУЖОЙ ТЕКСТ. Форма несёт описание лота, сообщение
покупателю после оплаты и цену; отправить её, потеряв поле, значит стереть у
продавца то, что он писал руками, и узнать об этом он сможет только глазами.

Отсюда три правила, каждое записано в коде отдельно:

  поля собираются ВСЕ ПОДРЯД, без перечня допустимых - пропущенное поле уходит
  пустым, а перечень отстаёт от площадки молча;

  сохранение отправляет прочитанное КАК ЕСТЬ, меняя ровно то, что просили;

  флажок, снятый на странице, останавливает сохранение: что уходит при снятом
  флажке, никто не наблюдал, а разница между «не трогать лот» и «выключить лот»
  как раз в этом.

Наблюдено 30-31.08.2026: форма lot-edit.logged.ru и запрос network.lot-save-form.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Final

from selectolax.parser import HTMLParser, Node

from ._observed import Observed
from ._result import Completeness, Defect, Severity
from ._stock import parse_stock_text
from .errors import ProtocolChangedError
from .extraction import SELECTORS

__all__ = ["LotForm", "parse_lot_form", "SAVE_PATH", "ACTIVE_FIELD"]

#: Адрес сохранения. Наблюдён дословно в атрибуте action формы.
SAVE_PATH: Final[str] = "/lots/offerSave"

#: Имя флажка показа лота в выдаче. Наблюдено и в форме, и в записи запроса.
#:
#: Стоит константой, а не литералом, потому что читается и пишется в двух разных
#: местах модуля: разъехавшись, они дали бы разбор, который видит одно, и запрос,
#: который меняет другое.
ACTIVE_FIELD: Final[str] = "active"

#: Селектор формы правки. Берётся из порождённой таблицы, а не пишется
#: литералом: иначе спецификация и код разойдутся молча.
_FORM: Final[str] = SELECTORS["lot-edit.form"]

#: Знак валюты рядом с полем цены.
_CURRENCY: Final[str] = SELECTORS["lot-edit.fields.currency_symbol"]
_AMOUNT: Final[str] = SELECTORS["lot-edit.fields.amount"]

#: Поля, которые в отпечаток НЕ входят.
#:
#: Защитный токен и метка сборки формы меняются при каждом чтении страницы, и
#: отпечаток, считанный вместе с ними, не совпал бы сам с собой уже через
#: секунду. Отпечаток обязан меняться от правки ЛОТА, а не от перезагрузки.
_VOLATILE: Final[frozenset[str]] = frozenset({"csrf_token", "form_created_at"})


@dataclass(frozen=True, slots=True)
class LotForm:
    """Прочитанная форма правки предложения.

    Attributes:
        offer_id (str): Идентификатор предложения.
        node_id (str): Идентификатор раздела.
        price_text (str): Цена, как она стоит в поле.
        currency_symbol (Observed[str]): Знак валюты рядом с полем цены.
        is_active (bool): Показывается ли лот в выдаче. Читается НАЛИЧИЕМ
            пометки checked у флажка active.
        revision (str): Отпечаток состояния лота. Наш собственный, а не
            площадкин: площадка версии не даёт вовсе.
        fields (dict[str, str]): Все поля формы, кроме флажков, как есть.
        checked (frozenset[str]): Имена отмеченных флажков.
        observed_at (datetime): Момент чтения.
        completeness (Completeness): Полнота чтения.
        reason (str): Почему полнота такая.
        defects (tuple[Defect, ...]): Что не собралось.
        stock (Observed[int]): Наличие из единственного текстового поля amount.
            Без поля, при disabled или неизвестном формате значение не выводится.
    """

    offer_id: str
    node_id: str
    price_text: str
    currency_symbol: Observed[str]
    is_active: bool
    revision: str
    fields: dict[str, str]
    checked: frozenset[str]
    observed_at: datetime
    completeness: Completeness
    reason: str
    defects: tuple[Defect, ...] = field(default_factory=tuple)
    _checkbox_values: dict[str, str] = field(default_factory=dict, repr=False)
    stock: Observed[int] = field(default_factory=lambda: Observed.missing("stock_not_normalized"))

    def to_request(self, *, price: str | None = None, active: bool | None = None) -> dict[str, str]:
        """Собирает поля запроса сохранения.

        ОТПРАВЛЯЕТСЯ ПРОЧИТАННОЕ, а меняется ровно то, что просили. Собрать
        запрос из перечня нужных полей было бы короче и стёрло бы всё
        остальное: описание лота, сообщение покупателю, картинки.

        Отмеченный флажок уходит значением «on» - это наблюдено в записи
        запроса.

        СНЯТЫЙ ФЛАЖОК УХОДИТ ПУСТОЙ СТРОКОЙ, и вот это НАМИ НЕ НАБЛЮДАЛОСЬ.
        Оба наших снимка сохранения сняты с отмеченным флажком; вид запроса при
        снятом известен от независимой реализации того же протокола, которая
        шлёт поле всегда. Наше собственное рассуждение говорило обратное - что
        снятый флажок по устройству форм не уходит вовсе.

        Различие не косметическое: «поля нет» и «поле есть, но пустое» - разные
        запросы, и который из двух выключает лот, решается на стороне площадки.
        Поэтому обе операции видимости объявлены стоящими на вторичном источнике
        и спрашивают согласия.

        Аргументы:
            price (str | None): Новая цена либо None, чтобы оставить прежнюю.
            active (bool | None): Требуемое состояние показа либо None, чтобы
                оставить как прочитано.

        Возвращает:
            dict[str, str]: Поля запроса.
        """
        out = dict(self.fields)
        if price is not None:
            out["price"] = price

        checked = set(self.checked)
        if active is True:
            checked.add(ACTIVE_FIELD)
        elif active is False:
            checked.discard(ACTIVE_FIELD)
            # Пустая строка, а не отсутствие ключа. См. пояснение выше: это
            # единственное место всего пакета, где уходит непроверенное нами.
            out[ACTIVE_FIELD] = ""

        for name in sorted(checked):
            out[name] = self._checkbox_values.get(name, "on")
        return out


def _revision_of(
    fields: dict[str, str], checked: frozenset[str], checkbox_values: dict[str, str]
) -> str:
    """Считает отпечаток состояния лота.

    ОТПЕЧАТОК НАШ, а не площадкин, и это надо сказать вслух. Контракт требует
    предусловия expected_revision, чтобы параллельная правка не была перетёрта
    молча; площадка же версии лота не даёт нигде.

    Поэтому версией служит отпечаток самих значений: изменилось что угодно -
    изменился отпечаток. Это слабее настоящей версии в одном: правка, вернувшая
    прежние значения, отпечатка не меняет. И сильнее в другом: он замечает
    правку любого поля, а не только объявленных.

    Аргументы:
        fields (dict[str, str]): Поля формы.
        checked (frozenset[str]): Отмеченные флажки.
        checkbox_values (dict[str, str]): Значения флажков, включая снятые.

    Возвращает:
        str: Шестнадцать шестнадцатеричных знаков.
    """
    # Границы значений нельзя обозначать переводом строки или '=': оба знака
    # встречаются в полях. Скрытое значение и флажок одного имени раздельны:
    # при снятом флажке в запрос уходит именно скрытое значение.
    parts = [
        "lot-form-v2",
        [[name, value] for name, value in sorted(fields.items()) if name not in _VOLATILE],
        [[name, value] for name, value in sorted(checkbox_values.items())],
        sorted(checked),
    ]
    material = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return sha256(material.encode("ascii")).hexdigest()[:16]


def _text(node: Node | None) -> str:
    """Читает текст узла.

    Аргументы:
        node (Node | None): Узел либо None.

    Возвращает:
        str: Текст без краевых пробелов.
    """
    return (node.text() or "").strip() if node is not None else ""


def _stock(form: Node) -> Observed[int]:
    controls = form.css(_AMOUNT)
    if not controls:
        return parse_stock_text(None)
    if len(controls) != 1:
        return Observed.missing("stock_ambiguous")
    node = controls[0]
    attributes = node.attributes
    if "disabled" in attributes:
        return Observed.missing("stock_disabled")
    if node.tag != "input" or (attributes.get("type") or "text").lower() != "text":
        return Observed.missing("stock_control_unknown")
    return parse_stock_text(attributes.get("value") or "")


def parse_lot_form(html: str, *, observed_at: datetime) -> LotForm:
    """Разбирает страницу правки предложения.

    Аргументы:
        html (str): Разметка страницы.
        observed_at (datetime): Момент чтения.

    Возвращает:
        LotForm: Прочитанная форма.

    Raises:
        ProtocolChangedError: Если формы нет либо в ней нет обязательных полей.
    """
    form = HTMLParser(html).css_first(_FORM)
    if form is None:
        raise ProtocolChangedError(
            f"на странице правки нет формы {_FORM!r}. Собирать запрос сохранения "
            "не из чего, и догадываться о полях здесь нельзя: лишнее поле "
            "стирает описание лота"
        )

    fields: dict[str, str] = {}
    checked: set[str] = set()
    checkbox_values: dict[str, str] = {}
    defects: list[Defect] = []

    # Поля собираются ВСЕ ПОДРЯД. Перечень допустимых отстал бы от площадки
    # молча, а отставание здесь означает пустое поле в запросе - то есть
    # стёртый текст продавца.
    for node in form.css("input, textarea, select"):
        attributes = node.attributes or {}
        name = attributes.get("name")
        if not name:
            # Кнопки имени не имеют и в запрос не уходят - так наблюдено.
            continue

        if "disabled" in attributes:
            continue
        kind = (attributes.get("type") or "text").lower()
        if kind in {"submit", "button", "reset", "file"}:
            continue
        if kind == "checkbox":
            if name in checkbox_values:
                raise ProtocolChangedError(
                    f"несколько флажков {name!r} требуют нескольких значений"
                )
            checkbox_values[name] = (
                (attributes.get("value") or "") if "value" in attributes else "on"
            )
            if "checked" in attributes:
                checked.add(name)
            continue
        if kind == "radio":
            if "checked" not in attributes:
                continue
            value = attributes.get("value") if "value" in attributes else "on"
        elif node.tag == "textarea":
            value = node.text() or ""
        elif node.tag == "select":
            options = node.css("option")
            selected = [one for one in options if "selected" in one.attributes]
            if "multiple" in attributes or len(selected) > 1:
                raise ProtocolChangedError(
                    f"поле {name!r} требует нескольких значений: плоская форма их потеряет"
                )
            option = selected[0] if selected else (options[0] if options else None)
            if option is None:
                raise ProtocolChangedError(f"у поля {name!r} нет вариантов")
            value = (
                option.attributes.get("value") if "value" in option.attributes else option.text()
            )
        else:
            value = attributes.get("value") or ""
        if name in fields:
            raise ProtocolChangedError(
                f"повтор поля {name!r}: сохранение потеряет одно из значений"
            )
        fields[name] = value or ""

    for required in ("csrf_token", "offer_id", "node_id", "price", "form_created_at"):
        if required not in fields:
            defects.append(
                Defect(
                    severity=Severity.PAGE,
                    code="lot_form_field_missing",
                    detail=f"в форме правки нет поля {required!r}",
                    row_index=None,
                    field_name=required,
                )
            )

    if defects:
        raise ProtocolChangedError(
            f"форма правки неполна: {[one.field_name for one in defects]}. "
            "Отправлять её нельзя: недостающее поле уходит пустым, а пустое "
            "поле здесь стирает то, что продавец писал руками"
        )

    symbol = _text(form.css_first(_CURRENCY))
    return LotForm(
        offer_id=fields["offer_id"],
        node_id=fields["node_id"],
        price_text=fields["price"],
        currency_symbol=(
            Observed.present(symbol) if symbol else Observed.missing("selector_no_match:currency")
        ),
        # Единственный носитель признака во всём проекте, и читается он
        # НАЛИЧИЕМ пометки, а не значением.
        is_active=ACTIVE_FIELD in checked,
        revision=_revision_of(fields, frozenset(checked), checkbox_values),
        fields=fields,
        checked=frozenset(checked),
        observed_at=observed_at,
        completeness=Completeness.COMPLETE,
        reason="all_fields_parsed",
        defects=(),
        _checkbox_values=checkbox_values,
        stock=_stock(form),
    )
