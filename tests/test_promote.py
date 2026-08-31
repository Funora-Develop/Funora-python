"""Проверки поднятия предложений раздела.

ЧЕМ ЭТА ОПЕРАЦИЯ ОТЛИЧАЕТСЯ ОТ ПРОЧИХ ЗАПИСЕЙ. Отправку сообщения можно
повторить и получить второе сообщение - плохо, но поправимо извинением. Правку
цены можно вернуть. Поднятие вернуть НЕЛЬЗЯ: оно тратит суточный предел, и
второго за те же сутки не будет.

Отсюда главные проверки набора, и обе про отказ читать ответ наугад:

  без признака отказа ответ отвергается, а не толкуется как успех;
  отказ площадки остаётся ИСХОДОМ, а не исключением - вместе со сроком, который
  единственный говорит, когда пробовать снова.

Наблюдено 31.08.2026: POST /lots/raise, ответ {error, msg, unlock_at, wait}.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Submit
from funora._raise import RAISE_PATH, parse_raise
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError, UnsupportedCapabilityError, ValidationError

GAME: Final[str] = "283"
NODE: Final[str] = "922"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)


def _observation(payload: Any) -> Observation:
    """Собирает наблюдение с телом-ответом.

    Аргументы:
        payload (Any): что вернула площадка.

    Возвращает:
        Observation: наблюдение.
    """
    body = json.dumps(payload, ensure_ascii=False)
    raw = body.encode("utf-8")
    return Observation(
        status=200,
        final_url="https://funpay.com/lots/raise",
        html=body,
        elapsed_ms=10,
        redirects=0,
        content_length=len(raw),
        declared_length=len(raw),
    )


def _engine() -> Engine:
    """Собирает движок без сети.

    Возвращает:
        Engine: движок.
    """
    return Engine(TransportSettings(), Budget())


def _drive(core: Any, payload: Any) -> tuple[Any, list[Any]]:
    """Прокручивает ядро, отвечая одним ответом.

    Аргументы:
        core (Any): сопрограмма.
        payload (Any): тело ответа.

    Возвращает:
        tuple[Any, list[Any]]: итог и перечень просьб.
    """
    asked: list[Any] = []
    reply: Any = None
    while True:
        try:
            request = core.send(reply)
        except StopIteration as stop:
            return stop.value, asked
        asked.append(request)
        reply = _observation(payload) if isinstance(request, Submit) else None


def test_a_response_without_the_refusal_flag_is_refused() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: нет признака отказа - нет и вывода об успехе.

    Площадка отвечает признаком ОТКАЗА, а не успеха. Считать отсутствие поля
    успехом значило бы объявить поднятие состоявшимся, ничего о нём не зная, - а
    проверить повтором нельзя: повтор тратит суточный предел.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError) as raised:
        parse_raise({"msg": "готово", "wait": 0}, observed_at=WHEN)
    assert "error" in str(raised.value)


@pytest.mark.parametrize("payload", ["строка", 42, None, [], {"error": "нет"}, {"error": 1}])
def test_an_unusable_response_is_refused(payload: Any) -> None:
    """Требует отвергать непригодный ответ, а не толковать его.

    Единица вместо истины исключается отдельно: в Python истина - это единица, и
    error=1 прочиталось бы как отказ, а error=0 как успех, ни разу не будучи
    логическим.

    Аргументы:
        payload (Any): непригодное тело ответа.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_raise(payload, observed_at=WHEN)


def test_a_refusal_stays_an_outcome_and_carries_the_wait() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: отказ - это исход, а не исключение.

    Отказав по неистёкшему остыванию, площадка называет и срок. Бросить здесь
    исключение значило бы выбросить срок вместе с ним, а он единственный
    говорит, когда пробовать снова.

    Возвращает:
        None
    """
    result = parse_raise(
        {"error": True, "msg": "подождите", "unlock_at": "2026-08-31 12:00:00", "wait": 3600},
        observed_at=WHEN,
    )

    assert result.raised is False
    assert result.message == "подождите"
    assert result.wait_seconds.or_none() == 3600
    assert result.unlock_at.or_none() == "2026-08-31 12:00:00"


def test_success_is_the_negation_of_the_refusal_flag() -> None:
    """Требует читать успех ОТРИЦАНИЕМ признака отказа.

    Возвращает:
        None
    """
    result = parse_raise({"error": False, "msg": "поднято"}, observed_at=WHEN)

    assert result.raised is True
    assert result.message == "поднято"
    # Срока в ответе не было - и поле честно говорит, что не наблюдалось.
    assert result.wait_seconds.or_none() is None
    assert result.unlock_at.or_none() is None


def test_a_boolean_wait_is_not_a_number_of_seconds() -> None:
    """Требует, чтобы wait=True не прочиталось как «ждать единицу».

    Возвращает:
        None
    """
    result = parse_raise({"error": True, "wait": True}, observed_at=WHEN)
    assert result.wait_seconds.or_none() is None


def test_the_operation_sends_game_and_node_and_nothing_else() -> None:
    """Требует отправлять ровно два наблюдённых поля.

    Идентификатора предложения в запросе нет: поднимается ВЕСЬ раздел.

    Возвращает:
        None
    """
    result, asked = _drive(_engine().promote_lots(GAME, NODE), {"error": False, "msg": "готово"})

    submits = [one for one in asked if isinstance(one, Submit)]
    assert len(submits) == 1, f"запросов {len(submits)}, а поднятие делается один раз"
    assert submits[0].path == RAISE_PATH
    assert submits[0].fields == {"game_id": GAME, "node_id": NODE}
    assert result.raised is True


def test_a_refusal_never_becomes_a_second_request() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: отказ не приводит к повтору.

    Повтор тратит невосполнимый суточный предел. Контракт объявляет операцию
    небезопасной и требует сверки вместо повтора.

    Возвращает:
        None
    """
    result, asked = _drive(
        _engine().promote_lots(GAME, NODE),
        {"error": True, "msg": "рано", "wait": 60},
    )

    submits = [one for one in asked if isinstance(one, Submit)]
    assert len(submits) == 1, f"после отказа ушло ещё {len(submits) - 1} запросов"
    assert result.raised is False


