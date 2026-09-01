"""Проверки чтения «покупатель смотрит».

РАСКОЛ НАБЛЮДЕНИЯ ЗДЕСЬ ГЛАВНОЕ, и он определяет весь набор.

ПОДПИСКА НАША: объект этого вида лежит в семи наших записях канала, и состав его
известен - признак, идентификатор, метка.

ОТВЕТ НА НЕЁ МЫ НЕ ВИДЕЛИ НИ РАЗУ. Что приходит внутри, известно от независимой
реализации того же протокола.

Отсюда устройство разбора: разметка сохраняется КАК ЕСТЬ, а ссылка и подпись
читаются из неё отдельно. Не разобралась - поля ненаблюдённые, а разметка при
вызывающем.

И отсюда же главная осторожность: всё, что не похоже на разметку, читается как
«не смотрит», а не как поломка. Ответа мы не видели, и объявлять поломкой то,
чего не понимаем, значило бы ломать чтение на первом же непредвиденном ответе.

Наблюдено 24-31.08.2026: network.runner-opros, network.send-minimal и ещё пять.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import RUNNER_PATH, Engine, Fetch, Submit
from funora._transport import Observation, TransportSettings
from funora._viewing import VIEWING_OBJECT, BuyerViewing, parse_buyer_viewing
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError, ValidationError

WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)
NODE: Final[str] = "247450736"
BUYER: Final[str] = "9310582"
OTHER: Final[str] = "1122334"

THREAD_HTML: Final[str] = (
    "<body data-app-data='"
    + json.dumps({"csrf-token": "0123456789abcdef", "userId": "8524891"}, ensure_ascii=False)
    + "'>"
    '<div class="chat chat-float" data-id="247450736" data-tag="a1b2c3d4" '
    'data-name="users-8524891-9310582" data-node-msg="1749300" '
    'data-bookmarks-tag="e5f6a7b8" data-user-id="9310582"></div>'
    '<div class="hidden" data-orders="11223344" data-user="8524891"></div>'
    '<a class="contact-item active" data-id="247450736" data-node-msg="1749300" '
    'data-user-msg="1749299"><div class="media-user-name">покупатель</div></a>'
    '<button class="navbar-toggle-logged"></button>'
    '<a class="user-link-dropdown" href="/users/8524891/"></a>'
    "</body>"
)

VIEWING_MARKUP: Final[str] = (
    '<div class="chat-panel"><a href="https://funpay.com/lots/offer?id=75289502">'
    "Стим аккаунт с играми</a></div>"
)


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


class _Scripted:
    """Отвечает страницей диалога и ответом канала."""

    def __init__(self, answer: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            answer (str | None): Тело ответа канала.

        Возвращает:
            None
        """
        self.answer = answer if answer is not None else json.dumps({"objects": []})
        self.submits: list[Submit] = []
        self.fetches: list[Fetch] = []

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
            if isinstance(request, Submit):
                self.submits.append(request)
                reply = _observation(self.answer, "https://funpay.com/runner/")
            elif isinstance(request, Fetch):
                self.fetches.append(request)
                reply = _observation(THREAD_HTML, f"https://funpay.com{request.path}")
            else:
                reply = None


def _engine() -> Engine:
    """Собирает движок без сети.

    Возвращает:
        Engine: Движок.
    """
    return Engine(TransportSettings(), Budget())


def _answer(*objects: dict[str, Any]) -> str:
    """Собирает ответ канала.

    Аргументы:
        objects (dict[str, Any]): Объекты ответа.

    Возвращает:
        str: Тело.
    """
    return json.dumps({"objects": list(objects)}, ensure_ascii=False)


