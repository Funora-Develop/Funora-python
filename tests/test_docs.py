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
from textwrap import dedent

import pytest

from funora import (
    Capability,
    CapabilityState,
    Client,
    Completeness,
    Confidence,
    EventType,
    OrderStatus,
    Origin,
    Presence,
    Severity,
)
from funora._chats import ChatsPage
from funora._orders import OrdersPage
from funora._thread import Thread

#: Корень репозитория.
ROOT = Path(__file__).resolve().parent.parent

#: Перечисления, чьи члены документация вправе называть по имени.
_ENUMS: dict[str, type] = {
    one.__name__: one
    for one in (
        Capability,
        CapabilityState,
        Completeness,
        Confidence,
        EventType,
        OrderStatus,
        Origin,
        Presence,
        Severity,
    )
}

#: Документы, которые проверяются.
DOCS = [
    ROOT / "README.md",
    ROOT / "README.en.md",
    *sorted((ROOT / "docs").rglob("*.md")),
    ROOT / "tests" / "fixtures" / "pages" / "README.md",
]

#: Блок кода на Python внутри разметки.
_CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

#: Ссылка в разметке.
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

#: Вызов операции: client.СЕРВИС.ОПЕРАЦИЯ(.
_CALL = re.compile(r"client\.(\w+)\.(\w+)\(")

#: Обращение к члену перечисления: ПЕРЕЧИСЛЕНИЕ.ЧЛЕН.
_ENUM_MEMBER = re.compile(r"\b([A-Z][A-Za-z]+)\.([A-Z][A-Z_0-9]+)\b")

#: Имя отказа в тексте.
_ERROR_NAME = re.compile(r"\b([A-Z][A-Za-z]*Error)\b")


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
            # Отступ снимается: у примера внутри блока-предупреждения или вкладки
            # он часть РАЗМЕТКИ, а не кода. Снимается общий отступ, поэтому
            # по-настоящему кривой отступ внутри примера проверку по-прежнему
            # роняет.
            ast.parse(dedent(block))
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
    """Проверяет, что КАЖДАЯ упомянутая документацией операция существует.

    Перечень берётся из самих документов, а не пишется рядом с проверкой.
    Написанный рядом перечень отстаёт молча: руководство обрастает главами, а
    проверка продолжает сверять те же три вызова, что и в день, когда её
    завели.

    Обещание, которое перестало выполняться, обнаруживается вызовом, а не
    чтением.

    Returns:
        None
    """
    client = Client(transport=object())  # type: ignore[arg-type]

    mentioned: set[tuple[str, str]] = set()
    for path in DOCS:
        for service, operation in _CALL.findall(path.read_text(encoding="utf-8")):
            mentioned.add((service, operation))

    assert mentioned, "в документации не нашлось ни одного вызова операции"

    missing = [
        f"client.{service}.{operation}"
        for service, operation in sorted(mentioned)
        if not callable(getattr(getattr(client, service, None), operation, None))
    ]
    assert not missing, (
        f"документация обещает операции, которых нет: {missing}. Обещание в "
        "руководстве опаснее метода в коде: метод найдут при ревью, обещание - "
        "нет, а поверят ему раньше"
    )


def test_documented_operations_exist_in_both_facades() -> None:
    """Проверяет, что обещанное есть и у асинхронного клиента.

    Руководство обещает, что перевод бота сводится к await. Обещание держится
    не само собой: операция, появившаяся в одном фасаде и не появившаяся в
    другом, обнаружится при переводе - то есть в худший момент.

    Returns:
        None
    """
    from funora._aclient import AsyncClient

    client = AsyncClient(transport=object())  # type: ignore[arg-type]

    mentioned: set[tuple[str, str]] = set()
    for path in DOCS:
        for service, operation in _CALL.findall(path.read_text(encoding="utf-8")):
            mentioned.add((service, operation))

    missing = [
        f"client.{service}.{operation}"
        for service, operation in sorted(mentioned)
        if not callable(getattr(getattr(client, service, None), operation, None))
    ]
    assert not missing, f"асинхронный фасад не обещает того же: {missing}"


