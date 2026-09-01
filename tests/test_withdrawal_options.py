"""Проверки способов вывода средств.

ЭТО РЕДКИЙ СЛУЧАЙ: ЗДЕСЬ НАШЕ НАБЛЮДЕНИЕ ТОЧНЕЕ ЧУЖОГО.

Независимая реализация того же протокола держит перечень способов вывода
рукописным списком из ВОСЬМИ. У площадки их ТРИНАДЦАТЬ, и два из восьми неверны:

  значения binance в перечне площадки нет вовсе - там binance_usdc и
  binance_usdt, и отправка «binance» была бы молча отброшена;
  fps и yandex - РАЗНЫЕ способы, слитые у неё в один.

Отсюда правило шире случая: рукописный перечень чужой стороны устаревает молча.
Наш устарел бы так же, и потому его тут нет - ключи читаются из того, что
прислала площадка.

Проверки набора стоят НА СНИМКЕ. Зашей я перечень руками - он разойдётся со
снимком и упадёт.

Наблюдено 31.08.2026: account-balance.v9.logged.ru, атрибут data-data у
div.withdraw-box.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from funora._account import (
    WithdrawalChannel,
    WithdrawalOption,
    parse_balance_page,
    parse_withdrawal_box,
)

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
SNAPSHOT: Final[Path] = ROOT / "tests/fixtures/pages/account-balance.v9.logged.ru.skeleton.txt"

#: Перечень, который держит рукописным сторонняя реализация. Стоит здесь НЕ как
#: истина, а как то, с чем мы сравниваем: расхождение и есть предмет проверки.
THIRD_PARTY_LIST: Final[frozenset[str]] = frozenset(
    {"qiwi", "fps", "binance", "usdt_trc", "card_rub", "card_usd", "card_eur", "wmz"}
)


def _snapshot() -> str:
    """Читает снимок страницы баланса.

    Возвращает:
        str: Разметка.
    """
    return SNAPSHOT.read_text(encoding="utf-8")


def _declared_keys() -> set[str]:
    """Достаёт перечень способов прямо из снимка, минуя наш разбор.

    Проверка, читающая перечень нашим же разбором, проверяла бы разбор сам
    собой. Здесь он добывается независимо - регулярным выражением по атрибуту.

    Возвращает:
        set[str]: Машинные имена способов.
    """
    found = re.search(r'withdraw-box"\s+data-data="([^"]+)"', _snapshot())
    assert found is not None, "область вывода в снимке не найдена - проверка стала пустой"
    settings = json.loads(html_lib.unescape(found.group(1)))
    return set(settings["extCurrencies"])


def test_the_list_of_options_comes_from_the_snapshot_not_from_us() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: перечень читается, а не зашивается.

    Разбор сверяется с тем, что лежит в снимке, добытым независимо. Зашей мы
    перечень руками - он разойдётся и упадёт здесь.

    Возвращает:
        None
    """
    options, _channels, defects = parse_withdrawal_box(_snapshot())

    assert defects == (), f"разбор нашёл повреждения: {defects}"
    assert {one.key for one in options} == _declared_keys()


def test_the_platform_declares_more_options_than_the_third_party_knows() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: чужой перечень неполон, и это доказуемо.

    Проверка стоит здесь не ради укора соседу. Она держит вывод, ради которого
    перечень и читается: рукописный список устаревает МОЛЧА, и наш устарел бы
    так же.

    Возвращает:
        None
    """
    declared = _declared_keys()

    assert len(declared) > len(THIRD_PARTY_LIST), (
        f"у площадки {len(declared)} способов, у стороннего перечня "
        f"{len(THIRD_PARTY_LIST)} - расхождения не стало, и вывод потерял опору"
    )
    missing = declared - THIRD_PARTY_LIST
    assert missing, "сторонний перечень оказался полным - вывод надо пересмотреть"


def test_the_third_party_list_names_options_the_platform_does_not_have() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: у чужого перечня есть НЕСУЩЕСТВУЮЩИЙ способ.

    Это хуже неполноты. Неполнота отнимает возможность; неверное значение
    отправляется площадке и отбрасывается ею молча - вывод не состоится, а
    отказа не будет.

    Возвращает:
        None
    """
    declared = _declared_keys()
    invented = THIRD_PARTY_LIST - declared

    assert "binance" in invented, (
        "значение binance перестало быть выдуманным - либо площадка его завела, "
        "либо мы читаем не тот снимок"
    )