def test_the_subscription_uses_our_observed_shape() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: подписка собирается по НАШЕМУ наблюдению.

    Состав её мы видели в семи записях: признак, идентификатор, метка. Это
    единственная половина этой операции, стоящая на нашем наблюдении, и портить
    её чужими догадками нельзя.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().read_buyer_viewing(NODE, (BUYER, OTHER)))

    assert len(script.submits) == 1
    sent = script.submits[0]
    assert sent.path == RUNNER_PATH
    objects = json.loads(sent.fields["objects"])

    assert [one["type"] for one in objects] == [VIEWING_OBJECT, VIEWING_OBJECT]
    assert [one["id"] for one in objects] == [BUYER, OTHER]
    assert all(one["data"] is False for one in objects)
    assert all(one["tag"] == "00000000" for one in objects)
    # Действия в запросе нет: это опрос, а не отправка.
    assert "request" not in sent.fields


def test_the_order_of_the_answer_is_the_order_that_was_asked() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: порядок сохраняется СПРОШЕННЫЙ.

    Ответ канала перечисляет объекты как попало. Вызывающий, сопоставлявший по
    месту, получил бы ЧУЖОГО покупателя - и показал бы продавцу, что не тот
    смотрит его лот.

    Возвращает:
        None
    """
    # Ответ нарочно перевёрнут.
    script = _Scripted(
        _answer(
            {"type": VIEWING_OBJECT, "id": OTHER, "tag": "t2", "data": False},
            {"type": VIEWING_OBJECT, "id": BUYER, "tag": "t1", "data": {"html": VIEWING_MARKUP}},
        )
    )
    result = script.run(_engine().read_buyer_viewing(NODE, (BUYER, OTHER)))

    assert [one.buyer_id for one in result] == [BUYER, OTHER], "порядок взят из ответа"
    assert result[0].viewing is True
    assert result[1].viewing is False


def test_the_markup_is_kept_as_is_and_the_link_is_read_from_it() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: разметка сохраняется целиком.

    Ответа этой точки мы не видели. Не разберись наши поля - у вызывающего
    останется то, из чего он поймёт сам.

    Возвращает:
        None
    """
    script = _Scripted(
        _answer({"type": VIEWING_OBJECT, "id": BUYER, "tag": "t", "data": {"html": VIEWING_MARKUP}})
    )
    one = script.run(_engine().read_buyer_viewing(NODE, (BUYER,)))[0]

    assert one.raw_html.or_none() == VIEWING_MARKUP, "разметка изменилась при чтении"
    assert one.lot_href.or_none() == "https://funpay.com/lots/offer?id=75289502"
    assert one.lot_text.or_none() == "Стим аккаунт с играми"


def test_markup_that_does_not_parse_keeps_the_markup() -> None:
    """Требует сохранять разметку, даже если ссылка из неё не прочиталась.

    Это и есть смысл поля: разбор строился по ЧУЖОМУ описанию, и ошибиться он
    вправе. Потеряв разметку вместе с разбором, мы отняли бы у вызывающего
    последнее.

    Возвращает:
        None
    """
    no_link = "<div>смотрит что-то, а ссылки нет</div>"
    script = _Scripted(
        _answer({"type": VIEWING_OBJECT, "id": BUYER, "tag": "t", "data": {"html": no_link}})
    )
    one = script.run(_engine().read_buyer_viewing(NODE, (BUYER,)))[0]

    assert one.viewing is True
    assert one.raw_html.or_none() == no_link, "разметка потеряна вместе с разбором"
    assert one.lot_href.or_none() is None
    assert one.lot_text.or_none() is None


