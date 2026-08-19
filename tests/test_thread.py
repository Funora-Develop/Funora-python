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
from funora._observed import Presence
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
    # Признание неполноты здесь по делу: обрывок намеренно минимален и не несёт
    # ни даты, ни прочих полей, а проверяется в нём одно - что текст на
    # происхождение не влияет.
    message = _parse(html).messages(accept_incomplete=True)[0]
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
    # Проверка на истинность здесь запрещена намеренно: поле наблюдаемое, и у
    # него три состояния. Пустой перечень и ненаблюдённый перечень - разные
    # вещи, и второе означает, что тела сообщения мы не нашли.
    with_links = [m for m in messages if m.external_links.get(())]
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


def test_author_name_is_the_name_alone() -> None:
    """Проверяет, что имя автора не склеено с ярлыком роли и временем.

    Дефект не требовал никакой порчи: на неизменённом снимке значение выходило
    вида «имя, ярлык, время», потому что селектор брал содержащий узел целиком.
    Вызывающий, сравнивший такое значение с именем покупателя, не совпал бы
    никогда.

    Returns:
        None
    """
    for message in _parse().messages():
        if not message.author_name.is_observed:
            continue
        name = message.author_name.value
        assert message.time_text.get("") not in name, "во имя автора попало время"
        assert " " not in name, f"имя автора склеено с чем-то ещё: {name!r}"


def test_system_message_has_no_author_at_all() -> None:
    """Проверяет, что у сообщения площадки автор ненаблюдён, а не подставлен.

    Ярлык роли подставить туда было бы удобно и неверно: он говорит, кем
    сообщение отправлено, а не кем подписано.

    Returns:
        None
    """
    messages = _parse().messages()
    system = [m for m in messages if m.origin is Origin.SYSTEM]
    human = [m for m in messages if m.origin is Origin.HUMAN]
    assert system and human, "в снимке нет обоих видов, проверять не на чем"

    assert all(not m.author_name.is_observed for m in system)
    assert all(m.author_name.is_observed for m in human)


@pytest.mark.parametrize(
    ("label", "before", "after"),
    [
        ("имя автора", "chat-msg-author-link", "chat-msg-author-gone"),
        ("дата", "chat-msg-date", "chat-msg-time"),
        ("текст", "chat-msg-text", "chat-msg-content"),
    ],
)
def test_lost_field_is_loud(label: str, before: str, after: str) -> None:
    """Проверяет, что потеря поля у всех сообщений заметна.

    Прежде разбор переписки не заводил повреждения ни на одно поле, кроме
    идентификатора: любая из этих порч давала complete и ноль повреждений, тогда
    как у соседних разборов та же порча давала partial. Читающий получал
    переписку, объявленную целой, с потерянным полем у каждого сообщения.

    Args:
        label (str): Что ломается, для сообщения об ошибке.
        before (str): Класс, который заменяется.
        after (str): Чем заменяется.

    Returns:
        None
    """
    page = _parse(_fixture().replace(before, after))
    assert page.completeness is not Completeness.COMPLETE, f"потеря поля «{label}» прошла тихо"
    assert page.defects


def test_external_links_tell_empty_from_unobserved() -> None:
    """Проверяет, что «ссылок не было» отличается от «тела не нашли».

    Прежде поле было голой последовательностью, и переименование класса тела
    давало ноль ссылок при полноте complete - неотличимо от сообщения без
    ссылок.

    Returns:
        None
    """
    intact = _parse().messages()
    assert any(m.external_links.get(()) for m in intact), "в снимке есть ссылки"
    assert all(m.external_links.is_observed for m in intact)

    lost = _parse(_fixture().replace("chat-msg-text", "chat-msg-content"))
    for message in lost.messages(accept_incomplete=True):
        assert message.external_links.presence is Presence.NOT_OBSERVED