def test_every_result_type_is_importable_from_the_package() -> None:
    """Требует, чтобы тип результата операции лежал в публичном пакете.

    Руководство пишет аннотации, а вызывающий их копирует. Тип, который
    операция уже возвращает, а импортировать его можно только из модуля с
    подчёркиванием, ставит вызывающего перед выбором из двух плохих: либо не
    аннотировать вовсе, либо опереться на то, о чём пакет прямо сказал «меняю
    молча».

    Перечень выводится из САМИХ операций, а не пишется рядом: написанный рядом
    отстанет на следующей операции и промолчит.

    Returns:
        None
    """
    import funora
    from funora import _client

    wanted: set[str] = set()
    for name in dir(_client):
        service = getattr(_client, name)
        if not (isinstance(service, type) and name.endswith("Service")):
            continue
        for member in vars(service):
            if member.startswith("_"):
                continue
            returns = getattr(service, member).__annotations__.get("return")
            if isinstance(returns, str):
                wanted.add(returns)

    assert wanted, "у сервисов не нашлось ни одного объявленного типа результата"

    # И то, чем результат перебирается: страница без своей записи бесполезна.
    element = re.compile(r"tuple\[(\w+), \.\.\.\]")
    for name in sorted(wanted):
        holder = getattr(funora, name, None)
        if holder is None:
            continue
        for member in vars(holder):
            if member.startswith("_"):
                continue
            returns = getattr(holder, member)
            annotation = getattr(returns, "__annotations__", {}).get("return")
            found = element.match(str(annotation))
            if found:
                wanted.add(found.group(1))

    # Сверяется ИМЕННО __all__, а не hasattr. Пакет проверяется mypy в строгом
    # режиме, а там повторный вывоз неявным не бывает: имя, не попавшее в
    # перечень, для проверяющего типы отсутствует, даже если импортируется.
    published = set(funora.__all__)
    missing = sorted(one for one in wanted if one not in published)
    assert not missing, f"операции возвращают типы, которых нет в публичном перечне: {missing}"


def test_the_readme_counts_the_operations_it_has() -> None:
    """Сверяет число операций в README с тем, сколько их у клиента.

    Число прозой протухает молча: операция добавляется, а «три операции
    чтения» остаётся стоять - и первый пришедший человек читает не о том SDK,
    который скачал. В этом наборе такое уже случалось.

    Считаются методы сервисов, а не строки таблицы: таблицу проверяет
    test_documented_operations_exist, и там каждая строка ищется на клиенте.

    Returns:
        None
    """
    from funora import _client
    from funora.operations import OPERATIONS, Safety

    methods = 0
    for name in dir(_client):
        service = getattr(_client, name)
        if isinstance(service, type) and name.endswith("Service"):
            methods += sum(1 for one in vars(service) if not one.startswith("_"))

    unsafe = {
        one.name.split(".")[-1] for one in OPERATIONS.values() if one.safety is not Safety.SAFE
    }
    writes = 0
    for name in dir(_client):
        service = getattr(_client, name)
        if isinstance(service, type) and name.endswith("Service"):
            writes += sum(1 for one in vars(service) if one in unsafe)

    words = {
        3: ("три", "three"),
        12: ("двенадцать", "twelve"),
        13: ("тринадцать", "thirteen"),
        14: ("четырнадцать", "fourteen"),
        15: ("пятнадцать", "fifteen"),
        16: ("шестнадцать", "sixteen"),
        17: ("семнадцать", "seventeen"),
        18: ("восемнадцать", "eighteen"),
        19: ("девятнадцать", "nineteen"),
        20: ("двадцать", "twenty"),
        21: ("двадцать одна", "twenty-one"),
    }
    assert methods in words, f"операций {methods}, а числительного для них нет"
    assert methods - writes in words, f"чтений {methods - writes}, числительного нет"

    russian = (ROOT / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "README.en.md").read_text(encoding="utf-8")

    assert words[methods][0] in russian, f"README.md не называет числа операций: их {methods}"
    assert words[methods][1] in english.lower(), (
        f"README.en.md не называет числа операций: их {methods}"
    )
    assert words[methods - writes][0] in russian, (
        f"README.md не называет числа чтений: их {methods - writes}"
    )
    assert words[methods - writes][1] in english.lower(), (
        f"README.en.md не называет числа чтений: их {methods - writes}"
    )


