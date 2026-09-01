"""Проверки возможностей, у которых НЕТ своей операции.

ТАКИХ ВОЗМОЖНОСТЕЙ ТРИ, и объединяет их устройство, а не тема: спросить их
вызывающий может, а позвать - нечего. Состояние им выставляет движок по ходу
другого чтения, и объявлено это в контракте признаком engine_state.

ЧЕМ ОНИ ОПАСНЫ ИМЕННО КАК КЛАСС. Возможность, чьё состояние не выставляет никто,
навсегда остаётся в начальном значении - и отвечает вызывающему уверенно и
неверно. Заметить это снаружи нельзя ничем: вызова, который бы упал, не
существует, потому что операции нет.

Ровно так и жила orders.events до 01.09.2026: спросить её было можно, ответ ни на
что не влиял, и реестр невыполненного объявлял это честно.

Отсюда устройство набора. Перечень берётся ИЗ КОНТРАКТА, и возможность, у которой
завтра появится признак engine_state, попадёт под проверку сама. Ниже - разбор
каждой по отдельности, потому что «состояние выставляется» мало: важно, ЧТО
именно оно различает.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
import yaml
from selectolax.parser import HTMLParser
from test_client import _FakeFetcher, _observation, _page

from funora._account import parse_balance_page
from funora._budget import Budget
from funora._client import Client
from funora._poll import Schedule
from funora._result import Completeness
from funora._watch import Router
from funora.capabilities import Capability, CapabilityState

WHEN: Final[datetime] = datetime(2026, 9, 1, tzinfo=UTC)
BALANCE: Final[str] = "account-balance.logged.ru"
ORDERS: Final[str] = "orders-trade.logged.ru"
CHATS: Final[str] = "chat.logged.ru"

#: Сколько возможностей без операции было на 01.09.2026.
#:
#: Число стоит здесь не ради числа. Опустей перечень - и проверка ниже прошла бы,
#: ничего не проверив: цикл по нулю записей не падает никогда.
_AT_LEAST: Final[int] = 3


def _spec_dir() -> Path | None:
    """Находит каталог спецификации.

    Возвращает:
        Path | None: Каталог либо None, если он не задан.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    return Path(raw) if raw else None


def _state_governed() -> list[str]:
    """Отбирает из контракта возможности, чьё состояние ставит движок.

    Возвращает:
        list[str]: Имена возможностей.
    """
    root = _spec_dir()
    if root is None:
        return []
    doc = yaml.safe_load((root / "spec" / "capabilities.yaml").read_text(encoding="utf-8"))
    return sorted(
        name
        for name, one in doc["capabilities"].items()
        if (one or {}).get("governed_without_operation") == "engine_state"
    )


def _engine_source() -> str:
    """Читает исходник движка.

    Возвращает:
        str: Текст модуля.
    """
    root = Path(__file__).resolve().parent.parent / "src" / "funora"
    return (root / "_engine.py").read_text(encoding="utf-8")


def test_the_registry_of_state_governed_capabilities_is_not_empty() -> None:
    """Требует, чтобы перечень не опустел молча.

    Пустой перечень прошёл бы проверку ниже: цикл по нулю записей не падает
    никогда.

    Возвращает:
        None
    """
    if _spec_dir() is None:
        pytest.skip("каталог спецификации не задан")

    found = _state_governed()
    assert len(found) >= _AT_LEAST, (
        f"возможностей без операции {len(found)}, а было {_AT_LEAST}. Если признак "
        "engine_state сняли - хорошо; если он пропал по недосмотру, то вместе с "
        "ним пропала и проверка того, что состояние вообще выставляется"
    )


