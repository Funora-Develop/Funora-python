"""Проверки разбора переписки.

Половина набора здесь - про одну вещь: происхождение сообщения нельзя определить
неверно в сторону «системное». Если бот выдаёт товар по сообщению площадки об
оплате, а признак ошибается, покупателю достаточно написать нужные слова.

Поэтому проверяется не только то, что признак работает на целой разметке, но и
то, что он отказывает при любом расхождении. Признак, который на испорченной
разметке начинает считать всё системным, хуже отсутствующего.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from funora import _thread as thread_module
from funora._orders import Completeness, Severity
from funora._thread import Origin, parse_thread
from funora.errors import IncompleteResultError, ProtocolChangedError

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения. Задан явно, чтобы разбор оставался повторяемым.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _fixture(name: str = "chat-thread.logged.ru") -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _parse(html: str | None = None):  # type: ignore[no-untyped-def]
    """Разбирает снимок с фиксированным моментом наблюдения.

    Args:
        html (str | None): Разметка. По умолчанию неизменённый снимок.

    Returns:
        Thread: Результат разбора.
    """
    return parse_thread(html if html is not None else _fixture(), observed_at=WHEN)


def test_origins_split_without_overlap() -> None:
    """Проверяет разделение сообщений по происхождению.

    Шесть системных и четыре пользовательских, ни одного неопределённого. Любое
    неопределённое здесь означало бы, что признаки разошлись уже на целой
    разметке, то есть правило неверно с самого начала.

    Returns:
        None
    """
    messages = _parse().messages()

    system = [m for m in messages if m.origin is Origin.SYSTEM]
    human = [m for m in messages if m.origin is Origin.HUMAN]
    unknown = [m for m in messages if m.origin is Origin.UNKNOWN]

    # Точный состав снимка меняется при пересъёмке и сам по себе ни о чём не
    # говорит. Проверяется, что оба вида в снимке есть: иначе согласованность
    # признаков подтверждалась бы на пустом множестве.
    assert system and human
    assert not unknown


def test_system_messages_have_no_author_link() -> None:
    """Проверяет надёжнейший из двух признаков.

    У сообщения пользователя автор всегда ссылка на профиль, и убрать её
    отправитель не может, что бы он ни написал в тексте.

    Returns:
        None
    """
    for message in _parse().messages():
        if message.origin is Origin.SYSTEM:
            assert not message.author_href.is_observed
        if message.origin is Origin.HUMAN:
            assert message.author_href.is_observed


def test_both_markers_present_is_unknown_not_system() -> None:
    """Проверяет закрытый отказ при совпадении признаков.

    Достаточно площадке начать оборачивать и сообщения пользователей, и правило
    «есть обёртка - значит системное» объявило бы системным всё подряд. Ответ
    «не знаю» здесь единственный безопасный.

    Returns:
        None
    """
    html = (
        '<div class="chat-message-list">'
        '<div class="chat-msg-item" id="message-1">'
        '<a class="chat-msg-author-link" href="/users/1/">кто-то</a>'
        '<div class="chat-msg-body"><div class="alert">оплачено</div></div>'
        "</div></div>"
    )
    message = _parse(html).messages(accept_incomplete=True)[0]
    assert message.origin is Origin.UNKNOWN


def test_neither_marker_is_unknown_not_system() -> None:
    """Проверяет закрытый отказ при отсутствии обоих признаков.

    Returns:
        None
    """
    html = (
        '<div class="chat-message-list">'
        '<div class="chat-msg-item" id="message-1">'
        '<div class="chat-msg-body"><div class="chat-msg-text">оплачено</div></div>'
        "</div></div>"
    )
    message = _parse(html).messages(accept_incomplete=True)[0]
    assert message.origin is Origin.UNKNOWN


def test_counterparty_text_cannot_forge_a_system_message() -> None:
    """Проверяет главное: текст не влияет на происхождение.

    Сообщение пользователя с текстом уведомления об оплате обязано остаться
    сообщением пользователя. Это ровно та схема обмана, о которой площадка
    предупреждает продавцов сама.

    Returns:
        None
    """
    html = (
        '<div class="chat-message-list">'
        '<div class="chat-msg-item" id="message-1">'
        '<a class="chat-msg-author-link" href="/users/1/">покупатель</a>'
        '<div class="chat-msg-body"><div class="chat-msg-text">'
        "Покупатель оплатил заказ. Средства зачислены на ваш счёт."
        "</div></div></div></div>"
    )
    message = _parse(html).messages()[0]
    assert message.origin is Origin.HUMAN


def test_removed_wrapper_degrades_instead_of_lying() -> None:
    """Проверяет поведение при исчезновении обёртки предупреждения.

    Системные сообщения теряют один из двух признаков и становятся
    неопределёнными, а не остаются системными. Переписка объявляется прочитанной
    не полностью.

    Returns:
        None
    """
    broken = _fixture().replace('class="alert alert-with-icon alert-info"', 'class="notice"')
    page = _parse(broken)

    assert page.completeness is Completeness.PARTIAL
    messages = page.messages(accept_incomplete=True)
    assert sum(1 for m in messages if m.origin is Origin.SYSTEM) == 0
    unknown = sum(1 for m in messages if m.origin is Origin.UNKNOWN)
    assert 0 < unknown < len(messages)
    assert any(d.code == "origin_indeterminate" for d in page.defects)


def test_indeterminate_everywhere_is_a_page_defect() -> None:
    """Проверяет повышение уровня, когда происхождение не читается нигде.

    Одно неопределённое сообщение - случайность. Все неопределённые означают, что
    различать площадку и собеседника нечем, а это отказ уровня страницы.

    Returns:
        None
    """
    html = (
        '<div class="chat-message-list">'
        '<div class="chat-msg-item" id="a"><div class="chat-msg-body">1</div></div>'
        '<div class="chat-msg-item" id="b"><div class="chat-msg-body">2</div></div>'
        "</div>"
    )
    page = _parse(html)
    assert any(
        d.severity is Severity.PAGE and d.code == "origin_indeterminate_in_all_messages"
        for d in page.defects
    )


def test_external_links_are_collected_not_followed() -> None:
    """Проверяет, что внешние ссылки видны вызывающему.

    Собраны они для того, чтобы их было видно, а не для того, чтобы по ним
    ходить: их пишет собеседник, и переход означал бы, что содержимое переписки
    управляет поведением клиента.

    Returns:
        None
    """
    messages = _parse().messages()
    with_links = [m for m in messages if m.external_links]
    assert with_links, "в снимке есть сообщения со ссылками на сторонние сайты"
    for message in with_links:
        assert message.origin is Origin.HUMAN


def test_no_api_answers_whether_it_is_paid() -> None:
    """Проверяет, что модуль не предлагает ответа на вопрос об оплате.

    Даже верно опознанное системное сообщение не является подтверждением оплаты:
    оно могло относиться к другому заказу, устареть, прийти по отменённому
    платежу. Метод с таким именем появился бы в чужом коде в тот же день, когда
    появился бы здесь, поэтому его отсутствие закреплено проверкой.

    Returns:
        None
    """
    forbidden = ("paid", "payment", "confirm", "оплач")
    names = [name for name in dir(thread_module) if not name.startswith("_")]
    names += [f.name for f in thread_module.Message.__dataclass_fields__.values()]

    for name in names:
        assert not any(word in name.lower() for word in forbidden), (
            f"имя {name} обещает ответ на вопрос об оплате, которого разметка не даёт"
        )


def test_missing_container_is_protocol_changed() -> None:
    """Проверяет отказ при отсутствии контейнера сообщений.

    Returns:
        None
    """
    with pytest.raises(ProtocolChangedError):
        _parse(_fixture("orders-trade.logged.ru"))


def test_incomplete_thread_requires_acknowledgement() -> None:
    """Проверяет, что неполная переписка не выдаётся молча.

    В переписке это опаснее, чем в списке: пропущенное сообщение выглядит как
    ненаписанное.

    Returns:
        None
    """
    broken = _fixture().replace('class="alert alert-with-icon alert-info"', 'class="notice"')
    page = _parse(broken)
    with pytest.raises(IncompleteResultError):
        page.messages()
    assert len(page.messages(accept_incomplete=True)) == page.rows_total


def test_message_ids_are_observed() -> None:
    """Проверяет, что у каждого сообщения есть идентификатор.

    Без него позиция переигрывания не адресует ничего конкретного.

    Returns:
        None
    """
    for message in _parse().messages():
        assert message.message_id.is_observed


def test_full_time_hint_is_present_in_threads() -> None:
    """Проверяет наличие подсказки с полной формой времени.

    На странице заказов её нет вовсе, здесь есть. Разбирать её всё равно нельзя -
    внутри локализованный текст, - но отличие страниц зафиксировано.

    Returns:
        None
    """
    for message in _parse().messages():
        assert message.time_full_text.is_observed


def test_parse_is_deterministic() -> None:
    """Проверяет повторяемость разбора.

    Returns:
        None
    """
    assert _parse().messages() == _parse().messages()
