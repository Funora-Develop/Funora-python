"""Проверки документации.

Документация ломается тише кода: пример в README перестаёт работать, ссылка
начинает вести в никуда, и узнаёт об этом первый пришедший человек, а не
сборка. Здесь проверяется то, что можно проверить машинно.

Набор намеренно не проверяет содержание. Он проверяет, что примеры остаются
исполнимыми, ссылки - разрешимыми, а обещания об операциях - выполнимыми.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from funora import Client
from funora._chats import ChatsPage
from funora._orders import OrdersPage
from funora._thread import Thread

#: Корень репозитория.
ROOT = Path(__file__).resolve().parent.parent

#: Документы, которые проверяются.
DOCS = [
    ROOT / "README.md",
    ROOT / "README.en.md",
    *sorted((ROOT / "docs").glob("*.md")),
    ROOT / "tests" / "fixtures" / "pages" / "README.md",
]

#: Блок кода на Python внутри разметки.
_CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

#: Ссылка в разметке.
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_python_examples_are_syntactically_valid(path: Path) -> None:
    """Проверяет, что примеры на Python разбираются интерпретатором.

    Пример с опечаткой выглядит убедительно и не работает. Разбор ловит это,
    ничего не выполняя.

    Args:
        path (Path): Проверяемый документ.

    Returns:
        None
    """
    for index, block in enumerate(_CODE_BLOCK.findall(path.read_text(encoding="utf-8"))):
        try:
            ast.parse(block)
        except SyntaxError as exc:  # pragma: no cover - сообщение важнее ветки
            pytest.fail(f"{path.name}, блок {index + 1}: {exc}")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    """Проверяет, что ссылки на файлы репозитория ведут к существующим файлам.

    Ссылка, ведущая в никуда, встречает первого пришедшего человека и говорит
    ему о проекте больше, чем хотелось бы.

    Args:
        path (Path): Проверяемый документ.

    Returns:
        None
    """
    broken: list[str] = []
    for target in _LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        resolved = (path.parent / target.split("#", 1)[0]).resolve()
        if not resolved.exists():
            broken.append(target)

    assert not broken, f"{path.name}: ссылки ведут в никуда: {broken}"


def test_documented_operations_exist() -> None:
    """Проверяет, что обещанные в README операции действительно есть.

    Таблица операций - обещание. Обещание, которое перестало выполняться,
    обнаруживается вызовом, а не чтением.

    Returns:
        None
    """
    client = Client(transport=object())  # type: ignore[arg-type]

    assert callable(client.orders.list)
    assert callable(client.chats.list)
    assert callable(client.chats.thread)


def test_documented_return_types_match() -> None:
    """Проверяет, что операции возвращают обещанные типы.

    Проверка идёт по объявлению, а не по вызову: вызывать здесь нечего, сети в
    наборе нет.

    Returns:
        None
    """
    from funora._client import ChatsService, OrdersService

    assert OrdersService.list.__annotations__["return"] == "OrdersPage"
    assert ChatsService.list.__annotations__["return"] == "ChatsPage"
    assert ChatsService.thread.__annotations__["return"] == "Thread"

    for cls in (OrdersPage, ChatsPage, Thread):
        assert hasattr(cls, "completeness")


def test_readme_names_the_same_operations_in_both_languages() -> None:
    """Проверяет, что русская и английская версии обещают одно и то же.

    Разошедшиеся переводы - обычное дело, и обычно расходятся они как раз в
    перечне того, что работает.

    Returns:
        None
    """
    calls = re.compile(r"client\.\w+\.\w+\(")

    ru = set(calls.findall((ROOT / "README.md").read_text(encoding="utf-8")))
    en = set(calls.findall((ROOT / "README.en.md").read_text(encoding="utf-8")))

    assert ru == en, f"версии README обещают разное: только в одной {ru ^ en}"


def test_readme_does_not_promise_payment_confirmation() -> None:
    """Проверяет, что README не обещает ответа на вопрос об оплате.

    Обещание в документации опаснее метода в коде: метод найдут при ревью,
    обещание - нет, а поверят ему раньше.

    Returns:
        None
    """
    for name in ("README.md", "README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for phrase in ("is_paid", "ispaid", "payment_confirmed", "оплата подтверждена"):
            assert phrase not in text, f"{name} обещает {phrase}"


def test_async_facade_mirrors_the_sync_one() -> None:
    """Проверяет, что асинхронный фасад обещает ровно то же, что синхронный.

    Два фасада над одним ядром - это обещание вызывающему: перевести бота на
    асинхронный клиент можно, дописав await. Обещание держится не само собой.
    Появись операция в одном фасаде и не появись в другом - вызывающий узнает об
    этом при переводе, то есть в худший момент.

    Сверяются имена операций и объявленные типы результата. Тела не сверяются:
    они и должны отличаться, в этом весь смысл двух фасадов.

    Returns:
        None
    """
    from funora._aclient import AsyncChatsService, AsyncClient, AsyncOrdersService
    from funora._client import ChatsService, Client, OrdersService

    pairs = ((OrdersService, AsyncOrdersService), (ChatsService, AsyncChatsService))
    for plain, other in pairs:
        here = {n for n in vars(plain) if not n.startswith("_")}
        there = {n for n in vars(other) if not n.startswith("_")}
        assert here == there, (
            f"фасады {plain.__name__} и {other.__name__} разошлись: {here ^ there}"
        )
        for name in sorted(here):
            expected = getattr(plain, name).__annotations__["return"]
            actual = getattr(other, name).__annotations__["return"]
            assert expected == actual, f"{name} обещает {expected} и {actual}"

    surface = {n for n in vars(Client) if not n.startswith("_")}
    other_surface = {n for n in vars(AsyncClient) if not n.startswith("_")}
    assert surface == other_surface, f"клиенты разошлись: {surface ^ other_surface}"


def test_documented_subscriptions_are_producible() -> None:
    """Проверяет, что примеры не подписываются на непорождаемое событие.

    Подписка на вид, которого реализация не порождает, отвергается при
    регистрации. Пример в документации, делающий так, учит вызову, который
    падает при запуске, - и это худший род устаревшей документации: он выглядит
    рабочим ровно до первого запуска.

    Проверка синтаксиса такой пример пропускает: он разбирается прекрасно.

    Returns:
        None
    """
    from funora._watch import PRODUCIBLE
    from funora.events import EventType

    named: list[tuple[str, str]] = []
    for path in DOCS:
        if not path.exists():
            continue
        for block in _CODE_BLOCK.findall(path.read_text(encoding="utf-8")):
            for call in re.findall(r"\.on\(\s*EventType\.([A-Z_]+)", block):
                named.append((path.name, call))

    assert named, "в документации нет ни одного примера подписки - проверять нечего"

    producible = {kind.name for kind in PRODUCIBLE}
    for where, name in named:
        assert name in EventType.__members__, (
            f"{where}: примера ради названо EventType.{name}, а такого вида нет"
        )
        assert name in producible, (
            f"{where}: пример подписывается на EventType.{name}, "
            "а реализация такого вида не порождает - вызов будет отвергнут"
        )
