"""Проверки перевода знака валюты в код ISO 4217.

ПОЧЕМУ ЭТУ ТАБЛИЦУ ВООБЩЕ МОЖНО БЫЛО ЗАВЕСТИ. Три недели проект отказывался её
писать, и отказывался правильно: знак доллара носят полтора десятка валют мира,
и выбирать среди них наугад - гадание, а не чтение.

Разрешил её ОДИН факт: набор валют площадки ЗАМКНУТ. Три валюты, три знака,
столкновений нет. Пока это так, таблица - чтение.

Отсюда устройство набора проверок. Он не проверяет, что таблица «правильная» -
это ничего не значит. Он проверяет ДВА условия, при которых она вообще имеет
право существовать, и проверяет их ПО НАШИМ ЖЕ ЗАПИСЯМ, а не по моему слову:

  перечень кодов совпадает с тем, что площадка объявляет сама;
  перечень знаков совпадает с тем, что наблюдалось в span.unit.

Разойдись любое из двух - таблица снова становится гаданием, и проверка обязана
это заметить раньше пользователя.

Улики лежат в tests/fixtures - поимённо, как велит правило проекта. Каталог
observations от репозитория закрыт, и проверка, читающая оттуда, проходила бы
у прочих молча, не проверяя ничего.

Наблюдено 30-31.08.2026: account-balance.v9.logged.ru и восемнадцать проб
валюты в tests/fixtures/probes.
"""

from __future__ import annotations

import glob
import html
import json
import re
from pathlib import Path
from typing import Any, Final

import pytest

from funora._money import CURRENCY_BY_SYMBOL, Money, currency_of_symbol
from funora.errors import ValidationError

ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: Знаки, наблюдавшиеся не в span.unit. Перечислены поимённо, а не отброшены
#: правилом: правило «трёхбуквенное - не знак» отбросило бы и настоящий код.
_NOT_UNITS: Final[frozenset[str]] = frozenset({"GTA", "MIR", "NBA", "XKO", "ZVV"})


def _probes() -> list[dict[str, Any]]:
    """Читает все пробы валюты.

    Возвращает:
        list[dict[str, Any]]: Разобранные пробы.
    """
    out: list[dict[str, Any]] = []
    for name in sorted(glob.glob(str(ROOT / "tests/fixtures/probes" / "currency.*.json"))):
        out.append(json.loads(Path(name).read_text(encoding="utf-8")))
    return out


def test_the_codes_are_the_ones_the_marketplace_declares_itself() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: перечень кодов - площадкин, а не наш.

    Область вывода средств несёт настройки объектом, и ключи объекта снимок
    сохраняет дословно. Три ключа - три валюты, и перечень этот объявляет сама
    площадка.

    Проверка читает снимок, а не повторяет мои слова: перепиши я таблицу - она
    разойдётся со снимком и упадёт.

    Возвращает:
        None
    """
    snapshot = (ROOT / "tests/fixtures/pages/account-balance.v9.logged.ru.skeleton.txt").read_text(
        encoding="utf-8"
    )
    found = re.search(r'withdraw-box"\s+data-data="([^"]+)"', snapshot)
    assert found is not None, "область вывода в снимке не найдена - проверка стала пустой"

    settings = json.loads(html.unescape(found.group(1)))
    declared = {code.upper() for code in settings["currencies"]}

    assert declared == set(CURRENCY_BY_SYMBOL.values()), (
        f"таблица знает {sorted(set(CURRENCY_BY_SYMBOL.values()))}, "
        f"а площадка объявляет {sorted(declared)}. Набор перестал быть замкнутым, "
        "и таблица снова стала гаданием"
    )


def test_the_switcher_always_offers_exactly_the_others() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: замкнутость подтверждена вторым путём.

    Переключатель перечисляет ПРОЧИЕ валюты, не текущую. Если валют три, то
    альтернатив всегда две - и это то же число, добытое иначе.

    Один путь оставил бы вопрос, полон ли перечень. Два независимых - нет.

    Возвращает:
        None
    """
    sizes: list[int] = []
    for probe in _probes():
        codes = {one.get("code") for one in (probe.get("switcher") or []) if one.get("code")}
        if codes:
            sizes.append(len(codes))

    assert sizes, "проб с переключателем не нашлось - проверка стала пустой"
    assert set(sizes) == {2}, (
        f"альтернатив наблюдалось {sorted(set(sizes))}, а при трёх валютах их всегда две"
    )


