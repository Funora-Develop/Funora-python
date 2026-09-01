"""Проверки дочитывания переписки назад от курсора.

ЧЕМ ЭТА ОПЕРАЦИЯ ОТЛИЧАЕТСЯ ОТ ПРОЧИХ ЗАИМСТВОВАННЫХ: у неё заимствован ЗАПРОС
ЦЕЛИКОМ. У отметки прочтения чужим был вывод о действии нашего же наблюдённого
запроса; у возврата - чтение ответа. Здесь чужие и адрес, и оба имени
параметров, и форма ответа: этой точки мы не видели ни разу.

Отсюда главная проверка набора - СВЕРКА НАПРАВЛЕНИЯ. Утверждение «курсор отдаёт
сообщения СТАРШЕ него» ничем не подтверждено, а молча отданный список выглядит
одинаково правильным в обе стороны. Реализация сверяет идентификаторы с посланным
курсором сама, и проверки ниже требуют, чтобы сверка была настоящей: отказ на
неверной стороне, отказ на смешанном ответе и отказ ДО отдачи чего бы то ни было.

Вторая по важности - РАЗЛИЧЕНИЕ ПУСТОГО И ИЗМЕНИВШЕГОСЯ. Соседняя реализация
отвечает пустым списком и на конец переписки, и на неузнанный ответ; у нас это
разные исходы, и путать их нельзя: первый останавливает листание правильно,
второй - молча и навсегда.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._chat_history import (
    CHAT_HISTORY_PATH,
    HISTORY_HEADERS,
    ChatHistory,
    parse_history,
)
from funora._engine import Ask, Engine
from funora._result import Completeness, Severity
from funora._thread import Origin
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import (
    CursorIncompatibleError,
    IncompleteResultError,
    UnexpectedResponseError,
    ValidationError,
)

WHEN: Final[datetime] = datetime(2026, 9, 1, tzinfo=UTC)
NODE: Final[str] = "247450736"
CURSOR: Final[str] = "5000"


def _human(text: str = "привет", author: str = "buyer") -> str:
    """Собирает разметку людского сообщения так, как её описал сторонний источник.

    Аргументы:
        text (str): Текст сообщения.
        author (str): Имя автора.

    Возвращает:
        str: Разметка фрагмента.
    """
    return f'<div class="chat-msg-item">{_inner(text, author)}</div>'


def _inner(text: str = "привет", author: str = "buyer") -> str:
    """Собирает НУТРО людского сообщения - без обёртки.

    Приходит ли обёртка во фрагменте ответа, у нас не наблюдалось. Разбор обязан
    работать в обоих случаях, и вторая половина этой пары проверяется отдельно.

    Аргументы:
        text (str): Текст сообщения.
        author (str): Имя автора.

    Возвращает:
        str: Разметка фрагмента без обёртки сообщения.
    """
    return (
        '<div class="media-user-name">'
        f'<a class="chat-msg-author-link" href="https://funpay.com/users/1/">{author}</a>'
        "</div>"
        '<div class="chat-msg-body">'
        f'<div class="chat-msg-text">{text}</div>'
        "</div>"
        '<div class="chat-msg-date" title="1 сентября 2026, 12:00">12:00</div>'
    )


def _system(text: str = "заказ оплачен") -> str:
    """Собирает разметку сообщения площадки.

    Аргументы:
        text (str): Текст сообщения.

    Возвращает:
        str: Разметка фрагмента.
    """
    return (
        '<div class="chat-msg-item">'
        '<div class="chat-msg-body">'
        f'<div class="alert"><div class="chat-msg-text">{text}</div></div>'
        "</div>"
        '<div class="chat-msg-date" title="1 сентября 2026, 11:00">11:00</div>'
        "</div>"
    )


def _answer(*entries: dict[str, Any]) -> dict[str, Any]:
    """Собирает ответ площадки в описанной сторонним источником форме.

    Аргументы:
        entries (dict[str, Any]): Записи сообщений.

    Возвращает:
        dict[str, Any]: Тело ответа.
    """
    return {"chat": {"node": {"id": int(NODE), "silent": False}, "messages": list(entries)}}


def _entry(identifier: int, markup: str | None = None, author: int = 1) -> dict[str, Any]:
    """Собирает одну запись сообщения.

    Аргументы:
        identifier (int): Идентификатор сообщения.
        markup (str | None): Разметка тела; по умолчанию людское сообщение.
        author (int): Идентификатор автора.

    Возвращает:
        dict[str, Any]: Запись.
    """
    return {"id": identifier, "author": author, "html": _human() if markup is None else markup}


def _parse(*entries: dict[str, Any], cursor: str = CURSOR) -> ChatHistory:
    """Разбирает ответ с заданными записями.

    Аргументы:
        entries (dict[str, Any]): Записи сообщений.
        cursor (str): Посланный курсор.

    Возвращает:
        ChatHistory: Итог разбора.
    """
    return parse_history(_answer(*entries), chat_id=NODE, cursor=cursor, observed_at=WHEN)


class _Driver:
    """Прокручивает ядро, подставляя заданный ответ.

    Аргументы:
        body (str): Тело ответа.
    """

    def __init__(self, body: str) -> None:
        self.body = body
        self.asks: list[Ask] = []

    def run(self, core: Any) -> Any:
        """Прокручивает ядро.

        Аргументы:
            core (Any): Сопрограмма.

        Возвращает:
            Any: Итог.
        """
        reply: Any = None
        while True:
            try:
                request = core.send(reply)
            except StopIteration as stop:
                return stop.value
            if isinstance(request, Ask):
                self.asks.append(request)
                raw = self.body.encode("utf-8")
                reply = Observation(
                    status=200,
                    final_url=f"https://funpay.com{request.path}",
                    html=self.body,
                    elapsed_ms=10,
                    redirects=0,
                    content_length=len(raw),
                    declared_length=len(raw),
                )
            else:
                reply = None


def _engine() -> Engine:
    """Собирает движок без сети, с подтверждённой возможностью.

    Возвращает:
        Engine: Движок.
    """
    engine = Engine(TransportSettings(), Budget())
    engine._state.capabilities[Capability.CHATS_HISTORY_PAGINATION] = CapabilityState.SUPPORTED
    return engine


# --- Сверка направления: главное, ради чего всё написано ---------------------


def test_a_reply_from_the_wrong_side_is_refused() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: ответ не с той стороны отвергается целиком.

    Что курсор отдаёт сообщения СТАРШЕ него, взято у независимой реализации того
    же протокола и нами не наблюдалось. Это единственная проверка чужого
    утверждения, и без неё оно осталось бы непроверяемым навсегда.

    Возвращает:
        None
    """
    with pytest.raises(CursorIncompatibleError) as raised:
        _parse(_entry(5001), _entry(5002))

    text = str(raised.value)
    assert "5001" in text, "отказ не называет, какое именно сообщение не с той стороны"
    assert "не наблюдалось" in text, "отказ не говорит, что утверждение о направлении чужое"


