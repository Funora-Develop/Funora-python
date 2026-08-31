"""Проверки отметки диалога прочитанным.

ЧЕМ ЭТА ОПЕРАЦИЯ ОТЛИЧАЕТСЯ ОТ ВСЕХ ПРОЧИХ. Отдельного запроса у неё НЕТ, и три
недели мы ждали его наблюдения впустую - искали то, чего не существует.

Диалог помечается прочитанным тем, что его узел попал в ПОДПИСКУ обычного опроса
канала обновлений. Форма этого опроса - наша: наши записи показывают её
десятками, страница делает её всякий раз, открыв переписку. Вывод о том, что
подписка снимает пометку непрочитанного, - чужой, от независимой реализации того
же протокола, и проверить его мы не могли: непрочитанность видна у ПОКУПАТЕЛЯ.

Отсюда три главные проверки набора:

  без согласия не уходит ничего, и отказ случается ДО сети;
  в запросе НЕТ поля request - иначе ушло бы сообщение, а не опрос;
  подтверждения операция не выдумывает: возвращает None.

Наблюдено 24-31.08.2026: записи network.runner-opros, network.send-minimal.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import RUNNER_PATH, Engine, Fetch, Submit
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import ValidationError
from funora.operations import OPERATIONS

NODE: Final[str] = "247450736"

THREAD_HTML: Final[str] = (
    '<body data-app-data=\'{"csrf-token": "0123456789abcdef", "userId": 8524891}\'>'
    '<div class="chat chat-float" data-id="247450736" data-tag="a1b2c3d4" '
    'data-name="users-8524891-9310582" data-node-msg="1749300" '
    'data-bookmarks-tag="e5f6a7b8" data-user-id="9310582"></div>'
    '<div class="hidden" data-orders="11223344" data-user="8524891"></div>'
    # Строка списка диалогов: оттуда берётся положение последнего сообщения.
    '<a class="contact-item active" data-id="247450736" data-node-msg="1749300" '
    'data-user-msg="1749299"><div class="media-user-name">покупатель</div></a>'
    # Признак вошедшего. Без него ответ классифицируется как «разметка
    # изменилась», и до самой операции дело не доходит вовсе.
    '<button class="navbar-toggle-logged"></button>'
    '<a class="user-link-dropdown" href="/users/8524891/"></a>'
    '<a class="menu-item-night" href="/night/"></a>'
    '<a href="/users/8524891/" class="menu-item-1"></a>'
    "</body>"
)


def _engine(*, opted_in: bool = True) -> Engine:
    """Собирает движок без сети.

    Аргументы:
        opted_in (bool): Дано ли согласие на непроверенный вывод.

    Возвращает:
        Engine: Движок.
    """
    engine = Engine(TransportSettings(), Budget())
    if opted_in:
        engine._state.opted_in = frozenset({Capability.CHATS_MARK_READ})
    return engine


class _Scripted:
    """Отвечает на просьбы движка страницей диалога и пустым ответом канала."""

    def __init__(self, *, html: str = THREAD_HTML) -> None:
        """Готовит сценарий.

        Аргументы:
            html (str): Разметка страницы диалога.

        Возвращает:
            None
        """
        self.html = html
        self.submits: list[Submit] = []
        self.fetches: list[Fetch] = []

    def run(self, core: Any) -> Any:
        """Прокручивает ядро операции.

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

            if isinstance(request, Submit):
                self.submits.append(request)
                body = json.dumps({"objects": [], "response": None}, ensure_ascii=False)
                reply = _observation(body, "https://funpay.com/runner/")
            elif isinstance(request, Fetch):
                self.fetches.append(request)
                reply = _observation(self.html, f"https://funpay.com{request.path}")
            else:
                reply = None


def _observation(html: str, url: str) -> Observation:
    """Собирает наблюдение.

    Аргументы:
        html (str): Тело ответа.
        url (str): Конечный адрес.

    Возвращает:
        Observation: Наблюдение.
    """
    raw = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=url,
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(raw),
        declared_length=len(raw),
    )