def test_the_public_list_has_no_duplicates() -> None:
    """Требует, чтобы имя в __all__ стояло один раз.

    Повтор безвреден для работы и вреден для чтения: перечень - это и есть
    ответ на вопрос «что тут публичное», а повторённое имя заставляет
    сомневаться, не разные ли это вещи.

    Returns:
        None
    """
    import funora

    seen: dict[str, int] = {}
    for name in funora.__all__:
        seen[name] = seen.get(name, 0) + 1

    twice = sorted(name for name, count in seen.items() if count > 1)
    assert not twice, f"имена в __all__ повторены: {twice}"

    unresolved = sorted(one for one in funora.__all__ if not hasattr(funora, one))
    assert not unresolved, f"в __all__ есть имена, которых в пакете нет: {unresolved}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_named_enum_members_exist(path: Path) -> None:
    """Проверяет, что названные перечисления и их члены существуют.

    Имя члена перечисления в тексте - такое же обещание, как вызов. Оно к тому
    же разбирается интерпретатором прекрасно: пример с CapabilityState.AVAILABLE
    вместо SUPPORTED выглядит убедительно, компилируется и не работает.

    Args:
        path (Path): Проверяемый документ.

    Returns:
        None
    """
    text = path.read_text(encoding="utf-8")

    wrong: list[str] = []
    for holder, member in _ENUM_MEMBER.findall(text):
        enum = _ENUMS.get(holder)
        if enum is None:
            continue
        if member not in enum.__members__:
            wrong.append(f"{holder}.{member}")

    assert not wrong, f"{path.name}: названы члены перечислений, которых нет: {sorted(set(wrong))}"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_named_errors_exist(path: Path) -> None:
    """Проверяет, что названные документом отказы существуют.

    Таблица ошибок - обещание вызывающему, что перехват по имени сработает.
    Имя, которого нет, он узнает при отладке падения, то есть в тот момент,
    когда ему меньше всего до этого дела.

    Args:
        path (Path): Проверяемый документ.

    Returns:
        None
    """
    import funora
    from funora import errors

    # Ищется в ДВУХ местах, и это не послабление. Модуль errors порождается из
    # контракта, а SecretNotFoundError - подкласс порождённого отказа, живущий
    # рядом с провайдерами секретов. Публичный путь к нему - сам пакет, и
    # документация вправе называть его наравне с остальными.
    named = {one for one in _ERROR_NAME.findall(path.read_text(encoding="utf-8"))}
    missing = sorted(one for one in named if not (hasattr(errors, one) or hasattr(funora, one)))

    assert not missing, f"{path.name}: названы отказы, которых нет: {missing}"


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

    # Совпадения имён и типов НЕ ДОСТАТОЧНО, и это выяснилось дорогой ценой.
    # AsyncClient.watch принимал on_handler_error и не передавал его дальше: у
    # синхронного клиента отказ обработчика доходил до вызывающего, у
    # асинхронного пропадал молча. Обе подписи при этом совпадали до знака, и
    # проверка выше была довольна.
    #
    # Поэтому сверяются и ИМЕНА АРГУМЕНТОВ, и то, что каждый из них поминается
    # в теле. Второе грубо, но ловит ровно эту болезнь: принять и выбросить.
    import inspect

    for name in sorted(surface):
        plain_method = getattr(Client, name)
        other_method = getattr(AsyncClient, name)
        if isinstance(plain_method, property) or not callable(plain_method):
            continue

        here = set(inspect.signature(plain_method).parameters)
        there = set(inspect.signature(other_method).parameters)
        assert here <= there, f"у асинхронного {name} нет аргументов {sorted(here - there)}"

        body = inspect.getsource(other_method)
        head = body[: body.index('"""')] if '"""' in body else body
        tail = body[body.index('"""', body.index('"""') + 3) :] if '"""' in body else body
        forgotten = [one for one in here if one not in {"self"} and one not in tail and one in head]
        assert not forgotten, (
            f"асинхронный {name} принимает {forgotten} и не поминает их в теле: "
            "аргумент принят и выброшен, а подпись обещает обратное"
        )

    # СОЗДАНИЕ КЛИЕНТА СВЕРЯЕТСЯ ОТДЕЛЬНО, и выяснилось это тоже дорогой ценой.
    # Перебор выше идёт по vars(), а __init__ начинается с подчёркивания и в
    # перебор не попадал. Асинхронный клиент из-за этого не принимал state_path
    # вовсе - то есть у него не было долговечного реестра НИКОГДА, а отправка
    # без реестра отказывает. Асинхронный фасад не мог отправить ни одного
    # сообщения, и обе проверки выше были довольны.
    made_here = set(inspect.signature(Client.__init__).parameters)
    made_there = set(inspect.signature(AsyncClient.__init__).parameters)
    assert made_here == made_there, (
        f"клиенты создаются по-разному: {sorted(made_here ^ made_there)}. "
        "Настройка, которой нет у одного из них, - это отказ операции, "
        "работающей у другого"
    )

    passed = inspect.getsource(AsyncClient.__init__)
    for name in sorted(made_there - {"self"}):
        assert passed.count(name) >= 2, (
            f"асинхронный клиент принимает {name} и никуда его не передаёт"
        )


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


def test_documented_list_of_produced_events_matches_reality() -> None:
    """Проверяет, что перечень порождаемого в документации не отстал.

    Документ, отставший от кода, врёт убедительнее кода: читатель верит тексту и
    не идёт смотреть исходники. Так и вышло - architecture.md утверждал, что
    событий об изменении состояния заказа не бывает вовсе, и двумя абзацами ниже
    перечислял их среди порождаемых.

    Проверка ищет строку «Что порождается сегодня» и сверяет перечисленные там
    имена с PRODUCIBLE.

    Returns:
        None
    """
    from funora._watch import PRODUCIBLE

    text = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    marker = "Что порождается сегодня"
    start = text.find(marker)
    assert start != -1, f"в architecture.md нет строки {marker!r} - проверять нечего"

    # Абзац: до первой пустой строки после маркера.
    end = text.find(chr(10) * 2, start)
    paragraph = text[start : end if end != -1 else len(text)]

    named = set(re.findall(r"`([a-z]+\.[a-z_]+)`", paragraph))
    produced = {str(kind) for kind in PRODUCIBLE}

    assert named == produced, (
        f"документация называет {sorted(named)}, реализация порождает {sorted(produced)}"
    )
