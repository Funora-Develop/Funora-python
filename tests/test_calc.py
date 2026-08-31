"""Проверки расчёта цены покупателя.

ЗАЧЕМ ЭТА ОПЕРАЦИЯ ВООБЩЕ. Цена, которую ставит продавец, и цена, которую платит
покупатель, - разные величины: между ними комиссия площадки, и зависит она от
способа оплаты. Разрыв этот наблюдаем нашим же снимком формы правки лота, а в
контракте до 31.08.2026 не назывался нигде - и молчание читалось как «цена одна».

ГЛАВНОЕ ОТЛИЧИЕ НАШЕГО РАЗБОРА ОТ СТОРОННЕГО - в том, что мы НЕ переводим цены в
число.

Сторонняя реализация, у которой взят состав запроса, убирает из строки пробелы и
зовёт float. На локали с запятой это отказ САМОГО ЯЗЫКА: не «не смогли посчитать»,
а падение у вызывающего, который ничего такого не просил.

Разделитель дробной части нам не наблюдался (см. amount_stays_text), и потому
цена отдаётся текстом, а знак валюты - рядом.

Отсюда же и вторая главная проверка: чтение на вторичном источнике согласия НЕ
спрашивает. Правило требует его у записи.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._calc import CHIPS_CALC_PATH, LOTS_CALC_PATH, parse_calculation
from funora._engine import Engine, Submit
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError, ValidationError
from funora.operations import OPERATIONS

NODE: Final[str] = "1908"
GAME: Final[str] = "283"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)

ANSWER: Final[dict[str, Any]] = {
    "methods": [
        {"name": "Банковская карта", "price": "1 234,56", "unit": "₽", "sort": 1},
        {"name": "СБП", "price": "1 200,00", "unit": "₽", "sort": 2},
    ],
    "minPrice": "50 ₽",
}


def _observation(html: str, url: str) -> Observation:
    """Собирает наблюдение.

    Аргументы:
        html (str): Тело ответа.
        url (str): Конечный адрес.

    Возвращает:
        Observation: Наблюдение.
    """
    raw = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=url,
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(raw),
        declared_length=len(raw),
    )


class _Scripted:
    """Отвечает на просьбу одним телом."""

    def __init__(self, body: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            body (str | None): Тело ответа.

        Возвращает:
            None
        """
        self.body = body if body is not None else json.dumps(ANSWER, ensure_ascii=False)
        self.submits: list[Submit] = []

    def run(self, core: Any) -> Any:
        """Прокручивает ядро.

        Аргументы:
            core (Any): Сопрограмма.

        Возвращает:
            Any: Итог.
        """
        reply: Any = None
        while True:
            try:
                request = core.send(reply)
            except StopIteration as stop:
                return stop.value
            if isinstance(request, Submit):
                self.submits.append(request)
                reply = _observation(self.body, f"https://funpay.com{request.path}")
            else:
                reply = None


def _engine() -> Engine:
    """Собирает движок без сети.

    Возвращает:
        Engine: Движок.
    """
    return Engine(TransportSettings(), Budget())


def test_prices_stay_text_and_are_never_turned_into_numbers() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: цена остаётся текстом.

    Строка «1 234,56» - обычная русская запись. Сторонняя реализация убирает из
    неё пробелы и зовёт float, то есть падает отказом самого языка.

    Мы отдаём её как есть, а знак валюты - отдельным полем.

    Возвращает:
        None
    """
    script = _Scripted()
    result = script.run(_engine().calculate_prices(node_id=NODE, price="1000"))

    assert result.methods[0].price_text == "1 234,56", "цена изменилась при чтении"
    assert result.methods[0].currency_symbol.or_none() == "₽"
    assert isinstance(result.methods[0].price_text, str)


def test_the_symbol_becomes_a_code_by_the_closed_set() -> None:
    """Требует, чтобы знак из ответа переводился в код.

    Ради этого перевод и заводился: набор валют площадки замкнут тремя, и
    таблица знаков потому не гадание, а чтение.

    Возвращает:
        None
    """
    from funora import currency_of_symbol

    script = _Scripted()
    result = script.run(_engine().calculate_prices(node_id=NODE, price="1000"))

    assert currency_of_symbol(result.methods[0].currency_symbol.value) == "RUB"


def test_reading_on_a_secondary_source_asks_no_consent() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: чтение согласия не спрашивает.

    Операция стоит на вторичном источнике - и всё же не спрашивает согласия.
    Правило требует его у операций ЗАПИСИ: чтение на чужом знании ошибётся
    ВИДИМО, а запись - необратимо.

    Спрашивать везде подряд значило бы обесценить механизм: вызывающий,
    привыкший включать всё, перестанет читать, ЧТО ему предлагают включить.

    Возвращает:
        None
    """
    contract = OPERATIONS["lots.calculate_prices"]
    assert contract.request_provenance == "third_party_report"
    assert contract.safety.value == "safe", "расчёт объявлен небезопасным - это чтение"

    script = _Scripted()
    # Согласия не давали вовсе.
    script.run(_engine().calculate_prices(node_id=NODE, price="1000"))
    assert len(script.submits) == 1


