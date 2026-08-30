"""Сверка возвращаемых типов со схемами моделей.

Набор появился после того, как выяснилось: в спецификации девять моделей, и
восьми из них в реализации не соответствует ничего, а девятая - Message -
расходится по составу полей и по смыслу одного из них. При этом
spec/services/chats.yaml объявлял, что chats.list возвращает Chat[], а собрать
Chat из наблюдённой разметки нельзя: у собеседника нет идентификатора, а числа
непрочитанных страница не даёт.

Реализация, читающая только спецификацию, вынуждена была выдумать и то и другое.
Ровно ту же ошибку для заказов заметили раньше и исправили руками - заменили
Order[] на OrderListEntry[], - но проверки, которая не даст ей вернуться, не
завели.

Здесь она и заводится. Схемы описывают то, что первая реализация вправду
возвращает, а набор сверяет с ними разобранные снимки: поле, добавленное в
запись и не описанное схемой, роняет проверку, и наоборот.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from _schema_check import check

from funora._chats import parse_chats_page
from funora._observed import Observed
from funora._orders import parse_orders_page
from funora._thread import parse_thread

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _spec_dir() -> Path | None:
    """Находит рабочую копию спецификации, если она задана.

    Returns:
        Path | None: Каталог репозитория Funora-spec либо None.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / "spec" / "models" / "observed.schema.json").is_file() else None


#: Причина пропуска, общая для набора.
SKIP_REASON = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(_spec_dir() is None, reason=SKIP_REASON)


def _schema(name: str) -> dict[str, Any]:
    """Читает схему модели.

    Args:
        name (str): Имя файла без расширения.

    Returns:
        dict[str, Any]: Схема.
    """
    root = _spec_dir()
    assert root is not None
    return json.loads(
        (root / "spec" / "models" / f"{name}.schema.json").read_text(encoding="utf-8")
    )