@pytest.mark.parametrize(
    "data",
    [False, None, 0, "", [], {}, {"html": None}, {"html": ""}, {"html": {"desktop": ""}}],
)
def test_anything_unlike_markup_reads_as_not_viewing(data: Any) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: непонятное читается как «не смотрит».

    Ответа мы не видели. Объявить поломкой то, чего не понимаем, значило бы
    ломать чтение на первом же непредвиденном ответе - у операции, которую зовут
    в цикле опроса.

    «Не смотрит» здесь не догадка, а осторожный отказ утверждать обратное.

    Аргументы:
        data (Any): Непредвиденное содержимое.

    Возвращает:
        None
    """
    one = parse_buyer_viewing(
        {"type": VIEWING_OBJECT, "id": BUYER, "data": data}, buyer_id=BUYER, observed_at=WHEN
    )

    assert one.viewing is False
    assert one.lot_href.or_none() is None
    assert one.raw_html.or_none() is None


def test_a_nested_desktop_markup_is_read() -> None:
    """Требует читать разметку и из вложенного вида.

    Сторонний источник берёт её из ключа desktop. Читаются оба вида: строка
    напрямую и вложенный ключ - потому что видели мы ни того, ни другого.

    Возвращает:
        None
    """
    one = parse_buyer_viewing(
        {"data": {"html": {"desktop": VIEWING_MARKUP}}}, buyer_id=BUYER, observed_at=WHEN
    )
    assert one.viewing is True
    assert one.lot_href.or_none() == "https://funpay.com/lots/offer?id=75289502"


def test_a_buyer_absent_from_the_answer_is_not_viewing() -> None:
    """Требует, чтобы неответивший получил наблюдение, а не пропал.

    Пропустив его, мы вернули бы перечень короче спрошенного, и вызывающий
    сопоставил бы записи по месту - то есть чужие.

    Возвращает:
        None
    """
    script = _Scripted(_answer())
    result = script.run(_engine().read_buyer_viewing(NODE, (BUYER, OTHER)))

    assert len(result) == 2, "перечень короче спрошенного"
    assert all(one.viewing is False for one in result)


def test_objects_of_other_types_are_ignored() -> None:
    """Требует не принимать чужой объект за просмотр.

    Ответ канала несёт объекты всех подписок разом, и брать первый попавшийся
    значило бы читать счётчик заказов как просмотр лота.

    Возвращает:
        None
    """
    # ЧУЖОЙ ОБЪЕКТ СТОИТ ПОСЛЕДНИМ НАРОЧНО. Стоя первым, он перезаписывался бы
    # настоящим, и проверка проходила бы даже у разбора, который вида не
    # смотрит. Порядок здесь - половина проверки.
    script = _Scripted(
        _answer(
            {"type": VIEWING_OBJECT, "id": BUYER, "tag": "t", "data": False},
            {"type": "orders_counters", "id": BUYER, "data": {"html": VIEWING_MARKUP}},
        )
    )
    one = script.run(_engine().read_buyer_viewing(NODE, (BUYER,)))[0]

    assert one.viewing is False, "чужой объект прочитан как просмотр"


@pytest.mark.parametrize(
    ("node", "buyers"),
    [("", (BUYER,)), (NODE, ()), (NODE, ("",)), (NODE, ("не-число",)), ("24/../", (BUYER,))],
)
def test_bad_input_is_refused_before_the_network(node: str, buyers: tuple[str, ...]) -> None:
    """Требует отказа ДО сети на непригодном вводе.

    Аргументы:
        node (str): Диалог.
        buyers (tuple[str, ...]): Покупатели.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().read_buyer_viewing(node, buyers))
    assert script.fetches == []


def test_a_body_that_is_not_json_is_refused() -> None:
    """Требует отвергать неразобравшийся ответ канала.

    Здесь отказ уместен: не разобралось ТЕЛО, а не содержимое объекта. Первое
    означает, что мы читаем не тот ответ.

    Возвращает:
        None
    """
    script = _Scripted("<html>нет</html>")
    with pytest.raises(ProtocolChangedError):
        script.run(_engine().read_buyer_viewing(NODE, (BUYER,)))


def test_the_capability_is_marked_after_a_parsed_answer() -> None:
    """Требует выставлять состояние по разобранному ответу.

    Возвращает:
        None
    """
    engine = _engine()
    _Scripted().run(engine.read_buyer_viewing(NODE, (BUYER,)))
    assert engine._state.capabilities[Capability.CHATS_BUYER_VIEWING] is CapabilityState.SUPPORTED


def test_the_model_has_no_invented_fields() -> None:
    """Требует, чтобы у записи не завелось полей сверх объявленных.

    Ответа мы не видели. Всякое лишнее поле здесь - чистая выдумка, и заметить
    её иначе нечем.

    Возвращает:
        None
    """
    assert set(BuyerViewing.__dataclass_fields__) == {
        "buyer_id",
        "viewing",
        "lot_href",
        "lot_text",
        "raw_html",
        "observed_at",
    }
