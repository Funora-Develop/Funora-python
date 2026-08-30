"""Проверки формы правки предложения.

СНИМОК ДОБЫТ ЧТЕНИЕМ. Ради него не было сделано ни одной записи на площадке, и
это стоит отдельного слова: прежде адрес сохранения и имена полей предлагалось
узнать, СОХРАНИВ чужой лот.

Открылись они починкой двух правил скелета:

  имя метода в адресе маскировалось как идентификатор - у браузерного сборщика
  исключение для него было с 24.08.2026, у питона не было;

  имена полей формы маскировались как всякое значение атрибута.

Здесь проверяется, что оба правила держатся, а значения полей по-прежнему
скрыты: в них цена, описание лота и сообщение покупателю.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from selectolax.parser import HTMLParser

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

FIXTURE: Final[str] = "lot-edit.logged.ru"

#: Имена полей, наблюдённые на снимке. Перечень закрытый: он и есть договор.
EXPECTED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "csrf_token",
        "form_created_at",
        "offer_id",
        "node_id",
        "location",
        "deleted",
        "price",
        "active",
        "deactivate_after_sale",
        "fields[summary][ru]",
        "fields[summary][en]",
        "fields[desc][ru]",
        "fields[desc][en]",
        "fields[payment_msg][ru]",
        "fields[payment_msg][en]",
        "fields[images]",
    }
)


def _form() -> object:
    """Возвращает узел формы правки.

    Возвращает:
        object: узел формы.
    """
    tree = HTMLParser((FIXTURES / f"{FIXTURE}.skeleton.txt").read_text(encoding="utf-8"))
    form = tree.css_first("form.form-offer-editor")
    assert form is not None, "форма правки не нашлась на снимке"
    return form


def test_the_save_endpoint_is_readable() -> None:
    """Требует, чтобы адрес сохранения читался ДОСЛОВНО.

    Ради этой строки предлагалось сохранить чужой лот. Она лежала в разметке и
    была скрыта правилом маскирования, которое приняло имя метода за
    идентификатор.

    Возвращает:
        None
    """
    action = (_form().attributes or {}).get("action")  # type: ignore[attr-defined]

    assert action == "https://funpay.com/lots/offerSave", (
        f"адрес сохранения прочитан как {action!r}. Замаскированный, он не "
        "годится ни для одной операции записи над лотами"
    )


def test_every_field_name_is_readable() -> None:
    """Требует, чтобы имена всех полей читались дословно.

    Имя выбирает площадка, а не человек: по нему собирается запрос, и без него
    снятая форма нечитаема как договор.

    Возвращает:
        None
    """
    names = {
        (one.attributes or {}).get("name")
        for one in _form().css("input, select, textarea")  # type: ignore[attr-defined]
    }
    names.discard(None)

    missing = sorted(EXPECTED_FIELDS - names)
    assert not missing, f"имена полей потеряны: {missing}"

    masked = sorted(one for one in names if one and one.startswith("T") and ":" in one)
    assert not masked, f"имена полей вышли подписями: {masked}. По такой форме запрос не собрать"


def test_no_field_value_is_readable() -> None:
    """Требует, чтобы ЗНАЧЕНИЯ полей остались скрытыми.

    Это вторая половина правила, и без неё первая недопустима. В значениях
    лежат цена, краткое и подробное описание лота и сообщение покупателю после
    оплаты - то есть текст, который писал человек.

    Возвращает:
        None
    """
    for one in _form().css("input"):  # type: ignore[attr-defined]
        value = (one.attributes or {}).get("value")
        if not value:
            continue
        assert value.startswith("T") and ":" in value, (
            f"значение поля {(one.attributes or {}).get('name')!r} сохранено дословно: {value!r}"
        )


def test_the_active_flag_carries_the_state_of_the_lot() -> None:
    """ГЛАВНАЯ НАХОДКА СНИМКА: признак включённости лота существует.

    Признака показа лота в выдаче в проекте не было ни одного, и модель Lot
    из-за этого не собиралась вовсе: поле объявлено обязательным, а взять его
    было неоткуда.

    Он есть здесь и читается НАЛИЧИЕМ пометки checked. На снимке она стоит.

    Возвращает:
        None
    """
    flags = [
        one
        for one in _form().css("input")  # type: ignore[attr-defined]
        if (one.attributes or {}).get("name") == "active"
    ]

    assert len(flags) == 1, f"флажков active на форме {len(flags)}"
    assert "checked" in (flags[0].attributes or {}), (
        "у флажка нет пометки checked: либо лот выключен, либо признак читается не наличием"
    )


def test_the_paired_hidden_field_is_only_on_the_other_flag() -> None:
    """Закрепляет РАЗЛИЧИЕ между двумя флажками формы.

    deactivate_after_sale объявлен дважды - скрытым и флажком, с одним именем.
    Это обычный приём: скрытое даёт значение при снятом флажке.

    У active такой пары НЕТ. Различие наблюдено, а не выведено, и что оно
    означает для запроса - не установлено: снятого флажка никто не видел.

    Возвращает:
        None
    """
    names = [
        (one.attributes or {}).get("name")
        for one in _form().css("input")  # type: ignore[attr-defined]
    ]

    assert names.count("deactivate_after_sale") == 2, (
        "пара скрытого и флажка распалась - приём перестал быть приёмом"
    )
    assert names.count("active") == 1, (
        "у active появилась пара: различие, записанное в контракте, исчезло, и "
        "запись надо переписать"
    )