def _page(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _as_json(value: Any) -> Any:
    """Переводит значение реализации в вид, описанный схемой.

    Обёртка наблюдения раскладывается в объект: спецификация описывает её
    объектом, потому что три состояния должны быть выражены и в тех языках, где
    обобщённого типа нет.

    Args:
        value (Any): Значение поля.

    Returns:
        Any: Представление, пригодное для сверки со схемой.
    """
    if isinstance(value, Observed):
        return {
            "presence": str(value.presence),
            "confidence": str(value.confidence) if value.confidence is not None else None,
            "reason": value.reason,
            "value": _as_json(value.or_none()),
        }
    if isinstance(value, tuple | list):
        return [_as_json(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _as_json(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool | int | str) or value is None:
        return str(value) if not isinstance(value, bool | int | str) else value
    return str(value)


def _page_as_json(page: Any, rows_key: str, rows: tuple[Any, ...]) -> dict[str, Any]:
    """Раскладывает результат чтения страницы в вид схемы.

    Записи отдаются методом, а не полем, поэтому собираются отдельно: открытый
    список делает неполноту незаметной, и схема описывает именно то, что метод
    отдаёт по явной просьбе.

    Args:
        page (Any): Результат чтения.
        rows_key (str): Имя поля с записями в схеме.
        rows (tuple[Any, ...]): Записи.

    Returns:
        dict[str, Any]: Представление страницы.
    """
    body = {
        field.name: _as_json(getattr(page, field.name))
        for field in dataclasses.fields(page)
        if not field.name.startswith("_")
    }
    body[rows_key] = [_as_json(row) for row in rows]
    return body


def test_order_list_entry_matches_its_schema() -> None:
    """Сверяет запись заказа со схемой.

    Returns:
        None
    """
    schema = _schema("order-list-entry")
    page = parse_orders_page(_page("orders-trade.logged.ru"), observed_at=WHEN)
    rows = page.rows(accept_incomplete=True)
    assert len(rows) > 2, "снимок обязан дать больше двух записей"

    for entry in rows:
        check(_as_json(entry), schema, where=f"заказ {entry.order_id}")


def test_chat_list_entry_matches_its_schema() -> None:
    """Сверяет запись диалога со схемой.

    Returns:
        None
    """
    schema = _schema("chat-list-entry")
    page = parse_chats_page(_page("chat.logged.ru"), observed_at=WHEN)
    rows = page.rows(accept_incomplete=True)
    assert len(rows) > 2

    for entry in rows:
        check(_as_json(entry), schema, where=f"диалог {entry.node_id}")


def test_thread_message_matches_its_schema() -> None:
    """Сверяет сообщение переписки со схемой.

    Returns:
        None
    """
    schema = _schema("thread-message")
    thread = parse_thread(_page("chat-thread.logged.ru"), observed_at=WHEN)
    messages = thread.messages(accept_incomplete=True)
    assert len(messages) > 2

    for message in messages:
        check(_as_json(message), schema, where=f"сообщение {message.row_index}")


def test_pages_match_their_schemas() -> None:
    """Сверяет результаты чтения страниц со схемами.

    Returns:
        None
    """
    orders = parse_orders_page(_page("orders-trade.logged.ru"), observed_at=WHEN)
    chats = parse_chats_page(_page("chat.logged.ru"), observed_at=WHEN)
    thread = parse_thread(_page("chat-thread.logged.ru"), observed_at=WHEN)

    check(
        _page_as_json(orders, "entries", orders.rows(accept_incomplete=True)),
        _schema("orders-page"),
        where="страница заказов",
    )
    check(
        _page_as_json(chats, "entries", chats.rows(accept_incomplete=True)),
        _schema("chats-page"),
        where="страница диалогов",
    )
    check(
        _page_as_json(thread, "messages", thread.messages(accept_incomplete=True)),
        _schema("thread"),
        where="переписка",
    )


def test_observed_envelope_matches_its_schema() -> None:
    """Сверяет обёртку наблюдения во всех трёх состояниях.

    Обёртка нормативна: реализация, отдающая голое значение, отбирает у
    вызывающего единственный способ отличить «поле пусто» от «поле не читали».
    Проверяются все три состояния, а не только встретившиеся на снимке.

    Returns:
        None
    """
    from funora._observed import Confidence

    schema = _schema("observed")
    for sample in (
        Observed.present("текст"),
        Observed.present(True),
        Observed.present("выведено", Confidence.INFERRED),
        Observed.empty(""),
        Observed.missing("selector_no_match:field"),
    ):
        check(_as_json(sample), schema, where=f"наблюдение {sample.presence}")


def test_every_returned_field_is_described() -> None:
    """Проверяет, что схема описывает все поля записи, и наоборот.

    Сверка выше проходит по снимку и потому слепа к полю, которого на снимке не
    случилось. Здесь сравниваются множества имён - объявление против объявления.

    Returns:
        None
    """
    from funora._chats import ChatListEntry
    from funora._orders import OrderListEntry
    from funora._own_lots import OwnLot
    from funora._runner import SendResult
    from funora._thread import Message

    pairs = (
        (OrderListEntry, "order-list-entry"),
        (ChatListEntry, "chat-list-entry"),
        (Message, "thread-message"),
        (SendResult, "send-result"),
        (OwnLot, "own-lot"),
    )
    for cls, name in pairs:
        declared = set(_schema(name).get("properties", {}))
        actual = {f.name for f in dataclasses.fields(cls) if not f.name.startswith("_")}
        assert actual == declared, (
            f"{cls.__name__}: реализация отдаёт {sorted(actual - declared)}, "
            f"схема требует {sorted(declared - actual)}"
        )


def test_unbuildable_models_say_so() -> None:
    """Проверяет, что неосуществимая модель об этом предупреждает.

    Схема без реализации описывает намерение, а читается как контракт. Второй
    SDK, написанный по такой схеме, выдумает то, чего никто не наблюдал - ровно
    так chats.list и обещал Chat[] с идентификатором собеседника, которого в
    разметке нет.

    Returns:
        None
    """
    root = _spec_dir()
    assert root is not None

    buildable = {
        "observed",
        "defect",
        "money",
        "order-list-entry",
        "chat-list-entry",
        "thread-message",
        "orders-page",
        "chats-page",
        "thread",
        # Служба отзывов написана в 0.8.0 по снимку профиля, который уже
        # лежал в проекте. Обе модели собираются разбором целиком.
        "review",
        "reviews-page",
        # Страница одного заказа читается с 0.10.0. Order с неё по-прежнему
        # не собирается: сторон она не разделяет, кода валюты не даёт.
        "order-view",
        # Страница баланса читается с 0.10.0: балансы и операции по счёту.
        "balance",
        "balance-page",
        "transaction",
        # Витрина читается с 0.11.0: публичный вид предложений продавца.
        "showcase-offer",
        "showcase-page",
        "showcase-section",
        # Каталог читается с 0.13.0: игры, их варианты и разделы каждого.
        "catalog-game",
        "catalog-page",
        "catalog-section",
        # Чтение аккаунта, проверка сессии и профиль возможностей - с 0.14.0.
        "session-health",
        "capability-profile",
        # Собственные лоты продавца - с 30.08.2026. Модель своя, а не Lot: та
        # требует is_active обязательным, а признака показа лота в выдаче на
        # странице нет ни одного.
        "own-lot",
        "own-lots-page",
        # Квитанция отправки - с 30.08.2026, первая модель ЗАПИСИ в проекте.
        #
        # Она заведена взамен Message, которая помечена неосуществимой и сама
        # говорит почему: два её обязательных поля из ответа канала не
        # заполняются вовсе. В квитанции только заполнимое из наблюдённого.
        "send-result",
    }
    for path in sorted((root / "spec" / "models").glob("*.schema.json")):
        name = path.name.removesuffix(".schema.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        if name in buildable:
            assert not doc.get("x-funora-not-implemented"), (
                f"{name}: модель помечена неосуществимой, а реализация её собирает"
            )
            continue
        assert doc.get("x-funora-not-implemented") is True, (
            f"{name}: модель не собирается ни одной реализацией и об этом не предупреждает"
        )