def test_fps_and_yandex_are_two_different_options() -> None:
    """Требует, чтобы два способа не слились в один.

    Сторонний источник отображает ЮMoney на fps. У площадки это разные ключи, и
    какой из них ЮMoney - мы не проверяли. Слить их значило бы вывести деньги не
    туда, куда просили.

    Возвращает:
        None
    """
    declared = _declared_keys()
    assert {"fps", "yandex"} <= declared, "один из двух способов пропал из снимка"


def test_every_option_carries_its_saved_wallets_as_a_list() -> None:
    """Требует читать сохранённые кошельки списком, пусть и пустым.

    Пустой список - положительный признак: площадка сказала «сохранённых нет».
    Отсутствие поля означало бы другое, и различать их обязательно.

    Возвращает:
        None
    """
    options, _channels, _defects = parse_withdrawal_box(_snapshot())

    assert options, "способов не прочиталось ни одного"
    for one in options:
        assert isinstance(one.saved_wallets, tuple), f"{one.key}: кошельки не список"
    # У наблюдённого аккаунта пусты ВСЕ. Наполнение не наблюдалось, и проверка
    # держит именно это: найдётся непустой - снимок другой, и запись устарела.
    assert all(one.saved_wallets == () for one in options), (
        "нашёлся непустой список кошельков - наблюдение изменилось, и запись "
        "«наполнение не наблюдалось» пора перечитать"
    )


def test_channels_name_their_currency_and_their_option() -> None:
    """Требует, чтобы канал называл и валюту, и способ.

    Канал без одного из двух не говорит ничего: вывести можно ЭТОЙ валютой ЭТИМ
    способом, и порознь половины бессмысленны.

    НА СНИМКЕ СВЯЗЬ НЕ ЧИТАЕТСЯ, и это находка сама по себе. Формат скелета
    сохраняет дословно КЛЮЧИ объектов, а не значения; перечень способов лежит
    ключами и уцелел, а ссылка канала на способ лежит ЗНАЧЕНИЕМ и замаскирована.

    То есть со снимка мы знаем, какие способы есть, и не знаем, какой валютой
    какой из них доступен. Проверка держит это различие явно.

    Возвращает:
        None
    """
    _options, channels, _defects = parse_withdrawal_box(_snapshot())

    assert channels, "каналов не прочиталось ни одного"
    for one in channels:
        assert one.currency in {"RUB", "USD", "EUR"}, f"валюта канала {one.currency!r}"
        assert one.option_key, "ссылка канала на способ пуста"


def test_the_link_from_a_channel_to_an_option_is_read_verbatim() -> None:
    """Обратная половина: на живых значениях связь читается точно.

    На снимке ссылка замаскирована, и предыдущая проверка не может её сверить.
    Здесь значения настоящие - и разбор обязан взять их как есть, не переиначив.

    Возвращает:
        None
    """
    settings = {
        "extCurrencies": {"card_rub": {"name": "Карта", "unit": "RUB", "wallets": []}},
        "currencies": {
            "rub": {"channels": [{"extCurrency": "card_rub", "name": "Карта", "feeInfo": "3%"}]}
        },
    }
    raw = html_lib.escape(json.dumps(settings, ensure_ascii=False), quote=True)
    options, channels, _defects = parse_withdrawal_box(
        f'<div class="withdraw-box" data-data="{raw}"></div>'
    )

    assert [one.key for one in options] == ["card_rub"]
    assert channels[0].option_key == "card_rub"
    assert channels[0].currency == "RUB"
    assert channels[0].fee_text.or_none() == "3%"