def test_the_cursor_itself_counts_as_the_wrong_side() -> None:
    """Граница строгая: сообщение, РАВНОЕ курсору, тоже не с той стороны.

    Без строгости листание встало бы на месте: каждый следующий шаг возвращал бы
    то же сообщение, от которого его и просили, и цикл крутился бы вечно.

    Возвращает:
        None
    """
    with pytest.raises(CursorIncompatibleError):
        _parse(_entry(int(CURSOR)))


def test_one_wrong_message_condemns_the_whole_reply() -> None:
    """Смешанный ответ отвергается целиком, а не наполовину.

    Отдать годную половину значило бы решить за вызывающего, что расхождение
    несущественно. Расхождение здесь означает, что чужое утверждение о точке
    неверно либо площадка его изменила, и обе причины касаются всего ответа.

    Возвращает:
        None
    """
    with pytest.raises(CursorIncompatibleError):
        _parse(_entry(4998), _entry(4999), _entry(5001))


def test_the_right_side_passes() -> None:
    """Обратная половина: годный ответ проходит.

    Без неё сверка, отвергающая ВСЁ, прошла бы три проверки выше.

    Возвращает:
        None
    """
    history = _parse(_entry(4998), _entry(4999))

    assert history.completeness is Completeness.COMPLETE, history.reason
    assert [one.message_id.value for one in history.messages()] == ["4998", "4999"]


def test_the_cursor_is_kept_in_the_result() -> None:
    """Посланный курсор остаётся в исходе.

    На нём стоит сверка, и по нему вызывающий видит, сдвинулось ли листание.

    Возвращает:
        None
    """
    assert _parse(_entry(4998)).cursor_sent == CURSOR
    assert _parse(_entry(4998)).chat_id == NODE


# --- Пустое против изменившегося ---------------------------------------------


