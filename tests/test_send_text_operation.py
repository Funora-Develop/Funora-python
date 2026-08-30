"""Проверки операции отправки текстового сообщения.

Первая операция ЗАПИСИ в проекте, и потому первая, у которой цена ошибки не
симметрична: лишний отказ - неудобство, лишняя отправка - второе сообщение
покупателю, которое нельзя отменить.

Сети здесь нет. Ядро - сопрограмма, и проверка отвечает на его просьбы сама:
так видно не только результат, но и ЧТО именно ушло бы в сеть.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from time import monotonic, time
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit
from funora._outbound import OutboundGovernor
from funora._transport import Observation, TransportSettings
from funora.errors import BudgetExhaustedError, UsageError, ValidationError
from funora.send_outcome import SendOutcome

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок открытого диалога форматом v8: там есть всё нужное для отправки.
THREAD: Final[str] = "chat-thread.v8.logged.ru"

#: Идентификатор диалога. Каким он будет в адресе, проверка и смотрит.
NODE_ID: Final[str] = "283028758"


def _page() -> str:
    """Читает снимок страницы диалога с ЧИСЛОВОЙ позицией.

    Скелет маскирует числа подписями, и позиция последнего сообщения на снимке
    выглядит как «T10:d#1». В запрос же она уходит числом - так наблюдено, - и
    разбор такую страницу отвергает: собрать из подписи запрос нельзя.

    Подстановка нужна затем, что иначе положительный случай отправки на фикстуре
    недостижим вовсе. Отрицательный проверяется отдельно, в наборе разбора.

    Возвращает:
        str: содержимое скелета с числовой позицией.
    """
    html = (FIXTURES / f"{THREAD}.skeleton.txt").read_text(encoding="utf-8")
    at = html.index("contact-item active")
    end = html.index(">", at)
    row = html[at:end]
    fixed = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="2010613313"', row, count=1)
    assert fixed != row, "позиция не подставилась"
    return html[:at] + fixed + html[end:]


def _observation(html: str, *, status: int = 200) -> Observation:
    """Собирает наблюдение, каким его отдаёт транспорт.

    Аргументы:
        html (str): тело ответа.
        status (int): код состояния.

    Возвращает:
        Observation: наблюдение.
    """
    return Observation(
        status=status,
        final_url="https://funpay.com/chat/",
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(html.encode("utf-8")),
    )


def _answer(*, objects: list[dict[str, Any]] | None = None, error: object = None) -> str:
    """Собирает ответ канала по наблюдённой форме.

    Аргументы:
        objects (list[dict[str, Any]] | None): объекты ответа.
        error (object): содержимое поля error.

    Возвращает:
        str: тело ответа.
    """
    return json.dumps({"objects": objects or [], "response": {"error": error}})


def _run(
    engine: Engine, core: Any, *, answer: str, page: str | None = None
) -> tuple[Any, list[Any]]:
    """Крутит ядро, отвечая на его просьбы, и запоминает их.

    Аргументы:
        engine (Engine): движок.
        core (Any): сопрограмма операции.
        answer (str): чем отвечать на просьбу об отправке.
        page (str | None): чем отвечать на просьбу о чтении.

    Возвращает:
        tuple[Any, list[Any]]: результат операции и перечень просьб.
    """
    asked: list[Any] = []
    reply: Any = None
    for _ in range(64):
        try:
            request = core.send(reply)
        except StopIteration as stop:
            return stop.value, asked
        asked.append(request)
        reply = None
        if isinstance(request, Fetch):
            reply = _observation(page if page is not None else _page())
        elif isinstance(request, Submit):
            reply = _observation(answer)
    pytest.fail("ядро не завершилось за разумное число шагов")


def _engine(*, warm: bool = True) -> Engine:
    """Собирает движок, готовый к отправке.

    Аргументы:
        warm (bool): считать ли переписку тёплой.

    Возвращает:
        Engine: движок.
    """
    engine = Engine(TransportSettings(), Budget())
    engine._state.outbound = OutboundGovernor()
    if warm:
        # Тепло требует ПОЛОЖИТЕЛЬНОГО свидетельства - наблюдённого входящего.
        #
        # Метка ставится НАСТОЯЩИМ временем, а не далёким будущим: запись из
        # будущего тёплой не считается, и это правильно - часы, подведённые
        # вперёд, не должны греть переписку задним числом.
        engine._state.outbound.note_incoming(NODE_ID, at_ms=int(time() * 1000))
    return engine


def _confirmation(html: str) -> str:
    """Собирает подтверждающий ответ под тот диалог, что на снимке.

    Аргументы:
        html (str): разметка страницы диалога.

    Возвращает:
        str: тело ответа канала.
    """
    from funora._runner import parse_runner_context

    node = parse_runner_context(html).node_name.value
    return _answer(
        objects=[
            {
                "type": "chat_node",
                "id": node,
                "tag": "7f3a9b21",
                "data": {
                    "node": {"id": 283028758, "name": node, "silent": False},
                    "messages": [{"id": 2010613400, "author": 12345678, "html": "<div/>"}],
                    "hasHistory": True,
                },
            }
        ]
    )


def test_a_confirmed_send_reads_the_page_then_submits_once() -> None:
    """Требует одного чтения, одной отправки и подтверждённого исхода.

    Чтение нужно не ради вежливости: со страницы берётся всё - имя диалога, его
    метка, позиция последнего сообщения и защитный токен. Оттуда же снимается
    опора сверки, и потому лишнего обращения нет.

    Возвращает:
        None
    """
    engine = _engine()
    page = _page()
    result, asked = _run(
        engine, engine.send_text(NODE_ID, "привет"), answer=_confirmation(page), page=page
    )

    fetches = [one for one in asked if isinstance(one, Fetch)]
    submits = [one for one in asked if isinstance(one, Submit)]
    assert len(fetches) == 1, f"обращений за страницей {len(fetches)}"
    assert len(submits) == 1, f"отправок {len(submits)}"

    assert NODE_ID in fetches[0].path, fetches[0].path
    assert result.outcome is SendOutcome.CONFIRMED, (result.outcome, result.reason)
    assert result.is_confirmed is True


def test_the_subscription_is_exactly_one_node_and_comes_from_the_page() -> None:
    """Требует подписки ровно на один узел, собранной со страницы.

    Канал подтверждает ТОЛЬКО подписанное - наблюдено контрольной парой. Пустая
    подписка отправку пропускает, а подтверждения не даёт: исход выходил бы
    неподтверждённым при каждой удачной отправке.

    Полная подписка при этом недостижима: метка закладок не наблюдалась.

    Возвращает:
        None
    """
    from funora._runner import parse_runner_context

    engine = _engine()
    page = _page()
    context = parse_runner_context(page)
    _, asked = _run(
        engine, engine.send_text(NODE_ID, "привет"), answer=_confirmation(page), page=page
    )

    submit = next(one for one in asked if isinstance(one, Submit))
    objects = json.loads(submit.fields["objects"])

    assert len(objects) == 1, f"подписка не из одного объекта: {objects}"
    assert objects[0]["type"] == "chat_node"
    assert objects[0]["id"] == context.node_name.value
    assert objects[0]["tag"] == context.chat_tag.value

    action = json.loads(submit.fields["request"])
    assert action["action"] == "chat_message"
    assert action["data"]["node"] == context.node_name.value
    assert action["data"]["last_message"] == int(context.last_message.value)
    assert action["data"]["content"] == "привет"


def test_the_token_goes_out_but_never_shows_up_in_the_request_object() -> None:
    """Требует, чтобы токен уходил в поле и не светился в описании просьбы.

    Просьбу кладут в журнал целиком при разборе неудачи. Токен рядом с ней
    оказался бы там же.

    Возвращает:
        None
    """
    engine = _engine()
    page = _page()
    _, asked = _run(
        engine, engine.send_text(NODE_ID, "привет"), answer=_confirmation(page), page=page
    )

    submit = next(one for one in asked if isinstance(one, Submit))
    token = submit.fields["csrf_token"]
    assert token, "защитный токен не подставлен"

    assert token not in repr(submit.path)
    assert token not in repr(submit.headers)
    assert "X-Requested-With" in submit.headers, "заголовок канала не выставлен"


def test_a_cold_dialogue_refuses_before_any_request_leaves() -> None:
    """Требует отказать холодному обращению ДО отправки.

    Переписка считается холодной, пока не доказано обратное. Отказ обязан
    случиться до того, как что-либо уйдёт: сообщение нельзя отменить.

    Возвращает:
        None
    """
    engine = _engine(warm=False)
    core = engine.send_text(NODE_ID, "привет")

    asked: list[Any] = []
    reply: Any = None
    with pytest.raises(UsageError, match="холодн"):
        for _ in range(64):
            request = core.send(reply)
            asked.append(request)
            reply = _observation(_page()) if isinstance(request, Fetch) else None

    assert not [one for one in asked if isinstance(one, Submit)], "сообщение ушло"


def test_the_governor_is_asked_and_its_refusal_stops_the_send() -> None:
    """Требует спрашивать ограничитель и останавливаться на его отказе.

    Ограничитель спрашивается ДО отправки и ждать не умеет: его пределы часовые.

    Возвращает:
        None
    """
    engine = _engine()
    # Только что писали в этот же диалог - упрёмся в паузу на переписку.
    engine._state.outbound.record(NODE_ID, now_ms=2**62, now_s=1e12)

    core = engine.send_text(NODE_ID, "привет")
    asked: list[Any] = []
    reply: Any = None
    with pytest.raises(BudgetExhaustedError, match="min_interval_per_chat"):
        for _ in range(64):
            request = core.send(reply)
            asked.append(request)
            reply = _observation(_page()) if isinstance(request, Fetch) else None

    assert not [one for one in asked if isinstance(one, Submit)], "сообщение ушло"


def test_the_attempt_is_recorded_before_the_request_leaves() -> None:
    """Требует записать попытку ВПЕРЕДИ запроса, а не после ответа.

    Форма отказа канала не наблюдалась, а транспортный отказ объявлен способным
    иметь последствия. «Не засчитаем, раз не подтвердилось» означало бы не
    считать ровно те отправки, которые могли уйти.

    Проверка обрывает ядро СРАЗУ после просьбы об отправке - до всякого ответа -
    и требует, чтобы попытка уже была записана.

    Возвращает:
        None
    """
    engine = _engine()
    core = engine.send_text(NODE_ID, "привет")

    reply: Any = None
    for _ in range(64):
        request = core.send(reply)
        if isinstance(request, Submit):
            break
        reply = _observation(_page()) if isinstance(request, Fetch) else None
    else:  # pragma: no cover - отправка обязана случиться
        pytest.fail("ядро не дошло до отправки")

    core.close()

    # Часы настоящие: далёкое будущее выстудило бы переписку, и первым назвался
    # бы холод, а проверка не о нём.
    refusal = engine._state.outbound.check(NODE_ID, now_ms=int(time() * 1000), now_s=monotonic())
    assert refusal is not None, "попытка не записана: следующая отправка прошла бы сразу"
    assert refusal.limit == "min_interval_per_chat"


def test_an_answer_without_confirmation_is_unconfirmed_not_an_error() -> None:
    """Требует вернуть исход, а не бросить, когда подтверждения нет.

    Исключение означает, что сообщение НЕ УШЛО. Всё, что случилось после ухода
    запроса, возвращается исходом: брошенное исключение прочиталось бы как
    неудача, а неудачей неоднозначный исход не является.

    Возвращает:
        None
    """
    engine = _engine()
    result, _ = _run(engine, engine.send_text(NODE_ID, "привет"), answer=_answer())

    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == "no_chat_node_in_answer"
    assert result.is_confirmed is False


def test_a_refusal_from_the_channel_is_an_outcome_too() -> None:
    """Требует вернуть отказ канала исходом, а не исключением.

    Возвращает:
        None
    """
    engine = _engine()
    result, _ = _run(
        engine, engine.send_text(NODE_ID, "привет"), answer=_answer(error="что-то не так")
    )

    assert result.outcome is SendOutcome.REFUSED
    assert result.reason == "channel_reported_error"


@pytest.mark.parametrize(
    ("node_id", "text"),
    [
        pytest.param("", "привет", id="пустой диалог"),
        pytest.param("не/адрес", "привет", id="мусор в адресе"),
        pytest.param(NODE_ID, "   ", id="пустой текст"),
    ],
)
def test_bad_arguments_are_refused_before_the_network(node_id: str, text: str) -> None:
    """Требует проверять доводы ДО сети.

    Подставленный в адрес мусор отправил бы запрос неизвестно куда, а пустое
    сообщение не наблюдалось вовсе: что с ним сделает площадка, неизвестно.

    Аргументы:
        node_id (str): идентификатор диалога.
        text (str): текст сообщения.

    Возвращает:
        None
    """
    engine = _engine()
    core = engine.send_text(node_id, text)

    with pytest.raises(ValidationError):
        core.send(None)


def test_a_page_unfit_for_sending_stops_before_the_send() -> None:
    """Требует остановиться на непригодной странице, ничего не отправив.

    Список диалогов без открытого собеседника не несёт ни имени диалога, ни его
    метки. Отправить с него нельзя.

    Возвращает:
        None
    """
    from funora.errors import ProtocolChangedError

    engine = _engine()
    listing = (FIXTURES / "chat.logged.ru.skeleton.txt").read_text(encoding="utf-8")

    core = engine.send_text(NODE_ID, "привет")
    asked: list[Any] = []
    reply: Any = None
    with pytest.raises(ProtocolChangedError, match="не годится для отправки"):
        for _ in range(64):
            request = core.send(reply)
            asked.append(request)
            reply = _observation(listing) if isinstance(request, Fetch) else None

    assert not [one for one in asked if isinstance(one, Submit)], "сообщение ушло"


def test_a_confirmed_send_does_not_read_the_history_at_all() -> None:
    """Требует НЕ сверяться, когда ответ подтвердил отправку.

    Стоимость чтения несимметрична, и на этом держится весь механизм: при
    подтверждённом исходе ответ канала сам несёт новое сообщение, и читать
    историю незачем.

    Сверяйся операция всегда - каждая удачная отправка стоила бы трёх лишних
    обращений и двенадцати секунд ожидания.

    Возвращает:
        None
    """
    from funora._engine import Pause

    engine = _engine()
    page = _page()
    result, asked = _run(
        engine, engine.send_text(NODE_ID, "привет"), answer=_confirmation(page), page=page
    )

    assert result.is_confirmed is True
    assert result.reconciled == "not_attempted", result.reconciled
    assert len([one for one in asked if isinstance(one, Fetch)]) == 1, "история перечитана зря"
    assert not [one for one in asked if isinstance(one, Pause)], "паузы выжданы зря"


def test_an_unconfirmed_send_reconciles_by_the_declared_schedule() -> None:
    """Требует свериться, когда подтверждения нет, и по объявленному расписанию.

    Сверка НИЧЕГО НЕ ОТПРАВЛЯЕТ: решение о повторной отправке принимает
    вызывающий. Проверка требует, чтобы после первой отправки не было ни одной
    второй, сколько бы чтений ни случилось.

    Возвращает:
        None
    """
    from funora._engine import Pause
    from funora.reconciliation import RECONCILE_DELAYS_MS

    engine = _engine()
    result, asked = _run(engine, engine.send_text(NODE_ID, "привет"), answer=_answer())

    assert result.is_confirmed is False
    assert result.reconciled != "not_attempted", "сверка не делалась, хотя исход неоднозначен"

    submits = [one for one in asked if isinstance(one, Submit)]
    assert len(submits) == 1, f"сверка отправила ещё раз: отправок {len(submits)}"

    pauses = [one.ms for one in asked if isinstance(one, Pause)]
    assert pauses, "сверка читала историю без пауз - раньше, чем площадка успеет"
    assert pauses[0] == RECONCILE_DELAYS_MS[0], (pauses, RECONCILE_DELAYS_MS)
    assert pauses == sorted(pauses), f"паузы не возрастают: {pauses}"


def test_reconciliation_never_says_not_delivered() -> None:
    """Требует, чтобы среди исходов сверки не было отрицания.

    Отсутствие сообщения в истории - свидетельство отрицательное. Объявив «не
    отправлено», реализация подтолкнула бы вызывающего написать второй раз.

    Возвращает:
        None
    """
    engine = _engine()
    result, _ = _run(engine, engine.send_text(NODE_ID, "привет"), answer=_answer())

    assert result.reconciled in {"delivered", "absent_from_history", "undetermined"}, (
        result.reconciled
    )