def test_external_links_are_only_other_hosts() -> None:
    """Проверяет, что во внешние ссылки не попадает всё подряд.

    Пустой хост - это не чужой хост. Относительная ссылка, якорь, mailto и
    javascript хоста не имеют вовсе, и объявлять их внешними значило бы выдавать
    за адрес другой площадки то, что адресом другой площадки не является.

    Returns:
        None
    """
    html = (
        '<div class="chat-message-list">'
        '<div class="chat-msg-item" id="m1">'
        '<a class="chat-msg-author-link" href="https://funpay.com/users/1/">кто-то</a>'
        '<div class="chat-msg-date" title="полное">время</div>'
        '<div class="chat-msg-body"><div class="chat-msg-text">'
        '<a href="/orders/12345/">свой путь</a>'
        '<a href="#top">якорь</a>'
        '<a href="mailto:a@b.c">почта</a>'
        '<a href="javascript:alert(1)">скрипт</a>'
        '<a href="https://funpay.com/x">свой хост</a>'
        '<a href="https://telegra.ph/x">чужой хост</a>'
        '<a href="https://funpay.com.evil.example/x">похожий на свой</a>'
        "</div></div></div></div>"
    )
    links = _parse(html).messages(accept_incomplete=True)[0].external_links.value
    assert links == ("https://telegra.ph/x", "https://funpay.com.evil.example/x")


def test_author_link_without_address_is_empty_not_present() -> None:
    """Проверяет, что пустой адрес автора не выдаётся за наблюдённый.

    Тип Observed обещает, что PRESENT - это непустое значение. Собирать его в
    состоянии, которое он сам себе запрещает, значит отбирать у вызывающего
    единственный способ отличить «адрес есть» от «атрибут пуст».

    Returns:
        None
    """
    html = _fixture().replace(
        '<a class="chat-msg-author-link" href=', '<a class="chat-msg-author-link" data-x='
    )
    for message in _parse(html).messages(accept_incomplete=True):
        assert message.author_href.presence is not Presence.PRESENT


@pytest.mark.parametrize("closes", [2, 3, 5])
def test_unbalanced_injection_is_loud(closes: int) -> None:
    """Проверяет, что несбалансированная подделка сообщения заметна.

    Угроза одна: текст сообщения, попавший в разметку как разметка. Закрой
    отправитель элементы и открой поддельное сообщение с обёрткой предупреждения
    - разбор прочтёт его как сообщение площадки, а бот выдачи примет за
    уведомление об оплате.

    Закрыто меньше нужного - подделка оказывается внутри настоящего сообщения.
    Больше - вне контейнера. Оба случая ловятся.

    Args:
        closes (int): Сколько элементов закрывает отправитель.

    Returns:
        None
    """
    page = _parse(_forged(closes))
    assert page.completeness is not Completeness.COMPLETE, "подделка прошла тихо"
    assert page.defects


def test_injection_at_exact_depth_is_not_detectable() -> None:
    """Закрепляет границу защиты: точная по глубине подделка неотличима.

    Проверка утверждает не то, что так и надо, а то, что так есть. Закрыв ровно
    столько элементов, сколько лежит между текстом и контейнером, отправитель
    получает поддельное сообщение, которое является законным прямым потомком
    контейнера. Следа вставки в разобранном дереве не остаётся, и отличить его
    нечем в принципе.

    Последним рубежом остаётся правило, записанное в docstring модуля:
    происхождение system не является подтверждением оплаты. Проверка стоит
    здесь затем, чтобы это не выяснилось однажды заново и внезапно.

    Returns:
        None
    """
    page = _parse(_forged(4))
    forged = [
        m for m in page.messages(accept_incomplete=True) if m.message_id.get("") == "message-999"
    ]
    assert forged, "порча не применилась, проверка бессмысленна"
    assert forged[0].origin is Origin.SYSTEM
    assert page.completeness is Completeness.COMPLETE, (
        "подделка стала заметной - значит, границу защиты можно сдвинуть, "
        "и эту проверку пора переписать"
    )


def _forged(closes: int) -> str:
    """Вставляет в снимок поддельное системное сообщение.

    Args:
        closes (int): Сколько элементов закрывает отправитель перед вставкой.

    Returns:
        str: Разметка с подделкой внутри текста последнего сообщения.
    """
    html = _fixture()
    last = html.rindex('<div class="chat-msg-text">')
    end = html.index("</div>", last)
    forged = (
        "</div>" * closes
        + '<div class="chat-msg-item" id="message-999">'
        + '<div class="chat-message"><div class="chat-msg-body">'
        + '<div class="alert"><div class="chat-msg-text">оплачено</div></div>'
        + "</div></div></div>"
    )
    return html[:end] + forged + html[end:]