@pytest.mark.parametrize("name", _state_governed())
def test_every_state_governed_capability_is_actually_set(name: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА: объявленное состояние вправду кто-то выставляет.

    Перечень берётся из КОНТРАКТА, и новая возможность с признаком engine_state
    попадёт сюда сама. Именно эта проверка поймала бы orders.events, жившую в
    начальном значении полторы недели.

    Проверяется присваивание в движке. Косвенно - да, но прямее нельзя: у
    возможности без операции нет вызова, который бы упал.

    Аргументы:
        name (str): Имя возможности по контракту.

    Возвращает:
        None
    """
    symbol = f"Capability.{name.replace('.', '_').upper()}"
    source = _engine_source()

    assert f"self._state.capabilities[{symbol}]" in source, (
        f"возможность {name} объявлена управляемой состоянием движка, а движок "
        f"её состояние не выставляет нигде. Значит она навсегда остаётся в "
        f"начальном значении и отвечает вызывающему уверенно и неверно"
    )


# --- Операции по счёту --------------------------------------------------------


def _complete_balance() -> str:
    """Собирает страницу счёта, прочитываемую ПОЛНОСТЬЮ.

    СНИМОК ПОЛНЫМ НЕ БЫВАЕТ, и это не поломка снимка. Во-первых, на нём двадцать
    пять операций и кнопка догрузки не спрятана - значит показаны не все.
    Во-вторых, настройки области вывода в скелете замаскированы подписью и как
    объект не разбираются.

    Оба различия правятся здесь нарочно и поимённо: без полного случая проверка
    ниже сравнивала бы degraded с degraded и молчала бы на любой ошибке.

    Возвращает:
        str: Разметка страницы.
    """
    html = _page(BALANCE)
    html = html.replace('data-data="T2142:acdops#1"', 'data-data="{}"')
    return html.replace(
        'class="btn btn-default dyn-table-continue"',
        'class="btn btn-default dyn-table-continue hidden"',
    )


def _transactions_after(html: str) -> CapabilityState:
    """Читает счёт и отдаёт состояние возможности операций.

    Аргументы:
        html (str): Разметка страницы счёта.

    Возвращает:
        CapabilityState: Состояние после чтения.
    """
    with Client(transport=_FakeFetcher([_observation(html)]), budget=Budget()) as client:
        client.account.balance()
        return client.engine._state.capabilities[Capability.ACCOUNT_TRANSACTIONS]


def test_transactions_are_supported_on_a_complete_page_with_rows() -> None:
    """Полностью прочитанная таблица со строками подтверждает возможность.

    Возвращает:
        None
    """
    page = parse_balance_page(_complete_balance(), WHEN)
    assert page.rows_total, "в собранной странице не осталось строк операций"

    assert _transactions_after(_complete_balance()) is CapabilityState.SUPPORTED


def test_transactions_are_degraded_when_more_rows_are_hidden_behind_a_button() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: показанная часть истории не выдаётся за всю.

    Площадка показывает двадцать пять последних операций и кнопку догрузки.
    Догрузка у нас не написана, и объявить прочитанное полной историей значило бы
    соврать вызывающему, который по ней сводит расчёты.

    Возвращает:
        None
    """
    page = parse_balance_page(_page(BALANCE), WHEN)
    assert page.reason == "more_rows_available", (
        f"снимок перестал быть примером недочитанной таблицы: {page.reason}"
    )

    assert _transactions_after(_page(BALANCE)) is CapabilityState.DEGRADED


def test_transactions_are_not_supported_when_the_table_is_empty() -> None:
    """Пустая таблица не выдаётся за работающую.

    Площадка вправе показать баланс и НЕ показать историю - например новому
    аккаунту. Свести их в одно состояние значило бы объявить историю работающей
    по факту баланса.

    Возвращает:
        None
    """
    tree = HTMLParser(_complete_balance())
    for node in tree.css(".tc-item"):
        node.decompose()
    empty = tree.html or ""
    assert "tc-item" not in empty, "строки операций не убрались"

    assert _transactions_after(empty) is not CapabilityState.SUPPORTED


def test_an_empty_table_is_never_read_as_complete() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: связь, на которой стоит вывод о возможности.

    Движок мерит возможность ОДНОЙ полнотой. До 01.09.2026 он мерил ещё и
    наличием строк, и та половина условия не решала ничего: разбор счёта
    объявляет пустую таблицу unknown, а не complete. Мутация это показала -
    снятие половины не уронило ни одной проверки.

    Условие сняли, а связь осталась: вывод «полно, значит история читается»
    верен лишь пока пустая таблица не бывает полной. Правило это живёт в другом
    модуле, и разойдись оно с движком - пустой счёт объявился бы работающим.

    Проверка стоит здесь, а не среди проверок разбора, потому что нужна она
    именно движку.

    Возвращает:
        None
    """
    tree = HTMLParser(_complete_balance())
    for node in tree.css(".tc-item"):
        node.decompose()

    page = parse_balance_page(tree.html or "", WHEN)

    assert page.rows_total == 0, "строки не убрались - проверка стала бессмысленной"
    assert page.completeness is not Completeness.COMPLETE, (
        "пустая таблица операций объявлена полной. Движок меряет возможность одной "
        "полнотой и теперь объявит работающей историю, которой нет"
    )


# --- События по заказам -------------------------------------------------------


def _watch(orders_html: str) -> CapabilityState:
    """Прокручивает один шаг наблюдения и отдаёт состояние возможности событий.

    Аргументы:
        orders_html (str): Разметка списка продаж.

    Возвращает:
        CapabilityState: Состояние возможности после шага.
    """
    responses: list[object] = [_observation(orders_html), _observation(_page(CHATS))]
    with Client(transport=_FakeFetcher(responses), budget=Budget()) as client:
        client.watch(Router(), max_iterations=1, schedule=Schedule())
        return client.engine._state.capabilities[Capability.ORDERS_EVENTS]


def test_order_events_are_supported_on_a_complete_read() -> None:
    """Полный список продаж объявляет события заслуживающими доверия.

    Возвращает:
        None
    """
    assert _watch(_page(ORDERS)) is CapabilityState.SUPPORTED


def test_order_events_are_degraded_on_an_incomplete_read() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: неполное чтение роняет доверие к событиям.

    Это не придирка. На неполном чтении курсор нарочно не сдвигается, чтобы
    выпавшие строки не сочли исчезнувшими, - а события по прочитанному всё равно
    порождаются. Вызывающий, принимающий их за полную картину, ошибается, и
    узнать об этом ему больше неоткуда: своего вызова у возможности нет.

    Возвращает:
        None
    """
    broken = _page(ORDERS).replace("tc-status", "tc-status-renamed")

    assert _watch(broken) is CapabilityState.DEGRADED


def test_the_watch_loop_does_not_leave_the_capability_unknown() -> None:
    """Прямая проверка того, ради чего запись реестра была снята.

    До 01.09.2026 цикл состояние не выставлял вовсе, и возможность оставалась в
    начальном значении навсегда. Проверка ловит возврат к этому.

    Возвращает:
        None
    """
    assert _watch(_page(ORDERS)) is not CapabilityState.UNKNOWN