@pytest.mark.parametrize(
    ("game", "node"),
    [("", NODE), (GAME, ""), ("abc", NODE), (GAME, "9o2"), (GAME, "922/../")],
)
def test_a_bad_identifier_is_refused_before_the_network(game: str, node: str) -> None:
    """Требует отказа ДО запроса на непригодном идентификаторе.

    Здесь это важнее обычного: ушедший запрос уже потратил бы предел.

    Аргументы:
        game (str): игра.
        node (str): раздел.

    Возвращает:
        None
    """
    core = _engine().promote_lots(game, node)
    with pytest.raises(ValidationError):
        core.send(None)


def test_an_unsupported_capability_stops_the_operation() -> None:
    """Требует, чтобы недоступное поднятие не уходило запросом.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.LOTS_PROMOTE] = CapabilityState.UNSUPPORTED

    core = engine.promote_lots(GAME, NODE)
    with pytest.raises(UnsupportedCapabilityError):
        core.send(None)


def test_a_refusal_still_marks_the_capability_supported() -> None:
    """Требует считать возможность доступной и при отказе по остыванию.

    Отказ по остыванию говорит, что операция доступна: она сработала бы, приди
    запрос позже. Недоступной её делает не остывание, а отсутствие права
    поднимать.

    Возвращает:
        None
    """
    engine = _engine()
    _drive(engine.promote_lots(GAME, NODE), {"error": True, "msg": "рано", "wait": 60})

    assert engine.capability(Capability.LOTS_PROMOTE) is CapabilityState.SUPPORTED


def test_a_body_that_is_not_json_is_refused() -> None:
    """Требует отвергать неразобравшееся тело, а не считать поднятие удачным.

    Возвращает:
        None
    """
    core = _engine().promote_lots(GAME, NODE)
    request = core.send(None)
    while not isinstance(request, Submit):
        request = core.send(None)

    body = "<html>что-то пошло не так</html>"
    raw = body.encode("utf-8")
    with pytest.raises(ProtocolChangedError):
        core.send(
            Observation(
                status=200,
                final_url="https://funpay.com/lots/raise",
                html=body,
                elapsed_ms=10,
                redirects=0,
                content_length=len(raw),
                declared_length=len(raw),
            )
        )


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_unlock_at_is_not_an_observed_moment(blank: str) -> None:
    """Требует, чтобы пустой срок не выдавался за наблюдённый.

    Разница здесь не косметическая. Наблюдённый пустой срок молча сказал бы
    «поднимать можно прямо сейчас, вот доказательство», и вызывающий пошёл бы
    тратить суточный предел на пустой строке. Ненаблюдённый честно говорит, что
    срока площадка не назвала.

    Аргументы:
        blank (str): пустое значение срока.

    Возвращает:
        None
    """
    result = parse_raise({"error": True, "unlock_at": blank}, observed_at=WHEN)

    assert result.unlock_at.or_none() is None
    assert result.unlock_at.reason == "unlock_at_not_in_response"


def test_a_choice_url_cancels_the_success() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: ответ с адресом окна выбора - не поднятие.

    Площадка отвечает без признака отказа, но с адресом окна выбора
    подкатегорий: она спрашивает, что поднимать, а не сообщает о поднятии.
    Прочитать это успехом значило бы сказать «поднято» там, где не поднято
    ничего, и отправить вызывающего ждать сутки впустую.

    Ответа такого вида мы сами не наблюдали - знаем о нём от независимой
    реализации того же протокола.

    Возвращает:
        None
    """
    result = parse_raise(
        {"error": False, "msg": "", "url": "https://funpay.com/lots/raise?modal=1"},
        observed_at=WHEN,
    )

    assert result.raised is False, "ответ с адресом окна выбора прочитан успехом"
    assert result.choice_url.or_none() == "https://funpay.com/lots/raise?modal=1"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_url_does_not_cancel_a_success(blank: str) -> None:
    """Требует, чтобы пустой адрес не отменял поднятия.

    Обратная половина предыдущей проверки. Отменять успех по пустой строке
    значило бы объявлять несостоявшимся то, что состоялось, - и вызывающий
    поднял бы второй раз, потратив второй предел.

    Аргументы:
        blank (str): пустой адрес.

    Возвращает:
        None
    """
    result = parse_raise({"error": False, "url": blank}, observed_at=WHEN)

    assert result.raised is True
    assert result.choice_url.or_none() is None


def test_a_refusal_with_a_url_is_still_a_refusal() -> None:
    """Требует, чтобы признак отказа и адрес окна не спорили.

    Возвращает:
        None
    """
    result = parse_raise({"error": True, "url": "https://funpay.com/x"}, observed_at=WHEN)
    assert result.raised is False