def test_the_two_addresses_take_two_different_arguments() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: у лотов раздел, у чипов игра.

    Адреса разные, и имена полей разные. Перепутать их значило бы спросить
    площадку не о том и получить ответ, который выглядит как ответ.

    Возвращает:
        None
    """
    lots = _Scripted()
    lots.run(_engine().calculate_prices(node_id=NODE, price="1000"))
    assert lots.submits[0].path == LOTS_CALC_PATH
    assert lots.submits[0].fields == {"nodeId": NODE, "price": "1000"}

    chips = _Scripted()
    chips.run(_engine().calculate_prices(game_id=GAME, price="1000"))
    assert chips.submits[0].path == CHIPS_CALC_PATH
    assert chips.submits[0].fields == {"game": GAME, "price": "1000"}


@pytest.mark.parametrize(
    ("node", "game"),
    [(None, None), (NODE, GAME)],
)
def test_exactly_one_argument_is_required(node: str | None, game: str | None) -> None:
    """Требует ровно одного довода.

    Ни одного - спрашивать не о чем. Оба - неизвестно, какой адрес имелся в
    виду, и угадывать мы не станем.

    Аргументы:
        node (str | None): Раздел.
        game (str | None): Игра.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().calculate_prices(node_id=node, game_id=game, price="1000"))
    assert script.submits == []


def test_an_empty_price_is_refused_before_the_network() -> None:
    """Требует отказа на пустой цене.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().calculate_prices(node_id=NODE, price="   "))
    assert script.submits == []


def test_a_refusal_in_the_body_is_not_read_as_a_calculation() -> None:
    """Требует отвергать ответ с признаком отказа.

    Возвращает:
        None
    """
    script = _Scripted(json.dumps({"error": "что-то не так"}, ensure_ascii=False))
    with pytest.raises(ProtocolChangedError) as raised:
        script.run(_engine().calculate_prices(node_id=NODE, price="1000"))
    assert "отказала в расчёте" in str(raised.value)


def test_a_missing_list_is_not_an_empty_one() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: нет перечня - это не пустой перечень.

    Пустой означает наблюдение «способов не предложено». Отсутствующий означает,
    что мы читаем не тот ответ, и молча выдать за него пустоту значило бы
    сказать продавцу «оплатить нечем».

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_calculation({"minPrice": "50 ₽"}, asked_price="1000", observed_at=WHEN)

    empty = parse_calculation({"methods": []}, asked_price="1000", observed_at=WHEN)
    assert empty.methods == ()


def test_a_method_without_a_price_is_refused() -> None:
    """Требует отвергать способ оплаты без цены.

    Подставить сюда ноль значило бы сказать продавцу, что покупатель заплатит
    ничего.

    Возвращает:
        None
    """
    for broken in ({"name": "карта"}, {"name": "карта", "price": ""}, {"price": 1234}):
        with pytest.raises(ProtocolChangedError):
            parse_calculation({"methods": [broken]}, asked_price="1000", observed_at=WHEN)


def test_the_minimum_price_is_kept_whole() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: наименьшая цена не делится.

    Сторонняя реализация делит эту строку по последнему пробелу и зовёт float на
    первой половине. Мы храним её целиком - вместе со знаком валюты.

    Возвращает:
        None
    """
    result = parse_calculation(ANSWER, asked_price="1000", observed_at=WHEN)
    assert result.min_price_text.or_none() == "50 ₽"


def test_an_absent_minimum_is_not_invented() -> None:
    """Требует, чтобы отсутствие наименьшей цены не подменялось нулём.

    Возвращает:
        None
    """
    result = parse_calculation({"methods": []}, asked_price="1000", observed_at=WHEN)
    assert result.min_price_text.or_none() is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, 1), (0, 0), ("3", 3), ("-1", -1), (True, None), (False, None), (1.5, None), (None, None)],
)
def test_the_sort_number_refuses_anything_that_is_not_one(raw: Any, expected: int | None) -> None:
    """Требует, чтобы порядковое число читалось числом.

    Логическое исключается отдельно: истина в Python - это единица, и порядок
    True встал бы между нулевым и вторым, ни разу не будучи числом.

    Аргументы:
        raw (Any): Значение из ответа.
        expected (int | None): Ожидаемое прочтение.

    Возвращает:
        None
    """
    result = parse_calculation(
        {"methods": [{"name": "к", "price": "1", "unit": "₽", "sort": raw}]},
        asked_price="1000",
        observed_at=WHEN,
    )
    assert result.methods[0].sort.or_none() == expected


def test_the_asked_price_comes_back_untouched() -> None:
    """Требует возвращать спрошенную цену как есть.

    Без неё расчёт нельзя связать с вопросом: способов много, а цена продавца
    была одна.

    Возвращает:
        None
    """
    script = _Scripted()
    result = script.run(_engine().calculate_prices(node_id=NODE, price="  1000  "))
    assert result.asked_price == "1000"


def test_the_capability_is_marked_after_a_parsed_answer() -> None:
    """Требует выставлять состояние по разобранному ответу.

    Возвращает:
        None
    """
    engine = _engine()
    _Scripted().run(engine.calculate_prices(node_id=NODE, price="1000"))

    assert engine._state.capabilities[Capability.LOTS_CALCULATE_PRICES] is CapabilityState.SUPPORTED
    assert (
        engine._state.capabilities[Capability.CHIPS_CALCULATE_PRICES]
        is not CapabilityState.SUPPORTED
    )


def test_a_body_that_is_not_json_is_refused() -> None:
    """Требует отвергать неразобравшееся тело.

    Возвращает:
        None
    """
    script = _Scripted("<html>нет</html>")
    with pytest.raises(ProtocolChangedError):
        script.run(_engine().calculate_prices(node_id=NODE, price="1000"))


@pytest.mark.parametrize("payload", ["строка", 42, None, [], True])
def test_an_unusable_payload_is_refused(payload: Any) -> None:
    """Требует отвергать непригодное тело, а не толковать его.

    Проверка выглядит рутинной и таковой не является. Без неё разбор, у которого
    убрана проверка вида, падает на строке отказом самого языка - AttributeError
    вместо FunoraError, - и общий перехват вызывающего его не поймает: остановится
    не операция, а весь его цикл.

    Аргументы:
        payload (Any): Непригодное тело.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_calculation(payload, asked_price="1000", observed_at=WHEN)
