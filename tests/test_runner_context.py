"""Проверки чтения того, что нужно для обращения к каналу обновлений.

Всё нужное лежит на одной странице, но в шести разных местах. Собранное вместе,
оно проверяется на снимке целиком; разбросанное по операциям - по частям, и
первая же операция записи собрала бы запрос из того, чего на странице не
оказалось.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from funora._runner import parse_runner_context

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Страница открытого диалога: там есть всё, кроме ключей объекта настроек.
THREAD: Final[str] = "chat-thread.logged.ru"

#: Список диалогов без открытого собеседника.
LIST_ONLY: Final[str] = "chat.logged.ru"

#: Страница заказа: виджет есть, списка диалогов нет.
ORDER: Final[str] = "order.logged.ru"

#: Та же страница диалога форматом v8: в ней есть ключи объекта настроек.
THREAD_V8: Final[str] = "chat-thread.v8.logged.ru"


def _page(name: str) -> str:
    """Читает снимок страницы.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_the_thread_page_carries_every_field_of_the_request() -> None:
    """Требует прочесть со страницы диалога всё, что идёт в запрос.

    Поля берутся из мест, установленных СВЕРКОЙ в живой вкладке, а не выведенных
    по форме: node из data-name (не из data-id - тот девять цифр против двадцати
    трёх знаков), last_message из data-node-msg открытой строки (не из
    data-user-msg).

    Возвращает:
        None
    """
    context = parse_runner_context(_page(THREAD))

    assert context.node_name.is_observed, context.node_name.reason
    assert context.node_id.is_observed
    assert context.chat_tag.is_observed
    assert context.bookmarks_tag.is_observed
    assert context.orders_tag.is_observed
    assert context.own_user_id.is_observed
    assert context.last_message.is_observed, context.last_message.reason

    # Имя диалога и его номер - РАЗНЫЕ значения, и путать их нельзя.
    assert context.node_name.value != context.node_id.value


def test_the_contact_list_page_says_it_cannot_send_and_why() -> None:
    """Требует громко заметить страницу, с которой отправить нельзя.

    На списке диалогов без открытого собеседника у виджета нет ни имени диалога,
    ни его метки. Это не поломка - это выбор страницы, и молчать нельзя:
    вызывающий принял бы пробел за отсутствие диалога.

    Возвращает:
        None
    """
    context = parse_runner_context(_page(LIST_ONLY))

    assert context.can_send is False
    assert "chat_not_selected" in {one.code for one in context.defects}
    assert not context.node_name.is_observed
    assert not context.chat_tag.is_observed

    # А то, что на странице ЕСТЬ, читается по-прежнему: страница исправна.
    assert context.own_user_id.is_observed
    assert context.bookmarks_tag.is_observed


def test_the_order_page_has_the_dialogue_but_not_the_position() -> None:
    """Требует различать «диалога нет» и «позиции нет».

    На странице заказа виджет есть и имя диалога несёт, а списка диалогов нет
    вовсе - значит нет и позиции последнего сообщения. Причины разные, и назвать
    их одинаково значило бы послать читающего не туда.

    Возвращает:
        None
    """
    context = parse_runner_context(_page(ORDER))

    assert context.node_name.is_observed
    assert context.chat_tag.is_observed
    assert not context.last_message.is_observed
    assert "contact_list_missing" in {one.code for one in context.defects}


def test_the_open_dialogue_row_is_found_by_identity_not_by_styling() -> None:
    """Требует искать открытую строку по идентификатору, а не по оформлению.

    Класс active - чужое решение о подсветке, и опираться на него значит читать
    оформление вместо существа. Идентификатор говорит по существу.

    Проверка снимает класс со всех строк и требует, чтобы строка всё равно
    нашлась.

    Возвращает:
        None
    """
    html = _page(THREAD)
    without_marking = html.replace("contact-item active", "contact-item")
    assert without_marking != html, "признак оформления не снялся - проверка пуста"

    context = parse_runner_context(without_marking)
    assert context.last_message.is_observed, (
        f"строка открытого диалога не нашлась без подсветки: {context.last_message.reason}"
    )


def test_the_styling_still_helps_when_the_widget_has_no_identifier() -> None:
    """Требует запасного пути, когда идентификатора у виджета нет.

    Порядок именно такой: сперва равенство значений, потом признак вида. Но
    отбрасывать признак вида нельзя - страница вправе не нести идентификатора, а
    подсветку нести.

    Возвращает:
        None
    """
    html = _page(THREAD)
    at = html.index('class="chat chat-float"')
    end = html.index(">", at)
    broken = html[:at] + html[at:end].replace("data-id=", "data-was-id=", 1) + html[end:]
    assert broken != html, "идентификатор виджета не снялся"

    context = parse_runner_context(broken)
    assert context.last_message.is_observed, (
        f"без идентификатора виджета строка не нашлась по подсветке: {context.last_message.reason}"
    )


