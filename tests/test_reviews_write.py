"""Проверки написания и снятия отзыва.

ГЛАВНОЕ ОТЛИЧИЕ ЭТОГО НАБОРА ОТ ПРОЧИХ - в том, чем объявляется успех.

Независимая реализация того же протокола, у которой взят состав запроса,
объявляет успехом ОТСУТСТВИЕ ОТКАЗА: смотрит код ответа и в тело не заглядывает.
Тело же несёт перерисованный виджет отзыва - готовый положительный признак.

Мы его читаем, и читаем ТЕМ ЖЕ разбором, что и отзыв на странице заказа. Отсюда
и устройство набора: он проверяет не «запрос ушёл», а «исход подтверждён тем, что
вернула площадка».

Второе отличие: applied означает «подтверждено», а не «получилось». Ложь требует
ПОСМОТРЕТЬ заказ, а не повторить вслепую.

Наблюдено нами: data-order и data-author на странице заказа.
Известно от FunPayAPI (FunPayCardinal): адреса, имена полей, ключи ответа.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit
from funora._review_write import (
    RATING_MAX,
    RATING_MIN,
    REVIEW_PATH,
    REVIEW_REMOVE_PATH,
    parse_review_response,
)
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError, UsageError, ValidationError
from funora.operations import OPERATIONS

ORDER: Final[str] = "ZVVQ8FKP"
OWN_ID: Final[str] = "8524891"

ORDER_HTML: Final[str] = (
    "<body data-app-data='"
    # Строкой, а не числом: на настоящей странице этот ключ приходит строкой,
    # и разбор требует именно её. Число читалось бы как строка молча, и
    # разница вышла бы наружу лишь при сверке с другим носителем.
    + json.dumps({"csrf-token": "0123456789abcdef", "userId": OWN_ID}, ensure_ascii=False)
    + "'>"
    '<button class="navbar-toggle-logged"></button>'
    '<a class="user-link-dropdown" href="/users/8524891/"></a>'
    f'<div class="review-container" data-order="{ORDER}" data-rating="5">'
    '<div class="review-item-row" data-author="9310582">спасибо</div>'
    "</div>"
    "</body>"
)


def _widget(rating: str) -> str:
    """Собирает перерисованный виджет отзыва.

    Аргументы:
        rating (str): Оценка в атрибуте. Пустая строка означает «отзыва нет».

    Возвращает:
        str: Кусок разметки.
    """
    return (
        f'<div class="review-container" data-order="{ORDER}" data-rating="{rating}">'
        '<div class="review-item-row" data-author="9310582"></div>'
        "</div>"
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
    """Отвечает страницей заказа и телом ответа на отзыв."""

    def __init__(self, *, html: str = ORDER_HTML, answer: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            html (str): Разметка страницы заказа.
            answer (str | None): Тело ответа на отзыв.

        Возвращает:
            None
        """
        self.html = html
        self.answer = answer if answer is not None else json.dumps({"content": _widget("5")})
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
                reply = _observation(self.answer, f"https://funpay.com{request.path}")
            elif isinstance(request, Fetch):
                self.fetches.append(request)
                reply = _observation(self.html, f"https://funpay.com{request.path}")
            else:
                reply = None


def _engine(*, opted_in: bool = True) -> Engine:
    """Собирает движок без сети.

    Аргументы:
        opted_in (bool): Дано ли согласие.

    Возвращает:
        Engine: Движок.
    """
    engine = Engine(TransportSettings(), Budget())
    if opted_in:
        engine._state.opted_in = frozenset({Capability.REVIEWS_LEAVE, Capability.REVIEWS_REMOVE})
    return engine


def test_success_is_read_from_the_body_not_from_the_absence_of_a_refusal() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: успех читается ПОЛОЖИТЕЛЬНЫМ признаком.

    Сторонний источник объявляет успехом отсутствие отказа. Мы читаем оценку из
    перерисованного виджета и сверяем её с отправленной.

    Проверка ставит крайний случай: ответ БЕЗ признака отказа, но с ЧУЖОЙ
    оценкой. У стороннего правила это успех; у нашего - неподтверждённый исход.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"content": _widget("3")}))
    result = script.run(_engine().leave_review(ORDER, rating=5, text="спасибо"))

    assert result.applied is False, (
        "площадка вернула оценку 3, а просили 5, - и это объявлено успехом"
    )
    assert result.rating.or_none() == 3, "прочитана не та оценка, что вернула площадка"