def test_without_consent_nothing_leaves_at_all() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: без согласия не уходит ни одного запроса.

    Отменить действие нельзя: пометить диалог непрочитанным обратно площадка не
    предлагает нигде. Значит отказ обязан случиться ДО сети.

    Возвращает:
        None
    """
    from funora.errors import UsageError

    script = _Scripted()
    core = _engine(opted_in=False).mark_chat_read(NODE)

    with pytest.raises(UsageError) as raised:
        script.run(core)

    assert script.submits == [], "без согласия ушло обращение к каналу"
    assert script.fetches == [], "без согласия ушло чтение страницы диалога"
    assert "FunPayAPI" in str(raised.value), "отказ не называет, кто сообщил о выводе"


def test_the_consent_requirement_is_read_from_the_contract() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: требование согласия живёт в контракте.

    Возвращает:
        None
    """
    contract = OPERATIONS["chats.mark_read"]
    assert contract.request_provenance == "third_party_report"
    assert contract.provenance_source
    # Непроверен именно ВЫВОД, а не форма: это различение и есть весь смысл
    # записи, и потерявшись, оно превратило бы согласие в суеверие.
    assert "ВЫВОД" in contract.provenance_rests_on


def test_the_request_carries_no_action_at_all() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: в запросе нет поля действия.

    Отметка прочтения и отправка сообщения идут ОДНИМ адресом и различаются
    ровно полем request. Положи его сюда - и вместо пометки уйдёт сообщение, а
    отменить сообщение нельзя.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().mark_chat_read(NODE))

    assert len(script.submits) == 1, f"обращений {len(script.submits)}, а ожидалось одно"
    sent = script.submits[0]
    assert sent.path == RUNNER_PATH
    assert "request" not in sent.fields, "в запросе оказалось поле действия - ушло бы сообщение"
    assert set(sent.fields) == {"objects", "csrf_token"}


def test_the_subscription_names_the_dialog_and_carries_no_text() -> None:
    """Требует подписки на тот самый диалог и пустого содержимого.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().mark_chat_read(NODE))

    objects = json.loads(script.submits[0].fields["objects"])
    assert len(objects) == 1, "подписка должна быть на один узел"
    node = objects[0]
    assert node["type"] == "chat_node"
    assert node["id"] == "users-8524891-9310582"
    assert node["tag"] == "a1b2c3d4"
    assert node["data"]["content"] == "", "содержимое непусто - это уже отправка"
    assert node["data"]["last_message"] == 1749300


def test_no_confirmation_is_invented() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: подтверждение не выдумывается.

    Ответ канала несёт состояние подписанных объектов, а пометка непрочитанного
    видна у ПОКУПАТЕЛЯ. Вернуть отсюда «готово» значило бы сообщить об
    исполнении, ничего о нём не зная.

    Возвращает:
        None
    """
    result = _Scripted().run(_engine().mark_chat_read(NODE))
    assert result is None


@pytest.mark.parametrize("node", ["", "  ", "24/../", "247-450", "a b"])
def test_a_bad_identifier_is_refused_before_the_network(node: str) -> None:
    """Требует отказа до сети на непригодном идентификаторе.

    Аргументы:
        node (str): Непригодный идентификатор.

    Возвращает:
        None
    """
    script = _Scripted()
    core = _engine().mark_chat_read(node)

    with pytest.raises(ValidationError):
        script.run(core)

    assert script.fetches == []


def test_the_capability_is_marked_supported_after_the_channel_answers() -> None:
    """Требует выставлять состояние по положительному свидетельству.

    Возвращает:
        None
    """
    engine = _engine()
    _Scripted().run(engine.mark_chat_read(NODE))

    assert engine._state.capabilities[Capability.CHATS_MARK_READ] is CapabilityState.SUPPORTED