def test_a_page_snapshot_older_than_v8_says_so_about_the_token() -> None:
    """Требует отличать «токена нет» от «снимок старый».

    Снимок страницы диалога сделан форматом старше v8, и ключей объекта настроек
    в нём нет. Это возраст снимка, а не поломка площадки, и лечится он
    пересъёмкой, а не разбирательством с разметкой.

    Возвращает:
        None
    """
    context = parse_runner_context(_page(THREAD))

    assert context.csrf_token is None
    assert "app_data_masked_by_skeleton" in {one.code for one in context.defects}
    assert context.can_send is False, "страница без токена не может считаться годной для отправки"


def test_the_position_comes_from_the_dialogue_carrier_not_the_own_one() -> None:
    """Требует брать позицию из data-node-msg, а не из data-user-msg.

    На снимке у открытой строки оба атрибута несут одинаковую подпись, и
    отличить их чтением нельзя: номера значений живут в разряде каждого имени
    отдельно, и совпадение номеров о равенстве не говорит.

    Наблюдением 29.08.2026 установлено, что в запрос идёт data-node-msg. Чтобы
    проверка это держала, второй носитель РАЗВОДИТСЯ с первым: подменяется
    значение data-user-msg открытой строки. Читающий обязан не заметить.

    Возвращает:
        None
    """
    html = _page(THREAD)
    at = html.index("contact-item active")
    end = html.index(">", at)
    row = html[at:end]
    assert "data-user-msg=" in row, "у открытой строки нет второго носителя - проверять нечего"

    spoiled = html[:at] + row.replace('data-user-msg="', 'data-user-msg="РАЗВЕДЕНО', 1) + html[end:]
    assert spoiled != html, "подмена второго носителя не сработала"

    honest = parse_runner_context(html)
    changed = parse_runner_context(spoiled)

    assert honest.last_message.is_observed
    assert changed.last_message.value == honest.last_message.value, (
        f"позиция сдвинулась вслед за data-user-msg: было {honest.last_message.value!r}, "
        f"стало {changed.last_message.value!r}. Читается не тот носитель"
    )


def _with_real_position(html: str) -> str:
    """Подставляет НАСТОЯЩЕЕ число вместо подписи позиции.

    Скелет маскирует числа подписями, и позиция последнего сообщения на снимке
    выглядит как «T10:d#1». В запрос же она уходит числом - так наблюдено.

    Подстановка нужна затем, что иначе положительный случай отправки на
    фикстуре недостижим вовсе, а проверен только отрицательный. Это тот же
    затор, что уже трижды за проект: разбор, который нельзя проверить на
    фикстуре, не проверен ничем.

    Аргументы:
        html (str): разметка снимка.

    Возвращает:
        str: разметка с числовой позицией у открытой строки.
    """
    at = html.index("contact-item active")
    end = html.index(">", at)
    row = html[at:end]
    fixed = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="2010613313"', row, count=1)
    assert fixed != row, "позиция не подставилась"
    return html[:at] + fixed + html[end:]


def test_a_masked_position_makes_the_page_unfit_and_says_why() -> None:
    """Требует отвергнуть страницу, где позиция прочитана не числом.

    В запрос позиция уходит ЧИСЛОМ - так наблюдено. Значение не из цифр
    означает, что прочитано не то: на снимке там подпись скелета, на живой
    странице - изменилась разметка.

    Собрать запрос из этого нельзя, и молчать нельзя тоже: негодная позиция
    ушла бы на площадку.

    Возвращает:
        None
    """
    context = parse_runner_context(_page(THREAD_V8))

    assert context.can_send is False
    assert "last_message_not_numeric" in {one.code for one in context.defects}

    # Прочее при этом читается: страница исправна, замаскирован один атрибут.
    assert context.node_name.is_observed
    assert context.chat_tag.is_observed
    assert context.csrf_token is not None


def test_a_v8_thread_page_is_fit_for_sending_and_says_so() -> None:
    """Требует, чтобы признак пригодности БЫВАЛ истинным.

    Признак, который не бывает истинным ни разу, проверен наполовину - и
    половина эта та, что не ловит ошибку «никогда не годится».

    Возвращает:
        None
    """
    context = parse_runner_context(_with_real_position(_page(THREAD_V8)))

    assert context.can_send is True, [one.code for one in context.defects]
    assert not context.defects, [one.code for one in context.defects]

    assert context.csrf_token is not None
    assert context.csrf_token.reveal()

    for field in (
        context.node_name,
        context.node_id,
        context.chat_tag,
        context.bookmarks_tag,
        context.orders_tag,
        context.own_user_id,
        context.last_message,
    ):
        assert field.is_observed, field.reason
    assert context.last_message.value.isdigit()


def test_the_token_of_a_fit_page_never_leaks_into_its_repr() -> None:
    """Требует, чтобы токен не выходил наружу вместе с прочитанным.

    Прочитанное складывают в журнал целиком - это первое, что делают при
    разборе неудачи. Токен рядом с ним оказался бы там же.

    Возвращает:
        None
    """
    context = parse_runner_context(_with_real_position(_page(THREAD_V8)))
    assert context.csrf_token is not None
    value = context.csrf_token.reveal()

    for rendered in (repr(context), str(context)):
        assert value not in rendered, "значение токена вышло наружу"
