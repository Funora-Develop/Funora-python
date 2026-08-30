"""Проверки правки цены лота.

ВТОРАЯ ОПЕРАЦИЯ ЗАПИСИ В ПРОЕКТЕ, и у неё своя цена ошибки. У отправки
сообщения лишний запрос стоит второго сообщения покупателю. Здесь - стёртого
описания лота: форма несёт подробное описание, сообщение покупателю после
оплаты и цену, и отправить её, потеряв поле, значит стереть то, что продавец
писал руками.

Отсюда главная проверка набора: ОТПРАВЛЯЕТСЯ ПРОЧИТАННОЕ. Меняется ровно одно
поле, всё прочее уходит тем же значением, каким пришло.

Наблюдено 30-31.08.2026: форма lot-edit.logged.ru и запрос network.lot-save-form.
Сети здесь нет - ядро сопрограмма, и проверка отвечает на его просьбы сама.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit
from funora._lot_form import SAVE_PATH, parse_lot_form
from funora._transport import Observation, TransportSettings
from funora.errors import (
    PreconditionFailedError,
    UnexpectedResponseError,
    UsageError,
    ValidationError,
)

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

FIXTURE: Final[str] = "lot-edit.logged.ru"

NODE: Final[str] = "1908"
OFFER: Final[str] = "75289502"

WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)


def _page(*, active: bool = True, price: str | None = None) -> str:
    """Читает снимок формы, при надобности меняя состояние.

    Аргументы:
        active (bool): оставить ли флажок показа отмеченным.
        price (str | None): подставить ли другую цену.

    Возвращает:
        str: разметка страницы правки.
    """
    html = (FIXTURES / f"{FIXTURE}.skeleton.txt").read_text(encoding="utf-8")
    if not active:
        html = html.replace('checked name="active"', 'name="active"', 1)
        assert 'checked name="active"' not in html, "флажок не снялся"
    if price is not None:
        form = parse_lot_form(html, observed_at=WHEN)
        html = html.replace(
            f'name="price" type="text" value="{form.price_text}"',
            f'name="price" type="text" value="{price}"',
            1,
        )
    return html


def _observation(html: str, *, url: str) -> Observation:
    """Собирает наблюдение, каким его отдаёт транспорт.

    Аргументы:
        html (str): тело ответа.
        url (str): конечный адрес.

    Возвращает:
        Observation: наблюдение.
    """
    body = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=url,
        html=html,
        elapsed_ms=10,
        redirects=1,
        content_length=len(body),
        declared_length=len(body),
    )


def _drive(
    core: Any,
    *,
    pages: list[str] | None = None,
    landing: str = f"https://funpay.com/lots/{NODE}/trade",
) -> tuple[Any, list[Any]]:
    """Прокручивает ядро, отвечая на его просьбы.

    Аргументы:
        core (Any): сопрограмма ядра.
        pages (list[str] | None): страницы на каждое чтение по порядку.
        landing (str): куда приводит сохранение.

    Возвращает:
        tuple[Any, list[Any]]: итог и перечень просьб.
    """
    asked: list[Any] = []
    served = list(pages or [])
    reply: Any = None
    while True:
        try:
            request = core.send(reply)
        except StopIteration as stop:
            return stop.value, asked
        asked.append(request)
        reply = None
        if isinstance(request, Fetch):
            html = served.pop(0) if served else _page()
            reply = _observation(html, url="https://funpay.com/lots/offerEdit")
        elif isinstance(request, Submit):
            reply = _observation("<html></html>", url=landing)


def _engine(state_path: Path | None = None) -> Engine:
    """Собирает движок без сети, но с долговечным журналом правок.

    Журнал здесь не украшение. Правка цены без него ОТКАЗЫВАЕТ, и отказ этот
    проверяется отдельно; всем прочим проверкам нужен движок, который правку
    допускает.

    Аргументы:
        state_path (Path | None): файл состояния либо None - тогда журнал
            объявляется недолговечным послаблением, без файла.

    Возвращает:
        Engine: движок.
    """
    if state_path is not None:
        return Engine(TransportSettings(), Budget(), state_path=state_path)
    return Engine(TransportSettings(), Budget(), unsafe_price_changes_without_audit=True)


def _revision() -> str:
    """Возвращает отпечаток формы со снимка.

    Возвращает:
        str: отпечаток.
    """
    return parse_lot_form(_page(), observed_at=WHEN).revision


def _expected_request(html: str, *, price: str) -> dict[str, str]:
    """Собирает ожидаемый запрос ВТОРОЙ, независимой рукой.

    Разбирать форму тем же кодом, что и операция, значит сверять его с ним
    самим: испорти он сборку запроса - испортятся обе стороны сравнения, и
    проверка этого не заметит. Ровно так четыре мутации и выжили.

    Здесь разметка читается заново и правило пишется прямо: непустое имя, не
    флажок - значение; флажок с пометкой - «on»; флажок без пометки не уходит.

    Аргументы:
        html (str): разметка страницы правки.
        price (str): цена, которую ждём в запросе.

    Возвращает:
        dict[str, str]: поля, которые обязаны уйти.
    """
    from selectolax.parser import HTMLParser

    form = HTMLParser(html).css_first("form.form-offer-editor")
    assert form is not None

    out: dict[str, str] = {}
    for node in form.css("input, textarea, select"):
        attributes = node.attributes or {}
        name = attributes.get("name")
        if not name:
            continue
        if attributes.get("type") == "checkbox":
            if "checked" in attributes:
                out[name] = "on"
            continue
        out[name] = (
            (node.text() or "") if node.tag == "textarea" else (attributes.get("value") or "")
        )

    out["price"] = price
    return out


def test_everything_read_is_sent_back_and_only_the_price_changes() -> None:
    """ГЛАВНАЯ ПРОВЕРКА НАБОРА.

    Форма несёт описание лота, сообщение покупателю после оплаты и картинки.
    Собрать запрос из перечня нужных полей было бы короче - и стёрло бы всё
    остальное, а узнал бы об этом продавец глазами.

    Эталон собирается ВТОРОЙ рукой, а не тем же кодом: сверка кода с ним самим
    пропустила четыре мутации подряд.

    Возвращает:
        None
    """
    html = _page()
    before = parse_lot_form(html, observed_at=WHEN)

    _, asked = _drive(
        _engine().update_price(NODE, OFFER, "2.50", expected_revision=before.revision)
    )
    submits = [one for one in asked if isinstance(one, Submit)]

    assert len(submits) == 1, f"запросов сохранения {len(submits)}"
    sent = submits[0].fields
    assert submits[0].path == SAVE_PATH

    expected = _expected_request(html, price="2.50")

    assert set(sent) == set(expected), (
        f"состав полей разошёлся: лишние {sorted(set(sent) - set(expected))}, "
        f"потерянные {sorted(set(expected) - set(sent))}"
    )
    for name, value in expected.items():
        assert sent[name] == value, (
            f"поле {name!r} ушло как {sent[name]!r} вместо {value!r} - правка "
            "цены испортила чужой текст"
        )

    # Отдельно и вслух: описание лота и сообщение покупателю обязаны уйти
    # НЕПУСТЫМИ. Пустое поле здесь стирает то, что продавец писал руками.
    for name in ("fields[desc][ru]", "fields[payment_msg][ru]", "fields[summary][ru]"):
        assert sent[name], f"поле {name!r} ушло пустым: текст продавца стёрт"

    assert sent["active"] == "on", "отмеченный флажок ушёл не значением «on»"


def test_a_stale_revision_stops_the_write() -> None:
    """Требует отказать, если лот успели изменить.

    Без этого параллельная правка - из другого процесса, из приложения, из
    веб-интерфейса - перетирается молча. Предусловие объявлено контрактом
    обязательным.

    Возвращает:
        None
    """
    with pytest.raises(PreconditionFailedError, match="изменился"):
        _drive(_engine().update_price(NODE, OFFER, "2.50", expected_revision="устаревший"))


def test_no_write_happens_when_the_revision_is_stale() -> None:
    """Требует, чтобы при расхождении отпечатка НИЧЕГО не ушло.

    Отказ после отправки был бы отказом задним числом: лот уже изменён.

    Возвращает:
        None
    """
    asked: list[Any] = []
    core = _engine().update_price(NODE, OFFER, "2.50", expected_revision="устаревший")
    reply: Any = None
    with pytest.raises(PreconditionFailedError):
        while True:
            request = core.send(reply)
            asked.append(request)
            reply = _observation(_page(), url="https://funpay.com/lots/offerEdit")

    assert not [one for one in asked if isinstance(one, Submit)], "запрос ушёл до отказа"


def test_an_inactive_lot_is_refused() -> None:
    """Требует отказать, когда лот выключен.

    Что уходит при СНЯТОМ флажке, никто не наблюдал. Отправив форму, мы
    отправили бы флажок отмеченным - то есть включили бы лот, которого не
    просили включать, и продавец узнал бы об этом из выдачи.

    Возвращает:
        None
    """
    off = _page(active=False)
    revision = parse_lot_form(off, observed_at=WHEN).revision

    with pytest.raises(UsageError, match="выключен"):
        _drive(
            _engine().update_price(NODE, OFFER, "2.50", expected_revision=revision),
            pages=[off],
        )


def test_an_empty_revision_is_refused_before_the_network() -> None:
    """Требует потребовать отпечаток, а не подставлять умолчание.

    Умолчание здесь означало бы «перетирай молча».

    Возвращает:
        None
    """
    asked: list[Any] = []
    core = _engine().update_price(NODE, OFFER, "2.50", expected_revision="")
    with pytest.raises(UsageError, match="expected_revision"):
        asked.append(core.send(None))

    assert not asked, "до сети дошло, хотя отпечатка не дали"


def test_an_empty_price_is_refused() -> None:
    """Требует отказать на пустой цене.

    Пустое поле стирает цену, а не оставляет прежнюю.

    Возвращает:
        None
    """
    with pytest.raises(ValidationError, match="цена"):
        _drive(_engine().update_price(NODE, OFFER, "   ", expected_revision=_revision()))


@pytest.mark.parametrize("bad", ["", "  ", "abc", "19o8", "1908/../"])
def test_a_bad_identifier_is_refused_before_the_network(bad: str) -> None:
    """Требует проверять идентификаторы ДО сети.

    Подставленный в адрес мусор отправил бы запрос неизвестно куда, а здесь по
    адресу открывается чужой лот.

    Аргументы:
        bad (str): непригодный идентификатор.

    Возвращает:
        None
    """
    with pytest.raises(ValidationError):
        _drive(_engine().update_price(bad, OFFER, "2.50", expected_revision=_revision()))

    with pytest.raises(ValidationError):
        _drive(_engine().update_price(NODE, bad, "2.50", expected_revision=_revision()))


def test_landing_somewhere_else_is_not_called_success() -> None:
    """Требует не объявлять успех по чужому адресу.

    Успех наблюдался ПЕРЕХОДОМ на список своих предложений раздела. Тела
    ответа страница не получает, и другого признака у нас нет: приведи
    сохранение куда-то ещё - что случилось с лотом, неизвестно.

    Возвращает:
        None
    """
    with pytest.raises(UnexpectedResponseError, match="привело"):
        _drive(
            _engine().update_price(NODE, OFFER, "2.50", expected_revision=_revision()),
            landing="https://funpay.com/lots/offerEdit",
        )


def test_the_form_is_read_again_after_saving() -> None:
    """Требует вернуть перечитанную форму, а не ту, что отправляли.

    Возвращённая «как отправляли» показала бы цену, которую мы ХОТЕЛИ, а не ту,
    которую площадка приняла.

    Возвращает:
        None
    """
    after, asked = _drive(
        _engine().update_price(NODE, OFFER, "2.50", expected_revision=_revision()),
        pages=[_page(), _page(price="2.50")],
    )

    reads = [one for one in asked if isinstance(one, Fetch)]
    assert len(reads) == 2, f"чтений формы {len(reads)}: до отправки и после"
    assert after.price_text == "2.50", f"вернулась цена {after.price_text!r}"


def test_the_revision_ignores_what_changes_on_every_load() -> None:
    """Требует, чтобы отпечаток не менялся от перезагрузки страницы.

    Защитный токен и метка сборки формы меняются при каждом чтении. Отпечаток,
    считанный вместе с ними, не совпал бы сам с собой уже через секунду, и
    правка цены отказывала бы всегда.

    Возвращает:
        None
    """
    html = _page()
    first = parse_lot_form(html, observed_at=WHEN)

    other = html.replace(
        f'name="form_created_at" type="hidden" value="{first.fields["form_created_at"]}"',
        'name="form_created_at" type="hidden" value="9999999999"',
        1,
    )
    assert other != html, "метка не подменилась"

    assert parse_lot_form(other, observed_at=WHEN).revision == first.revision, (
        "отпечаток изменился от перезагрузки: правка цены отказывала бы всегда"
    )


def test_the_revision_changes_when_the_lot_changes() -> None:
    """Требует, чтобы отпечаток менялся от правки лота.

    Иначе он ничего не защищает.

    Возвращает:
        None
    """
    first = parse_lot_form(_page(), observed_at=WHEN)
    assert parse_lot_form(_page(price="9.99"), observed_at=WHEN).revision != first.revision
    assert parse_lot_form(_page(active=False), observed_at=WHEN).revision != first.revision
