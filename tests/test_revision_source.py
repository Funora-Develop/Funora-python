"""Проверяет, что версия события берётся из объявленного источника.

Версия входит в отпечаток, а отпечаток - идентичность события: на нём стоят
гашение повторов и ключи идемпотентности. Контракт объявляет для каждого вида,
что именно служит версией, и требует брать объявленное, а не выводить
самостоятельно.

Проверялось до сих пор ПОКРЫТИЕ: у каждого вида объявлен источник, и у каждого
источника есть вид. Берёт ли реализация вправду объявленное - не проверял никто.

Проверка здесь поведенческая и двусторонняя. Меняется объявленное - отпечаток
обязан перемениться; меняется соседнее - обязан остаться. Одной половины мало:
реализация, кладущая в версию всю строку целиком, первую половину проходит и
порождает событие о смене статуса всякий раз, когда правят описание лота.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from funora._chats import parse_chats_page
from funora._diff import diff_chats, diff_orders
from funora._orders import parse_orders_page
from funora.events import EventType

#: Каталог со снимками страниц.
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Момент наблюдения. Один на все прогоны: в версию он входить не должен.
WHEN = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

#: Идентификатор аккаунта для отпечатков.
ACCOUNT = "12345678"

#: Что кладётся в курсор, чтобы всякая строка выглядела изменившейся.
OTHER = "было-иначе"

#: Где лежит рабочая копия спецификации.
SPEC_DIR = os.environ.get("FUNORA_SPEC_DIR")


def _swapped(name: str, *pairs: tuple[str, str]) -> str:
    """Читает снимок и подменяет в нём названные куски.

    Подмена текстовая и до разбора: снимок остаётся неприкосновенным, а проверке
    нужна пара страниц, различающихся ровно одним.

    Args:
        name (str): Имя снимка без расширения.
        *pairs (tuple[str, str]): Пары «что заменить» и «на что». Каждая обязана
            найтись, иначе проверка сравнивала бы страницу саму с собой.

    Returns:
        str: Разметка с подменами.
    """
    html = (PAGES / f"{name}.skeleton.txt").read_text(encoding="utf-8")
    for old, new in pairs:
        assert old in html, f"в снимке нет {old!r} - проверка сравнивала бы одно и то же"
        html = html.replace(old, new, 1)
    return html


def _order_revisions(html: str) -> list[str]:
    """Возвращает отпечатки событий о смене состояния заказа.

    Args:
        html (str): Разметка списка заказов.

    Returns:
        list[str]: Отпечатки в порядке порождения.
    """
    page = parse_orders_page(html, observed_at=WHEN)
    known = {one.order_id: OTHER for one in page.rows(accept_incomplete=True)}
    return [
        one.id
        for one in diff_orders(known, page, account_id=ACCOUNT)
        if one.type is EventType.ORDER_STATUS_CHANGED
    ]


def _chat_revisions(html: str) -> list[str]:
    """Возвращает отпечатки событий об изменении диалога.

    Args:
        html (str): Разметка списка диалогов.

    Returns:
        list[str]: Отпечатки в порядке порождения.
    """
    page = parse_chats_page(html, observed_at=WHEN)
    known = {one.node_id: OTHER for one in page.rows(accept_incomplete=True)}
    return [
        one.id
        for one in diff_chats(known, page, account_id=ACCOUNT)
        if one.type is EventType.CHAT_UNREAD_CHANGED
    ]


def test_the_declared_source_moves_the_order_revision() -> None:
    """Меняет объявленный источник: состояние заказа.

    Меняются ОБА носителя состояния сразу. Один даёт не смену статуса, а
    расхождение носителей: состояние объявляется ненаблюдённым, события не
    возникает вовсе, и проверка сравнивала бы отсутствие с отсутствием.

    Returns:
        None
    """
    base = _order_revisions(_swapped("orders-trade.logged.ru"))
    other = _order_revisions(
        _swapped(
            "orders-trade.logged.ru",
            ('<a class="tc-item info"', '<a class="tc-item"'),
            ('class="tc-status text-primary"', 'class="tc-status text-success"'),
        )
    )

    assert base and other, "события о смене состояния не породились"
    assert base[0] != other[0], (
        "состояние заказа сменилось, а отпечаток тот же: версия берётся не из "
        "объявленного источника, и гашение повторов проглотит смену статуса - "
        "то есть бот не узнает об оплате"
    )


def test_a_neighbouring_field_does_not_move_the_order_revision() -> None:
    """Меняет соседнее: подпись текста описания заказа.

    Без этой половины проверка ничего не значила бы: реализация, кладущая в
    версию всю строку целиком, первую половину проходит.

    Returns:
        None
    """
    base = _order_revisions(_swapped("orders-trade.logged.ru"))
    other = _order_revisions(_swapped("orders-trade.logged.ru", ("T106:acops", "T77:acops")))

    assert base and other
    assert base == other, (
        "описание заказа поменялось, а отпечатки съехали: версия захватывает "
        "лишнее, и событие о статусе придёт после правки, к статусу не "
        "относящейся"
    )


def test_the_declared_source_moves_the_chat_revision() -> None:
    """Меняет объявленный источник: позицию последнего сообщения.

    Именно позицию, а не идентификатор. Контракт объявлял здесь last_message_id,
    и это было неверно: реализация читает непрозрачное значение атрибута,
    сравнивает его только на равенство и не знает, что оно означает.

    Returns:
        None
    """
    base = _chat_revisions(_swapped("chat.logged.ru"))
    other = _chat_revisions(
        _swapped("chat.logged.ru", ('data-node-msg="T10:d#1"', 'data-node-msg="T10:d#7"'))
    )

    assert base and other, "события об изменении диалога не породились"
    assert base[0] != other[0], (
        "позиция последнего сообщения сдвинулась, а отпечаток тот же: гашение "
        "повторов проглотит новое сообщение"
    )


def test_a_neighbouring_field_does_not_move_the_chat_revision() -> None:
    """Меняет соседнее: подпись текста последнего сообщения в списке.

    Returns:
        None
    """
    base = _chat_revisions(_swapped("chat.logged.ru"))
    other = _chat_revisions(_swapped("chat.logged.ru", ("T60:cops", "T44:cops")))

    assert base and other
    assert base == other, "текст превью поменялся, а отпечатки съехали: версия захватывает лишнее"


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec" / "events" / "delivery.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_every_producible_kind_declares_its_source() -> None:
    """Требует объявленный источник у каждого ПОРОЖДАЕМОГО вида.

    Покрытие сверяется воротами спецификации по всем объявленным видам. Здесь
    проверяется меньшее и более важное подмножество: те виды, которые эта
    реализация вправду порождает. Для них объявление не документация, а то, чему
    обязан следовать разбор.

    Returns:
        None
    """
    import yaml

    from funora._watch import PRODUCIBLE

    delivery = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "events" / "delivery.yaml").read_text(encoding="utf-8")
    )
    sources: dict[str, Any] = delivery["revision_source"]["sources"]

    for kind in sorted(str(one) for one in PRODUCIBLE):
        assert kind in sources, f"порождаемый вид «{kind}» не объявил источника версии"
        assert str(sources[kind]).strip(), f"у порождаемого вида «{kind}» источник пуст"


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec" / "events" / "delivery.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_the_chat_source_is_declared_as_a_position() -> None:
    """Сверяет объявление с тем, что реализация вправду читает.

    Реализация берёт позицию - непрозрачное значение атрибута, о котором
    известно только то, что оно меняется. Объявление говорило «last_message_id»
    и обещало вызывающему идентификатор сообщения, которого в разметке нет.

    Returns:
        None
    """
    import yaml

    delivery = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "events" / "delivery.yaml").read_text(encoding="utf-8")
    )
    source = str(delivery["revision_source"]["sources"]["chat.unread_changed"])

    assert "позици" in source.lower(), (
        f"источник версии диалога объявлен как {source!r}. Реализация читает "
        "непрозрачную позицию, и называть её идентификатором значит обещать "
        "вызывающему то, чего в разметке нет"
    )


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec" / "events" / "delivery.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_the_chat_revision_is_composed_of_the_declared_parts() -> None:
    """Сверяет состав версии диалога с машиночитаемым перечнем частей.

    Перечень `chat_revision.parts` объявлен списком, а не прозой, - значит его
    можно сверить, и до сих пор его не читал никто. Состав важен целиком: версия
    входит в отпечаток, и переставь вторая реализация части местами, у неё на
    каждое событие о диалоге вышел бы другой отпечаток.

    Сверяется по каждой строке настоящего снимка: частей ровно столько, сколько
    объявлено, первая - позиция строки, вторая - её признак прочитанности.

    Returns:
        None
    """
    import yaml

    from funora._diff import UNKNOWN_UNREAD, _chat_state
    from funora.events import REVISION_SEPARATOR

    delivery = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "events" / "delivery.yaml").read_text(encoding="utf-8")
    )
    declared: list[str] = list(delivery["chat_revision"]["parts"])
    assert declared == ["last_message_position", "unread_flag"], (
        f"перечень частей версии диалога изменился на {declared!r}. Это смена "
        "отпечатка у каждого события о диалоге - сохранённые ключи "
        "идемпотентности обнуляются, и подъём версии контракта обязателен"
    )

    page = parse_chats_page(_swapped("chat.logged.ru"), observed_at=WHEN)
    rows = list(page.rows(accept_incomplete=True))
    assert rows, "снимок не дал ни одной строки"

    for one in rows:
        position = one.last_message_position.or_none()
        assert position is not None, "позиция не выведена: сверять состав не на чем"

        parts = _chat_state(position, one.unread).split(REVISION_SEPARATOR)
        assert len(parts) == len(declared), (
            f"версия диалога сложена из {len(parts)} частей, а объявлено "
            f"{len(declared)}: {declared!r}"
        )
        assert parts[0] == position, (
            f"первой частью объявлена {declared[0]!r}, а стоит {parts[0]!r}. "
            "Перестановка частей меняет отпечаток каждого события о диалоге"
        )
        expected = (
            UNKNOWN_UNREAD if not one.unread.is_observed else ("1" if one.unread.value else "0")
        )
        assert parts[1] == expected, (
            f"второй частью объявлена {declared[1]!r} со значением {expected!r}, "
            f"а стоит {parts[1]!r}"
        )


def _rebuild(parts: list[dict[str, Any]], payload: dict[str, Any], separator: str) -> str:
    """Собирает версию заново из объявленного контрактом состава.

    Собственного знания о видах событий здесь нет намеренно: всё, чем эта
    функция руководствуется, прочитано из спецификации. Иначе проверка сверяла
    бы реализацию сама с собой.

    Args:
        parts (list[dict[str, Any]]): Объявленные части по порядку.
        payload (dict[str, Any]): Нагрузка события.
        separator (str): Разделитель частей версии.

    Returns:
        str: Восстановленная версия.

    Raises:
        AssertionError: Если часть объявлена невыразимо либо называет ключ,
            которого в нагрузке нет. Второе - настоящий разрыв: получатель
            держит на руках только нагрузку, и вывести из неё необъявленное он
            не может.
    """
    built: list[str] = []
    for one in parts:
        if "constant" in one:
            built.append(str(one["constant"]))
            continue
        keys = one["from"] if isinstance(one["from"], list) else [one["from"]]
        for key in keys:
            assert key in payload, (
                f"часть версии названа ключом нагрузки «{key}», а его в нагрузке "
                "нет. Получатель держит на руках только нагрузку: часть, которую "
                "из неё не вывести, вторая реализация не соберёт вовсе"
            )
        values = ["" if payload[key] is None else str(payload[key]) for key in keys]
        built.append(str(one.get("joined_by", "")).join(values))
    return separator.join(built)


@pytest.mark.skipif(
    not SPEC_DIR or not (Path(SPEC_DIR) / "spec" / "events" / "delivery.yaml").is_file(),
    reason="переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec",
)
def test_every_produced_event_composes_its_declared_revision() -> None:
    """Сверяет версию КАЖДОГО порождаемого события с объявленным составом.

    Версия в событие не кладётся - она входит только в отпечаток. Значит
    единственный способ сверить состав: собрать версию заново из объявленного,
    пересчитать отпечаток и потребовать совпадения с настоящим. Совпал - состав
    объявлен верно; разошёлся - вторая реализация, собравшая версию по
    контракту, получит другой идентификатор того же события.

    До этой проверки состав был прозой, и два объявления из восьми уже успели с
    реализацией разойтись.

    Returns:
        None
    """
    import yaml
    from test_event_schemas import ACCOUNT as SCHEMA_ACCOUNT
    from test_event_schemas import _every_produced_event

    from funora._diff import _fingerprint
    from funora._watch import PRODUCIBLE
    from funora.events import REVISION_SEPARATOR

    delivery = yaml.safe_load(
        (Path(SPEC_DIR or ".") / "spec" / "events" / "delivery.yaml").read_text(encoding="utf-8")
    )
    declared: dict[str, Any] = dict(delivery["revision_parts"])
    declared.pop("every_producible_kind_declares_one", None)

    for kind in sorted(str(one) for one in PRODUCIBLE):
        assert kind in declared, (
            f"порождаемый вид «{kind}» не объявил состава версии. Версия входит "
            "в отпечаток, и несверенный состав - это разные идентификаторы "
            "одного события у двух реализаций"
        )

    checked: set[str] = set()
    for event in _every_produced_event():
        kind = str(event.type)
        entry = declared[kind]
        if "not_in_payload" in entry:
            continue
        checked.add(kind)

        rebuilt = _rebuild(entry["parts"], dict(event.payload), REVISION_SEPARATOR)
        assert (
            _fingerprint(
                account_id=SCHEMA_ACCOUNT,
                event_type=event.type,
                entity_id=event.entity_id,
                revision=rebuilt,
            )
            == event.id
        ), (
            f"версия события «{kind}» сложена не из объявленных частей "
            f"{entry['parts']!r}: собранное по контракту даёт другой отпечаток. "
            "Вторая реализация посчитает его по контракту, и гашение повторов у "
            "подключившего обе не сработает ни разу - одно и то же событие "
            "придёт ему дважды"
        )

    expected = {k for k, v in declared.items() if "parts" in v}
    assert checked == expected, (
        f"сверены не все виды с объявленным составом: не хватает "
        f"{sorted(expected - checked)}. Необойдённый вид - несверенный состав"
    )