def test_the_currency_of_a_channel_is_upper_case() -> None:
    """Требует приводить код валюты к прописным.

    Площадка объявляет валюты строчными ключами, а переключатель в шапке -
    прописными. Два вида одного кода разошлись бы при сверке молча.

    Возвращает:
        None
    """
    _options, channels, _defects = parse_withdrawal_box(_snapshot())
    assert all(one.currency == one.currency.upper() for one in channels)


def test_the_fee_is_kept_as_text() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: комиссия остаётся текстом.

    Слова «комиссия» в проекте до 31.08.2026 не было нигде. Названа она теперь -
    и названа текстом, потому что иначе нельзя: это строка на локали интерфейса,
    а строить расчёт ДЕНЕГ на переводе запрещено правилом.

    Возвращает:
        None
    """
    _options, channels, _defects = parse_withdrawal_box(_snapshot())

    assert any(one.fee_text.is_observed for one in channels), "комиссия не прочиталась ни у одного"

    fields = set(WithdrawalChannel.__dataclass_fields__)
    assert not {one for one in fields if "percent" in one or "amount" in one}, (
        f"у канала завелось поле величины комиссии: {sorted(fields)}. Вывести её "
        "можно только из текста на локали, и это запрещено правилом"
    )


def test_a_page_without_the_box_is_not_a_defect() -> None:
    """Требует не считать отсутствие области вывода поломкой.

    Гостю она не показывается вовсе. Объявить это повреждением значило бы
    сломать чтение страницы под гостем.

    Возвращает:
        None
    """
    options, channels, defects = parse_withdrawal_box("<body><div>ничего</div></body>")

    assert options == () and channels == ()
    assert defects == (), "отсутствие области объявлено повреждением"


def test_unreadable_settings_are_a_defect_and_not_silence() -> None:
    """Требует объявлять повреждением НЕРАЗБИРАЕМЫЕ настройки.

    Обратная половина предыдущей. Область есть, а настройки в ней не читаются -
    это уже поломка, и молчать о ней нельзя: способов вывода не окажется, и
    вызывающий решит, что их нет.

    Возвращает:
        None
    """
    broken = '<div class="withdraw-box" data-data="не json"></div>'
    options, channels, defects = parse_withdrawal_box(broken)

    assert options == () and channels == ()
    assert len(defects) == 1
    assert defects[0].code == "withdraw_box_not_json"


def test_the_balance_page_carries_them_without_a_second_request() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: способы приходят чтением баланса.

    Отдельного запроса за ними нет, и делать его значило бы ходить на площадку
    дважды за одним ответом - тот же довод, по которому на этой же странице
    лежат операции по счёту.

    Возвращает:
        None
    """
    page = parse_balance_page(_snapshot(), datetime(2026, 8, 31, tzinfo=UTC))

    assert page.withdrawal_options, "страница баланса не принесла способов вывода"
    assert page.withdrawal_channels, "страница баланса не принесла каналов"
    assert {one.key for one in page.withdrawal_options} == _declared_keys()


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"extCurrencies": "не объект"},
        {"extCurrencies": {"card_rub": "не объект"}},
        {"currencies": {"rub": {"channels": "не список"}}},
        {"currencies": {"rub": {"channels": ["не объект"]}}},
    ],
)
def test_a_misshapen_settings_object_does_not_crash(settings: dict[str, Any]) -> None:
    """Требует переживать непригодные настройки без падения языка.

    Отказ самого языка здесь особенно дорог: он останавливает не чтение способов
    вывода, а чтение всей страницы баланса - вместе с балансами и операциями.

    Аргументы:
        settings (dict[str, Any]): Непригодные настройки.

    Возвращает:
        None
    """
    raw = html_lib.escape(json.dumps(settings, ensure_ascii=False), quote=True)
    options, channels, _defects = parse_withdrawal_box(
        f'<div class="withdraw-box" data-data="{raw}"></div>'
    )
    assert isinstance(options, tuple) and isinstance(channels, tuple)