def test_every_symbol_ever_seen_in_a_unit_is_in_the_table() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: таблица покрывает всё наблюдённое.

    Знак, встреченный рядом с суммой и не попавший в таблицу, - это валюта, чью
    сумму мы прочитали бы неверно либо не прочитали вовсе. Проверка ищет такие
    по ВСЕМ пробам сразу.

    Возвращает:
        None
    """
    seen: set[str] = set()
    for probe in _probes():
        symbols = probe.get("symbols")
        names = (
            list(symbols)
            if isinstance(symbols, dict)
            else [one.get("symbol") for one in (symbols or [])]
        )
        seen.update(one for one in names if one and one not in _NOT_UNITS)

    assert seen, "знаков в пробах не нашлось - проверка стала пустой"
    assert seen == set(CURRENCY_BY_SYMBOL), (
        f"в пробах знаки {sorted(seen)}, а в таблице {sorted(CURRENCY_BY_SYMBOL)}"
    )


def test_the_amounts_on_a_showcase_follow_the_setting() -> None:
    """Требует, чтобы наблюдение «суммы следуют за настройкой» не пропало.

    На этом наблюдении стоит право брать код из ШАПКИ на витрине. Три сбора
    одной страницы в трёх положениях переключателя, и в каждом ровно один знак.

    Возвращает:
        None
    """
    by_symbol: dict[str, int] = {}
    for name in ("rubles", "dollars", "euro"):
        path = ROOT / f"tests/fixtures/probes/currency.users-n.{name}.json"
        probe = json.loads(path.read_text(encoding="utf-8"))
        symbols = probe.get("symbols") or {}
        names = (
            {k: v.get("count", 0) for k, v in symbols.items()}
            if isinstance(symbols, dict)
            else {one.get("symbol"): one.get("count", 0) for one in symbols}
        )
        assert len(names) == 1, f"сбор {name}: знаков {sorted(names)}, а ожидался один"
        by_symbol.update(names)

    assert set(by_symbol) == set(CURRENCY_BY_SYMBOL), (
        "три положения переключателя дали не три разных знака"
    )
    assert all(count > 100 for count in by_symbol.values()), (
        f"цен в сборе слишком мало: {by_symbol}. На единичных ценах вывод не стоит"
    )


def test_the_order_list_does_not_follow_the_setting() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: список продаж за настройкой НЕ следует.

    Это обратная половина предыдущей, и без неё таблицей пользовались бы не там.
    Два сбора списка продаж при РАЗНОЙ текущей валюте дали одинаковый набор
    знаков - значит суммы заказов показаны в валюте своих заказов.

    Отсюда правило: на списке продаж код берётся из знака рядом с суммой, а не
    из шапки.

    Возвращает:
        None
    """
    seen: list[tuple[frozenset[str], frozenset[str]]] = []
    for name in ("codes-carrier", "codes-carrier-2"):
        path = ROOT / f"tests/fixtures/probes/currency.orders-trade.{name}.json"
        probe = json.loads(path.read_text(encoding="utf-8"))
        symbols = probe.get("symbols")
        names = (
            set(symbols)
            if isinstance(symbols, dict)
            else {one.get("symbol") for one in (symbols or [])}
        )
        alternatives = {one.get("code") for one in (probe.get("switcher") or []) if one.get("code")}
        seen.append((frozenset(names & set(CURRENCY_BY_SYMBOL)), frozenset(alternatives)))

    assert len(seen) == 2, "нужны оба сбора списка продаж"
    (symbols_a, alts_a), (symbols_b, alts_b) = seen

    assert alts_a != alts_b, "текущая валюта в двух сборах не менялась - опыт ничего не показывает"
    assert symbols_a == symbols_b, (
        f"знаки сменились с {sorted(symbols_a)} на {sorted(symbols_b)} вместе с настройкой - "
        "значит суммы за настройкой всё-таки следуют, и правило неверно"
    )


@pytest.mark.parametrize(("symbol", "code"), [("₽", "RUB"), ("$", "USD"), ("€", "EUR")])
def test_a_known_symbol_becomes_its_code(symbol: str, code: str) -> None:
    """Требует переводить наблюдённый знак в код.

    Аргументы:
        symbol (str): Знак валюты.
        code (str): Ожидаемый код.

    Возвращает:
        None
    """
    assert currency_of_symbol(symbol) == code
    assert currency_of_symbol(f"  {symbol} ") == code, "краевые пробелы помешали переводу"


@pytest.mark.parametrize("symbol", ["¥", "£", "USD", "", "  ", "₩", "RUB"])
def test_an_unknown_symbol_is_refused_aloud(symbol: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: незнакомый знак отказывает, а не подставляется.

    Заведи площадка четвёртую валюту - появится четвёртый знак. Подставить
    вместо него правдоподобное значило бы приписать сумме чужую валюту, а увидел
    бы это не разработчик, а продавец.

    Код валюты буквами - тоже отказ: таблица переводит ЗНАК, и принять на вход
    готовый код значило бы пропускать мимо себя то, что никто не проверял.

    Аргументы:
        symbol (str): Незнакомый знак.

    Возвращает:
        None
    """
    with pytest.raises(ValidationError) as raised:
        currency_of_symbol(symbol)

    assert "наблюдённого набора" in str(raised.value)


def test_the_table_has_no_collisions() -> None:
    """Требует взаимной однозначности таблицы.

    Два знака на один код означали бы, что по коду знак не восстановить, а
    именно на однозначности стоит право этой таблицей пользоваться.

    Возвращает:
        None
    """
    codes = list(CURRENCY_BY_SYMBOL.values())
    assert len(codes) == len(set(codes)), f"код повторяется: {codes}"
    assert all(re.fullmatch(r"[A-Z]{3}", one) for one in codes), f"код не по ISO 4217: {codes}"


def test_a_price_becomes_money_that_can_be_added() -> None:
    """Требует, чтобы прочитанное складывалось.

    Ради этого тип и заведён: прежде вызывающий складывал цены своим кодом, в
    котором нет ни проверки валют, ни защиты от плавающей точки.

    Возвращает:
        None
    """
    first = Money(150_000, currency_of_symbol("₽"), 2)
    second = Money(25_050, currency_of_symbol("₽"), 2)

    assert (first + second) == Money(175_050, "RUB", 2)
