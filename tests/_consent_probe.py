"""Зовёт заимствованную операцию записи БЕЗ согласия и даёт отказу выйти.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Сквозная проверка правила о согласии берёт перечень
операций из контракта, а позвать их надо по-настоящему: проверка, читающая
только объявление, не заметит операции, у которой объявление есть, а
предохранителя в коде нет.

ТАБЛИЦА ЗДЕСЬ НАРОЧНО НЕПОЛНАЯ ПО УСТРОЙСТВУ. Новая заимствованная запись в неё
не попадёт сама, и проверка упадёт с прямым указанием дописать - то есть автор
новой операции обязан будет ЗАДУМАТЬСЯ о согласии, а не обойти его молчанием.

Доводы подставляются заведомо пригодные: отказ обязан случиться ДО сети, и до
сети дело не дойдёт вовсе.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any, Final

from funora._budget import Budget
from funora._engine import Engine
from funora._transport import TransportSettings

#: Как позвать каждую заимствованную операцию записи.
#:
#: Доводы годные: проверяется отказ по СОГЛАСИЮ, а не по разбору доводов.
#: Отказ обязан случиться раньше всего прочего.
_CALLS: Final[dict[str, Callable[[Engine], Generator[Any, Any, Any]]]] = {
    "lots.activate": lambda e: e.set_lot_visible(
        "1908", "75289502", visible=True, expected_revision="deadbeefdeadbeef"
    ),
    "lots.deactivate": lambda e: e.set_lot_visible(
        "1908", "75289502", visible=False, expected_revision="deadbeefdeadbeef"
    ),
    "chats.mark_read": lambda e: e.mark_chat_read("247450736"),
    "reviews.leave": lambda e: e.leave_review("ZVVQ8FKP", rating=5, text="спасибо"),
    "reviews.remove": lambda e: e.remove_review("ZVVQ8FKP"),
    "account.switch_currency": lambda e: e.switch_currency("USD"),
    "orders.refund": lambda e: e.refund_order("ZVVQ8FKP"),
}


def call_without_consent(name: str) -> None:
    """Зовёт операцию без согласия и даёт отказу выйти наружу.

    Аргументы:
        name (str): Имя операции по контракту.

    Возвращает:
        None

    Raises:
        AssertionError: Если операции нет в таблице - значит новая
            заимствованная запись заведена, а о согласии не подумали.
        UsageError: Ожидаемый отказ. Ради него всё и делается.
    """
    call = _CALLS.get(name)
    assert call is not None, (
        f"операция {name} объявлена заимствованной записью, но позвать её "
        "проверка не умеет. Допишите вызов в tests/_consent_probe.py - и заодно "
        "убедитесь, что предохранитель согласия у неё вправду есть"
    )

    # Согласия не даём ВОВСЕ: перечень включённых пуст по умолчанию.
    engine = Engine(TransportSettings(), Budget())
    core = call(engine)
    # Первый шаг и обязан отказать: до сети дело не дойдёт.
    core.send(None)