def test_an_empty_list_means_the_beginning_of_the_thread() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: пустой перечень - это наблюдение, а не сбой.

    Вызывающий, листающий в цикле, останавливается по этому признаку. Объяви мы
    пустой ответ неизвестностью - цикл ходил бы за одним и тем же вечно.

    Возвращает:
        None
    """
    history = _parse()

    assert history.exhausted is True, "конец переписки не назван"
    assert history.completeness is Completeness.COMPLETE, (
        "пустой ответ объявлен неполным - а он полон: площадке нечего отдать"
    )
    assert history.reason == "history_exhausted"
    assert history.messages() == ()


def test_a_missing_messages_key_is_not_an_empty_thread() -> None:
    """Отсутствие ключа отличается от пустого перечня.

    Соседняя реализация отвечает пустым списком на оба случая. У нас первый
    останавливает листание правильно, второй - молча и навсегда, поэтому
    отсутствие ключа обязано быть отказом.

    Возвращает:
        None
    """
    with pytest.raises(UnexpectedResponseError) as raised:
        parse_history({"chat": {"node": {"id": 1}}}, chat_id=NODE, cursor=CURSOR, observed_at=WHEN)

    assert "не признак конца переписки" in str(raised.value)


def test_a_missing_chat_object_is_refused() -> None:
    """Ответ без объекта переписки отвергается.

    Возвращает:
        None
    """
    with pytest.raises(UnexpectedResponseError):
        parse_history({}, chat_id=NODE, cursor=CURSOR, observed_at=WHEN)


@pytest.mark.parametrize("payload", [[], "текст", 12, None])
def test_a_reply_of_the_wrong_shape_is_refused(payload: object) -> None:
    """Ответ не объектом отвергается.

    Аргументы:
        payload (object): Тело ответа.

    Возвращает:
        None
    """
    with pytest.raises(UnexpectedResponseError):
        parse_history(payload, chat_id=NODE, cursor=CURSOR, observed_at=WHEN)


def test_messages_of_the_wrong_shape_are_refused() -> None:
    """Перечень сообщений не перечнем отвергается.

    Возвращает:
        None
    """
    with pytest.raises(UnexpectedResponseError):
        parse_history(
            {"chat": {"messages": {"id": 1}}}, chat_id=NODE, cursor=CURSOR, observed_at=WHEN
        )


# --- Идентификатор берётся из поля ответа, а не из разметки -------------------


def test_the_identifier_comes_from_the_field_not_the_markup() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: идентификатор берётся из поля ответа.

    Во фрагменте атрибута с идентификатором может не быть вовсе - обёртка
    сообщения приходит ли в нём, у нас не наблюдалось. Поле ответа надёжнее, и
    на нём же стоит сверка направления.

    Возвращает:
        None
    """
    history = _parse(_entry(4998, markup=_inner("без обёртки")))

    one = history.messages()[0]
    assert one.message_id.value == "4998", "идентификатор не взят из поля ответа"
    assert one.text.value == "без обёртки", "текст не разобрался без обёртки сообщения"
    assert one.origin is Origin.HUMAN, "происхождение не определилось без обёртки"


def test_no_defect_is_raised_about_an_identifier_that_was_found() -> None:
    """Повреждение об отсутствии идентификатора снимается.

    Разбор страницы ищет его в атрибуте и жалуется, не найдя. Здесь он найден в
    другом месте, и жалоба была бы ложной тревогой - то есть шумом, за которым
    перестают следить.

    Возвращает:
        None
    """
    history = _parse(_entry(4998, markup=_inner("без обёртки")))

    assert not [one for one in history.defects if one.field_name == "message_id"], (
        "разбор жалуется на идентификатор, который у него есть"
    )
    assert history.completeness is Completeness.COMPLETE, history.reason


@pytest.mark.parametrize("bad", ["12a", "", "-5", "5.0", None, [], {"a": 1}])
def test_an_unreadable_identifier_drops_the_row(bad: object) -> None:
    """Нечисловой идентификатор отбрасывает запись, а не проходит молча.

    Сверять направление по строке «12a» нельзя, и запись, прошедшая мимо сверки,
    обесценила бы её целиком.

    Аргументы:
        bad (object): Непригодный идентификатор.

    Возвращает:
        None
    """
    history = _parse({"id": bad, "author": 1, "html": _human()})

    assert history.rows_total == 1, "запись не сосчитана"
    assert history.rows_accepted == 0, "непригодная запись принята"
    assert history.completeness is Completeness.PARTIAL
    assert any(one.code == "identifier_unreadable" for one in history.defects)


def test_a_row_without_markup_is_refused() -> None:
    """Запись без разметки отвергается.

    Возвращает:
        None
    """
    with pytest.raises(UnexpectedResponseError):
        _parse({"id": 4998, "author": 1})


# --- Прочее в разборе ---------------------------------------------------------


def test_duplicate_identifiers_are_named() -> None:
    """Одинаковые идентификаторы называются повреждением страницы.

    Листание по повторяющемуся курсору встанет на месте.

    Возвращает:
        None
    """
    history = _parse(_entry(4998), _entry(4998))

    assert history.completeness is Completeness.PARTIAL
    found = [one for one in history.defects if one.code == "duplicate_identifiers"]
    assert found and found[0].severity is Severity.PAGE