def test_a_matching_rating_confirms_the_outcome() -> None:
    """Обратная половина: совпавшая оценка подтверждает исход.

    Без неё предыдущая проверка проходила бы и на разборе, который не
    подтверждает ничего никогда.

    Возвращает:
        None
    """
    script = _Scripted()
    result = script.run(_engine().leave_review(ORDER, rating=5, text="спасибо"))

    assert result.applied is True
    assert result.rating.or_none() == 5


def test_removal_is_confirmed_by_the_absence_of_a_rating() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: снятие подтверждается пустой оценкой.

    Пустой атрибут оценки означает «отзыва нет» - это наблюдение со страницы
    заказа, а не неудача поиска. Ровно оно и служит подтверждением снятия.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"content": _widget("")}))
    result = script.run(_engine().remove_review(ORDER))

    assert script.submits[0].path == REVIEW_REMOVE_PATH
    assert result.applied is True
    assert result.rating.or_none() == 0


def test_a_removal_that_left_the_rating_is_not_confirmed() -> None:
    """Требует не считать снятие состоявшимся при оставшейся оценке.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"content": _widget("5")}))
    result = script.run(_engine().remove_review(ORDER))

    assert result.applied is False, "оценка осталась, а снятие объявлено состоявшимся"


def test_without_consent_nothing_leaves_at_all() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: без согласия не уходит ни одного запроса.

    Отзыв виден покупателю и всем посетителям профиля. Отказ обязан случиться ДО
    сети.

    Возвращает:
        None
    """
    script = _Scripted()
    core = _engine(opted_in=False).leave_review(ORDER, rating=5, text="спасибо")

    with pytest.raises(UsageError) as raised:
        script.run(core)

    assert script.submits == [] and script.fetches == []
    assert "FunPayAPI" in str(raised.value)


def test_the_consent_requirement_is_read_from_the_contract() -> None:
    """Требует, чтобы согласие спрашивалось по контракту.

    Возвращает:
        None
    """
    for name in ("reviews.leave", "reviews.remove"):
        contract = OPERATIONS[name]
        assert contract.request_provenance == "third_party_report", name
        assert contract.provenance_source, name
        assert contract.provenance_rests_on, name


def test_dropping_the_declaration_drops_the_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Требует, чтобы снятие объявления снимало и требование согласия.

    Возвращает:
        None
    """
    import dataclasses

    plain = dataclasses.replace(
        OPERATIONS["reviews.leave"],
        request_provenance="",
        provenance_source="",
        provenance_rests_on="",
    )
    monkeypatch.setitem(OPERATIONS, "reviews.leave", plain)

    script = _Scripted()
    script.run(_engine(opted_in=False).leave_review(ORDER, rating=5, text="да"))

    assert len(script.submits) == 1, "объявление снято, а отказ остался"


def test_the_author_is_taken_from_the_page_not_from_the_caller() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: автор берётся со страницы.

    Просить идентификатор автора у вызывающего значило бы дать ему написать
    отзыв от ЧУЖОГО имени. Он читается из настроек страницы, и подставить туда
    другой неоткуда.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().leave_review(ORDER, rating=4, text="норм"))

    sent = script.submits[0]
    assert sent.path == REVIEW_PATH
    assert sent.fields["authorId"] == OWN_ID
    assert sent.fields["orderId"] == ORDER
    assert sent.fields["rating"] == "4"
    assert sent.fields["text"] == "норм"
    assert "csrf_token" in sent.fields


def test_removal_sends_no_rating_and_no_text() -> None:
    """Требует, чтобы у снятия не было полей оценки и текста.

    У этой точки их нет вовсе - значит снятие не выражается отправкой пустого
    отзыва. Отправить их значило бы сделать не то, о чём просили.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"content": _widget("")}))
    script.run(_engine().remove_review(ORDER))

    sent = script.submits[0]
    assert set(sent.fields) == {"authorId", "orderId", "csrf_token"}