def test_a_page_without_the_channel_data_is_refused() -> None:
    """Требует отказа, если со страницы не собрать обращения.

    Возвращает:
        None
    """
    from funora.errors import ProtocolChangedError

    # Страница ВОШЕДШЕГО, но без виджета переписки. Признак вошедшего здесь
    # обязателен: без него ответ отвергается раньше, классификатором, и проверка
    # ловила бы совсем другой отказ - что и случилось с первой её редакцией.
    logged_in_but_useless = (
        '<body data-app-data=\'{"csrf-token": "0123456789abcdef"}\'>'
        '<button class="navbar-toggle-logged"></button>'
        '<a class="user-link-dropdown" href="/users/8524891/"></a>'
        "<div>ни виджета переписки, ни списка диалогов</div>"
        "</body>"
    )
    script = _Scripted(html=logged_in_but_useless)
    core = _engine().mark_chat_read(NODE)

    with pytest.raises(ProtocolChangedError) as raised:
        script.run(core)

    assert "не годится для обращения" in str(raised.value), (
        "отказ пришёл не от проверки пригодности страницы, а откуда-то ещё"
    )
    assert script.submits == [], "страница непригодна, а обращение всё равно ушло"


def test_sending_a_message_also_subscribes_to_the_dialog(tmp_path: Any) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: отправка делает то же самое побочно.

    Отправка кладёт в подписку тот же объект - иначе ответ канала не подтвердит
    её, канал подтверждает только подписанное. Значит отправка попутно помечает
    переписку прочитанной, и объявлено это теперь вслух.

    Проверка стоит здесь, а не в наборе отправки, нарочно: она про СВЯЗЬ двух
    операций, и стоя порознь, они разошлись бы молча.

    Возвращает:
        None
    """
    from funora._engine import IMPLEMENTED

    assert Capability.CHATS_MARK_READ in IMPLEMENTED

    script = _Scripted()
    # Отправке нужен долговечный реестр: без него ограничитель исходящих
    # отказывает, и до канала дело не доходит вовсе.
    engine = Engine(TransportSettings(), Budget(), state_path=tmp_path / "state.json")
    engine._state.opted_in = frozenset({Capability.CHATS_MARK_READ, Capability.CHATS_SEND_TEXT})
    # Исход отправки здесь не проверяется вовсе: набор про ПОБОЧНОЕ действие, и
    # важно только то, что ушло в подписке.
    #
    # declared_cold нужен затем, что переписка синтетическая: ограничитель
    # исходящих не видел ни одного входящего и отверг бы обращение как холодное.
    with contextlib.suppress(Exception):
        script.run(engine.send_text(NODE, "здравствуйте", declared_cold=True))

    assert script.submits, "отправка не дошла до канала - проверка стала пустой"
    objects = json.loads(script.submits[0].fields["objects"])
    assert any(one["type"] == "chat_node" for one in objects), (
        "отправка ушла БЕЗ подписки на диалог. Тогда либо она не подтверждается "
        "ответом канала, либо побочного действия у неё нет - и объявление о нём "
        "в контракте стало неправдой"
    )


def test_dropping_the_declaration_drops_the_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ШЕСТАЯ: снимут объявление - исчезнет и требование.

    Проверка «без согласия отказывает» не показывает, ОТКУДА взялось требование:
    отказ, записанный в коде правилом сам по себе, прошёл бы её точно так же.

    Разница видна ровно в одном опыте - убрать объявление из контракта.

    Возвращает:
        None
    """
    import dataclasses

    plain = dataclasses.replace(
        OPERATIONS["chats.mark_read"],
        request_provenance="",
        provenance_source="",
        provenance_rests_on="",
    )
    monkeypatch.setitem(OPERATIONS, "chats.mark_read", plain)

    script = _Scripted()
    script.run(_engine(opted_in=False).mark_chat_read(NODE))

    assert len(script.submits) == 1, (
        "объявление снято, а отказ остался - значит правило живёт в коде и "
        "переживёт снятие объявления"
    )