def test_origin_is_determined_structurally() -> None:
    """Происхождение определяется разметкой и здесь тоже.

    Правило то же, что на странице переписки: ссылка на автора у людского
    сообщения, обёртка предупреждения у сообщения площадки.

    Возвращает:
        None
    """
    history = _parse(_entry(4997, markup=_system()), _entry(4998))
    first, second = history.messages(accept_incomplete=True)

    assert first.origin is Origin.SYSTEM, "сообщение площадки принято за людское"
    assert second.origin is Origin.HUMAN, "людское сообщение принято за системное"


def test_incomplete_history_is_guarded() -> None:
    """Неполный итог не отдаётся без признания.

    Пропущенное при листании сообщение не вернётся: следующий шаг возьмёт курсор
    от того, что дошло.

    Возвращает:
        None
    """
    history = _parse({"id": "12a", "author": 1, "html": _human()})

    with pytest.raises(IncompleteResultError):
        history.messages()
    assert history.messages(accept_incomplete=True) == ()
    assert len(history) == 0


# --- Запрос: адрес, параметры, заголовки -------------------------------------


def test_the_request_carries_the_borrowed_address_and_headers() -> None:
    """Запрос уходит по заимствованному адресу с заимствованными заголовками.

    Признак «спрашивает сценарий страницы» обязателен по тому же чужому
    сообщению, что и адрес: без него площадка отвечает страницей, и разбор
    объекта на ней не сойдётся.

    Возвращает:
        None
    """
    driver = _Driver(json.dumps(_answer(_entry(4998))))
    driver.run(_engine().read_history_before(NODE, before_message_id=CURSOR))

    assert len(driver.asks) == 1, "запрос ушёл не один раз"
    ask = driver.asks[0]
    assert ask.path == f"{CHAT_HISTORY_PATH}?node={NODE}&last_message={CURSOR}"
    assert ask.headers["x-requested-with"] == "XMLHttpRequest"
    assert ask.headers == HISTORY_HEADERS


def test_a_non_numeric_cursor_is_refused_before_the_network() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: нечисловой курсор не доходит до сети.

    На курсоре стоит сверка направления - единственное, чем мы проверяем чужое
    утверждение об этой точке. Пропусти мы сюда нечисловой курсор, сверка молча
    перестала бы работать, а операция продолжала бы выглядеть проверенной.

    Возвращает:
        None
    """
    driver = _Driver("{}")

    with pytest.raises(ValidationError) as raised:
        driver.run(_engine().read_history_before(NODE, before_message_id="12a"))

    assert not driver.asks, "запрос ушёл при непригодном курсоре"
    assert "сверка направления" in str(raised.value)


@pytest.mark.parametrize("bad", ["", "  ", "../../etc", "12 34", "a/b"])
def test_a_bad_node_is_refused_before_the_network(bad: str) -> None:
    """Непригодный узел не доходит до сети.

    Аргументы:
        bad (str): Непригодный идентификатор диалога.

    Возвращает:
        None
    """
    driver = _Driver("{}")

    with pytest.raises(ValidationError):
        driver.run(_engine().read_history_before(bad, before_message_id=CURSOR))

    assert not driver.asks, "запрос ушёл при непригодном узле"


def test_a_reply_that_is_not_an_object_is_refused() -> None:
    """Ответ, не разобравшийся как объект, отвергается внятно.

    Возвращает:
        None
    """
    driver = _Driver("<html>страница проверки</html>")

    with pytest.raises(UnexpectedResponseError) as raised:
        driver.run(_engine().read_history_before(NODE, before_message_id=CURSOR))

    assert "не разобрался" in str(raised.value)


def test_the_capability_gates_the_operation() -> None:
    """Возможность гасит операцию, пока не подтверждена.

    Возвращает:
        None
    """
    engine = Engine(TransportSettings(), Budget())
    engine._state.capabilities[Capability.CHATS_HISTORY_PAGINATION] = CapabilityState.UNSUPPORTED
    driver = _Driver("{}")

    with pytest.raises(Exception) as raised:
        driver.run(engine.read_history_before(NODE, before_message_id=CURSOR))

    assert not driver.asks, "запрос ушёл при неподтверждённой возможности"
    assert not isinstance(raised.value, ValidationError)


def test_reading_asks_no_consent() -> None:
    """Заимствованное ЧТЕНИЕ согласия не спрашивает.

    Обратная половина правила о заимствованном знании. Спрашивать согласие везде
    подряд значило бы обесценить механизм: вызывающий, привыкший включать всё,
    перестанет читать, ЧТО ему предлагают включить.

    Возвращает:
        None
    """
    engine = _engine()
    assert Capability.CHATS_HISTORY_PAGINATION not in engine._state.opted_in

    driver = _Driver(json.dumps(_answer(_entry(4998))))
    history = driver.run(engine.read_history_before(NODE, before_message_id=CURSOR))

    assert isinstance(history, ChatHistory), "чтение потребовало согласия"
    assert driver.asks, "запрос не ушёл"
