"""Проверяет контракт ошибок операций.

Спецификация объявляет на каждую операцию перечень ошибок, которыми та вправе
завершиться. Это ровно то, что вызывающий выписывает в ``except``, - и до
недавнего времени перечень не доходил до пакета вовсе: генератор принимал ключ
``errors`` и молча его выбрасывал.

Отсюда две проверки. Первая - что объявленное имя вообще существует: опечатка в
устойчивом идентификаторе даёт перечень, который выглядит контрактом и не
описывает ничего. Вторая - что вызывающий, поймавший объявленное, поймает
всё: ошибка, возбуждаемая операцией и не объявленная у неё, проходит мимо
``except``, выписанного по контракту, - и делает это на негативной ветке, где
расхождение обнаруживается позже всего.
"""

from __future__ import annotations

import pytest

from funora import errors as errors_module
from funora.errors import ERROR_BY_STABLE_ID
from funora.operations import OPERATIONS

#: Операции, которые эталонная реализация выполняет по-настоящему.
#:
#: Только для них можно сверять объявленное с возбуждаемым: у остальных нет
#: кода, который что-либо возбуждал бы, и сверка была бы пустой.
IMPLEMENTED_OPERATIONS: tuple[str, ...] = (
    "orders.list",
    "chats.list",
    "chats.history",
)


def test_every_operation_declares_its_errors() -> None:
    """Проверяет, что каждая операция объявляет перечень ошибок.

    Пустой перечень допустим и означает «эта операция не отказывает никогда».
    Отсутствие поля недопустимо: оно неотличимо от забывчивости, а вызывающему
    в обоих случаях нечего написать в except.

    Returns:
        None
    """
    for name, operation in OPERATIONS.items():
        assert isinstance(operation.errors, tuple), f"операция {name} не объявляет перечень ошибок"


def test_declared_errors_exist() -> None:
    """Проверяет, что объявленный идентификатор ошибки существует.

    Опечатка в устойчивом идентификаторе даёт перечень, который выглядит
    контрактом и не описывает ничего: вызывающий выпишет except по имени,
    которого нет ни у одного класса.

    Returns:
        None
    """
    unknown: list[str] = []
    for name, operation in OPERATIONS.items():
        for code in operation.errors:
            if code not in ERROR_BY_STABLE_ID:
                unknown.append(f"{name} -> {code}")

    assert not unknown, (
        f"операции объявляют ошибки, которых нет среди классов: {sorted(unknown)}. "
        "Устойчивый идентификатор - это то, по чему вызывающий ловит; "
        "несуществующий не поймает ничего"
    )


@pytest.mark.parametrize("name", IMPLEMENTED_OPERATIONS)
def test_declared_errors_are_catchable_together(name: str) -> None:
    """Проверяет, что объявленное ловится одним except.

    Вызывающий пишет ``except (A, B, C)`` по перечню из контракта. Проверка
    строит этот кортеж из объявленных идентификаторов и убеждается, что он
    собирается: каждый элемент - класс исключения, а не что-нибудь ещё.

    Args:
        name (str): Идентификатор операции.

    Returns:
        None
    """
    caught = tuple(ERROR_BY_STABLE_ID[code] for code in OPERATIONS[name].errors)
    assert caught, f"операция {name} не объявляет ни одной ошибки"
    for cls in caught:
        assert isinstance(cls, type) and issubclass(cls, BaseException), (
            f"{name}: {cls!r} не является классом исключения"
        )


def test_unexpected_response_is_declared_where_it_happens() -> None:
    """Проверяет, что классификатор не возбуждает необъявленного.

    Таблица вердиктов переводит 4xx на любой странице в
    funora.protocol.unexpected_response. Этот класс - СЕСТРА protocol.changed
    под общим ProtocolError, а не его потомок: except, выписанный по прежнему
    перечню из трёх ошибок, её не ловил.

    Перечень был исправлен; проверка не даёт исправлению откатиться.

    Returns:
        None
    """
    stable_id = "funora.protocol.unexpected_response"
    assert stable_id in ERROR_BY_STABLE_ID, "класс ошибки исчез из спецификации"

    for name in IMPLEMENTED_OPERATIONS:
        assert stable_id in OPERATIONS[name].errors, (
            f"операция {name} читает страницу через классификатор, который "
            f"возбуждает {stable_id}, но у неё это не объявлено. Вызывающий "
            "выпишет except по контракту и не поймает"
        )


def test_declared_errors_do_not_promise_the_impossible() -> None:
    """Проверяет, что операция не обещает ошибок, которых не бывает.

    Обратная половина предыдущей. Перечень, обещающий лишнее, заставляет
    вызывающего писать ветку, которая не исполнится никогда, - и та тихо
    протухает вместе с предположениями, на которых написана.

    Проверка узкая намеренно: она смотрит только на классы, которых нет в
    пакете вовсе.

    Returns:
        None
    """
    absent: list[str] = []
    for name, operation in OPERATIONS.items():
        for code in operation.errors:
            cls = ERROR_BY_STABLE_ID.get(code)
            if cls is None or not hasattr(errors_module, cls.__name__):
                absent.append(f"{name} -> {code}")

    assert not absent, f"обещаны ошибки, которых нет в пакете: {sorted(absent)}"