def test_the_option_model_has_no_invented_fields() -> None:
    """Требует, чтобы у способа не завелось полей сверх наблюдённых.

    Наблюдены четыре: машинное имя, название, единица и подпись поля адреса.
    Пятое означало бы, что мы что-то вывели, а не прочитали.

    Возвращает:
        None
    """
    assert set(WithdrawalOption.__dataclass_fields__) == {
        "key",
        "name",
        "unit",
        "wallet_label",
        "saved_wallets",
    }


def test_a_saved_wallet_is_read_and_not_dropped() -> None:
    """Требует читать НЕПУСТОЙ список кошельков.

    У наблюдённого аккаунта пусты все тринадцать, и потому проверка на снимке
    прошла бы и у разбора, который список всегда выбрасывает.

    Здесь значения настоящие. Потерять сохранённый адрес значило бы показать
    продавцу «кошельков нет» там, где они есть, - и отправить его заводить их
    заново.

    Возвращает:
        None
    """
    settings = {
        "extCurrencies": {
            "card_rub": {"name": "Карта", "wallets": ["2202********1234", "4276********5678"]},
            "qiwi": {"name": "Киви", "wallets": []},
        }
    }
    raw = html_lib.escape(json.dumps(settings, ensure_ascii=False), quote=True)
    options, _channels, _defects = parse_withdrawal_box(
        f'<div class="withdraw-box" data-data="{raw}"></div>'
    )
    by_key = {one.key: one for one in options}

    assert by_key["card_rub"].saved_wallets == ("2202********1234", "4276********5678")
    assert by_key["qiwi"].saved_wallets == (), "пустой список стал непустым"


def test_a_wallet_that_is_not_a_string_is_dropped_and_the_rest_survive() -> None:
    """Требует отбрасывать нестроковый адрес, не теряя остальных.

    Отбросить весь список из-за одного непригодного значило бы сказать «нет
    кошельков» там, где есть три из четырёх.

    Возвращает:
        None
    """
    settings = {"extCurrencies": {"qiwi": {"wallets": ["+79001234567", 42, None, "второй"]}}}
    raw = html_lib.escape(json.dumps(settings, ensure_ascii=False), quote=True)
    options, _channels, _defects = parse_withdrawal_box(
        f'<div class="withdraw-box" data-data="{raw}"></div>'
    )

    assert options[0].saved_wallets == ("+79001234567", "второй")


def test_an_empty_name_is_observed_as_empty_and_not_as_a_value() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ШЕСТАЯ: пустая строка не выдаётся за значение.

    Различие тут не косметическое. «Пусто» - наблюдение: площадка прислала поле
    и оставила его пустым. «Значение» - утверждение, что название способа
    вправду есть и равно пустой строке.

    Вызывающий, показывающий продавцу перечень способов, во втором случае
    напечатает пустую строку и решит, что так и надо.

    Возвращает:
        None
    """
    settings = {"extCurrencies": {"qiwi": {"name": "  ", "unit": "RUB", "wallets": []}}}
    raw = html_lib.escape(json.dumps(settings, ensure_ascii=False), quote=True)
    options, _channels, _defects = parse_withdrawal_box(
        f'<div class="withdraw-box" data-data="{raw}"></div>'
    )

    from funora._observed import Presence

    # ОБА СОСТОЯНИЯ НАБЛЮДЁННЫЕ, и это верно: «пусто» - тоже наблюдение.
    # Различает их presence, и различие держится именно на нём.
    name = options[0].name
    assert name.or_none() == "", "пустое название прочиталось иначе"
    assert name.presence is Presence.EMPTY, (
        "пустая строка объявлена значением. Пусто - это наблюдение «площадка "
        "оставила поле пустым», а не «название равно пустой строке»"
    )
    assert options[0].unit.presence is Presence.PRESENT, "непустое поле стало пустым"