@pytest.mark.parametrize("rating", [0, -1, 6, 100])
def test_an_unobserved_rating_is_refused_before_the_network(rating: int) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: неизвестная оценка не уходит.

    Наблюдены оценки от одного до пяти. Что площадка сделает с шестёркой либо с
    нулём, никто не видел, и отправлять непроверенное мы не станем.

    Аргументы:
        rating (int): Оценка вне наблюдённого предела.

    Возвращает:
        None
    """
    script = _Scripted()
    core = _engine().leave_review(ORDER, rating=rating, text="да")

    with pytest.raises(ValidationError) as raised:
        script.run(core)

    assert script.fetches == [], "оценка непригодна, а страница всё равно прочитана"
    assert f"{RATING_MIN}..{RATING_MAX}" in str(raised.value)


@pytest.mark.parametrize("order", ["", "  ", "ZVV/../", "ZVV-8F"])
def test_a_bad_order_number_is_refused_before_the_network(order: str) -> None:
    """Требует отказа до сети на непригодном номере заказа.

    Аргументы:
        order (str): Непригодный номер.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().leave_review(order, rating=5, text="да"))
    assert script.fetches == []


def test_a_body_without_the_widget_is_not_a_failure() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ШЕСТАЯ: нет виджета - это «не подтвердилось».

    Ответ без перерисованного виджета не означает неудачи: запрос мог
    состояться. Объявить здесь провал так же неверно, как объявить успех.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"msg": "что-то пошло не так"}))
    result = script.run(_engine().leave_review(ORDER, rating=5, text="да"))

    assert result.applied is False
    assert result.message == "что-то пошло не так"
    assert result.rating.or_none() is None, "оценки не было, а прочиталась"


def test_a_body_that_is_not_json_is_refused() -> None:
    """Требует отвергать неразобравшееся тело.

    Возвращает:
        None
    """
    script = _Scripted(answer="<html>ошибка</html>")
    with pytest.raises(ProtocolChangedError):
        script.run(_engine().leave_review(ORDER, rating=5, text="да"))


def test_a_page_without_the_own_identifier_is_refused() -> None:
    """Требует отказа, если собственный идентификатор не читается.

    Подставить сюда что-нибудь значило бы написать отзыв от чужого имени.

    Возвращает:
        None
    """
    without = ORDER_HTML.replace(f'"userId": "{OWN_ID}"', '"nothing": 1')
    script = _Scripted(html=without)

    with pytest.raises(ProtocolChangedError) as raised:
        script.run(_engine().leave_review(ORDER, rating=5, text="да"))

    assert "чужого имени" in str(raised.value)
    assert script.submits == []


def test_the_capability_is_marked_by_a_parsed_answer() -> None:
    """Требует выставлять состояние по разобранному ответу.

    Неподтверждённый исход говорит о НАШЕМ чтении, а не о праве аккаунта.
    Право есть - ответ пришёл и разобрался.

    Возвращает:
        None
    """
    engine = _engine()
    script = _Scripted(answer=json.dumps({"content": _widget("3")}))
    script.run(engine.leave_review(ORDER, rating=5, text="да"))

    assert engine._state.capabilities[Capability.REVIEWS_LEAVE] is CapabilityState.SUPPORTED
    assert engine._state.capabilities[Capability.REVIEWS_REMOVE] is not CapabilityState.SUPPORTED


@pytest.mark.parametrize("payload", ["строка", 42, None, []])
def test_an_unusable_payload_is_refused(payload: Any) -> None:
    """Требует отвергать непригодное тело, а не толковать его.

    Аргументы:
        payload (Any): Непригодное тело.

    Возвращает:
        None
    """
    from datetime import UTC, datetime

    with pytest.raises(ProtocolChangedError):
        parse_review_response(payload, expected_rating=5, observed_at=datetime.now(UTC))
