"""Проверки включения и выключения лота.

ЧЕМ ЭТИ ДВЕ ОПЕРАЦИИ ОТЛИЧАЮТСЯ ОТ ВСЕХ ПРОЧИХ. Это единственное место пакета,
где уходит поле, которого мы сами не наблюдали.

Оба наших снимка сохранения лота сняты с ОТМЕЧЕННЫМ флажком active. Что уходит
при снятом, известно от независимой реализации того же протокола: она шлёт поле
ВСЕГДА, при выключенном лоте - пустой строкой. Наше собственное рассуждение
говорило обратное - что снятый флажок по устройству форм не уходит вовсе.

Отсюда три главные проверки набора:

  без явного согласия не уходит ничего, и отказ случается ДО сети;
  требование согласия читается из КОНТРАКТА, а не стоит здесь литералом;
  состояние показа сверяется ПОСЛЕ сохранения - иначе мы сообщали бы об
  исполнении, ничего о нём не зная.

Наблюдено 30-31.08.2026: форма lot-edit.logged.ru, запрос network.lot-save-form.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit
from funora._lot_form import ACTIVE_FIELD, SAVE_PATH, LotForm
from funora._observed import Observed
from funora._result import Completeness
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability
from funora.errors import PreconditionFailedError, UnexpectedResponseError, UsageError
from funora.operations import OPERATIONS

NODE: Final[str] = "922"
OFFER: Final[str] = "75289502"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)

FORM_PATH: Final[str] = "/lots/offerEdit"


def _form(*, active: bool) -> LotForm:
    """Собирает форму лота без разметки.

    Аргументы:
        active (bool): Отмечен ли флажок показа.

    Возвращает:
        LotForm: Форма.
    """
    fields = {
        "offer_id": OFFER,
        "node_id": NODE,
        "price": "2.50",
        "csrf_token": "0123456789abcdef",
        "fields[desc][ru]": "описание, которое стереть нельзя",
        "fields[payment_msg][ru]": "сообщение покупателю",
    }
    checked = frozenset({ACTIVE_FIELD}) if active else frozenset()
    return LotForm(
        offer_id=OFFER,
        node_id=NODE,
        price_text="2.50",
        currency_symbol=Observed.present("₽"),
        is_active=active,
        revision="deadbeefdeadbeef",
        fields=fields,
        checked=checked,
        observed_at=WHEN,
        completeness=Completeness.COMPLETE,
        reason="",
        defects=(),
    )


def _engine(*, opted_in: bool = True) -> Engine:
    """Собирает движок без сети.

    Аргументы:
        opted_in (bool): Дано ли согласие на непроверенный запрос.

    Возвращает:
        Engine: Движок.
    """
    engine = Engine(TransportSettings(), Budget())
    if opted_in:
        engine._state.opted_in = frozenset({Capability.LOTS_ACTIVATE, Capability.LOTS_DEACTIVATE})
    return engine


class _Scripted:
    """Отвечает на просьбы движка по заранее назначенному сценарию."""

    def __init__(self, engine: Engine, forms: list[LotForm], *, landing: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            engine (Engine): Движок, чей разбор формы подменяется.
            forms (list[LotForm]): Формы по порядку чтения.
            landing (str | None): Адрес, куда приводит сохранение.

        Возвращает:
            None
        """
        self.engine = engine
        self.forms = list(forms)
        self.landing = landing if landing is not None else f"/lots/{NODE}/trade"
        self.submits: list[Submit] = []
        self.fetches: list[Fetch] = []

    def run(self, core: Any) -> Any:
        """Прокручивает ядро операции.

        Аргументы:
            core (Any): Сопрограмма операции.

        Возвращает:
            Any: Итог операции.
        """
        reply: Any = None
        while True:
            try:
                request = core.send(reply)
            except StopIteration as stop:
                return stop.value

            if isinstance(request, Submit):
                self.submits.append(request)
                reply = Observation(
                    status=200,
                    final_url=f"https://funpay.com{self.landing}",
                    html="",
                    elapsed_ms=10,
                    redirects=1,
                    content_length=0,
                    declared_length=0,
                )
            elif isinstance(request, Fetch):
                self.fetches.append(request)
                reply = Observation(
                    status=200,
                    final_url=f"https://funpay.com{request.path}",
                    html="<html></html>",
                    elapsed_ms=10,
                    redirects=0,
                    content_length=13,
                    declared_length=13,
                )
            else:
                reply = None


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Подменяет чтение формы заранее назначенными формами.

    Аргументы:
        monkeypatch (pytest.MonkeyPatch): Подменятель.

    Возвращает:
        Any: Изготовитель сценария.
    """

    def make(engine: Engine, forms: list[LotForm], *, landing: str | None = None) -> _Scripted:
        """Готовит сценарий и подменяет чтение формы.

        Аргументы:
            engine (Engine): Движок.
            forms (list[LotForm]): Формы по порядку чтения.
            landing (str | None): Адрес после сохранения.

        Возвращает:
            _Scripted: Сценарий.
        """
        queue = list(forms)

        def fake_read(self: Engine, node_id: str, offer_id: str) -> Any:
            """Отдаёт следующую назначенную форму, сходив в сеть.

            Аргументы:
                self (Engine): Движок.
                node_id (str): Раздел.
                offer_id (str): Предложение.

            Возвращает:
                Any: Сопрограмма, отдающая форму.
            """

            def core() -> Any:
                yield Fetch(f"{FORM_PATH}?id={offer_id}")
                return queue.pop(0)

            return core()

        monkeypatch.setattr(Engine, "read_lot_form", fake_read, raising=True)
        return _Scripted(engine, forms, landing=landing)

    return make


def test_without_consent_nothing_leaves_at_all(patched: Any) -> None:
    """ГЛАВНАЯ ПРОВЕРКА: без согласия не уходит ни одного запроса.

    Операция стоит на поле, которого мы не наблюдали. Ошибка здесь не
    возвращает пустой результат - она включает выключенный лот либо оставляет
    включённым тот, который просили снять.

    Отказ обязан случиться ДО сети: ушедший запрос уже поменял бы лот.

    Возвращает:
        None
    """
    engine = _engine(opted_in=False)
    script = patched(engine, [_form(active=True)])

    core = engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="deadbeefdeadbeef")
    with pytest.raises(UsageError) as raised:
        script.run(core)

    assert script.submits == [], "без согласия ушёл запрос сохранения"
    assert script.fetches == [], "без согласия ушло чтение формы"
    text = str(raised.value)
    assert "lots.deactivate" in text
    assert "FunPayAPI" in text, "отказ не называет, кто сообщил о непроверенном поле"


def test_the_consent_requirement_is_read_from_the_contract() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: требование согласия живёт в контракте.

    Правило, записанное в коде отдельно, пережило бы снятие объявления и
    осталось бы отказывать неизвестно по чьему требованию. Ровно так уже вышло
    с аудитом правки цены.

    Возвращает:
        None
    """
    for name in ("lots.activate", "lots.deactivate"):
        contract = OPERATIONS[name]
        assert contract.request_provenance == "third_party_report", (
            f"{name}: происхождение запроса не объявлено, а операция его спрашивает"
        )
        assert contract.provenance_source, f"{name}: источник не назван"
        assert contract.provenance_rests_on, f"{name}: не сказано, что именно непроверено"


def test_a_cleared_flag_leaves_as_an_empty_string(patched: Any) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: снятый флажок уходит пустой строкой.

    Это и есть то самое непроверенное место. «Поля нет» и «поле есть, но
    пустое» - разные запросы, и проверка стоит здесь затем, чтобы выбранный
    вариант нельзя было сменить молча.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=True), _form(active=False)])

    script.run(
        engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="deadbeefdeadbeef")
    )

    assert len(script.submits) == 1
    sent = script.submits[0].fields
    assert sent[ACTIVE_FIELD] == "", "снятый флажок ушёл не пустой строкой"
    assert script.submits[0].path == SAVE_PATH


def test_turning_on_sends_the_flag_as_on(patched: Any) -> None:
    """Требует, чтобы включение уходило наблюдённым значением.

    Отмеченный флажок наблюдён в записи запроса значением «on» - это наша
    половина знания, и она проверяется отдельно от чужой.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=False), _form(active=True)])

    script.run(
        engine.set_lot_visible(NODE, OFFER, visible=True, expected_revision="deadbeefdeadbeef")
    )

    assert script.submits[0].fields[ACTIVE_FIELD] == "on"


def test_everything_read_is_sent_back(patched: Any) -> None:
    """Требует отправлять прочитанное целиком.

    Форма несёт описание лота и сообщение покупателю. Собрать запрос из перечня
    нужных полей значило бы стереть чужой текст, а узнал бы об этом продавец
    глазами, из своей же витрины.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=True), _form(active=False)])

    script.run(
        engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="deadbeefdeadbeef")
    )

    sent = script.submits[0].fields
    assert sent["fields[desc][ru]"] == "описание, которое стереть нельзя"
    assert sent["fields[payment_msg][ru]"] == "сообщение покупателю"
    assert sent["price"] == "2.50", "цена изменилась, а её не просили трогать"


def test_a_lot_already_in_the_wanted_state_sends_nothing(patched: Any) -> None:
    """Требует не отправлять запрос, который ничего не меняет.

    Контракт объявляет операцию идемпотентной. Запрос, ничего не меняющий, всё
    равно отправил бы форму целиком - то есть подставил бы под непроверенное
    поле лот, которого не просили трогать.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=True)])

    result = script.run(
        engine.set_lot_visible(NODE, OFFER, visible=True, expected_revision="deadbeefdeadbeef")
    )

    assert script.submits == [], "лот уже показан, а запрос всё равно ушёл"
    assert result.is_active is True


def test_a_stale_revision_stops_the_operation(patched: Any) -> None:
    """Требует отказа, если лот успели изменить.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=True)])

    core = engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="0000000000000000")
    with pytest.raises(PreconditionFailedError):
        script.run(core)

    assert script.submits == []


def test_a_missing_revision_is_refused_before_the_network(patched: Any) -> None:
    """Требует отказа без отпечатка, и отказа до сети.

    Уходит вся форма. Без отпечатка параллельная правка была бы перетёрта
    молча, и перетёрта не флажком, а описанием лота.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=True)])

    core = engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="")
    with pytest.raises(UsageError):
        script.run(core)

    assert script.fetches == [], "без отпечатка успели сходить за формой"


def test_a_landing_elsewhere_is_not_a_success(patched: Any) -> None:
    """Требует не объявлять успех по чужому адресу.

    Возвращает:
        None
    """
    engine = _engine()
    script = patched(engine, [_form(active=True)], landing="/lots/999/trade")

    core = engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="deadbeefdeadbeef")
    with pytest.raises(UnexpectedResponseError):
        script.run(core)


def test_a_state_that_did_not_change_is_refused_aloud(patched: Any) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: исход сверяется перечитыванием.

    Мы отправили поле, которого сами не наблюдали. Единственный способ узнать,
    что вышло, - посмотреть. Вернуть форму, собранную из наших намерений,
    значило бы сообщить об исполнении, ничего о нём не зная, - а именно здесь
    чужой вариант запроса и мог бы оказаться неверным.

    Возвращает:
        None
    """
    engine = _engine()
    # Сохранение прошло, переход состоялся, а лот остался показанным: ровно то,
    # что случилось бы, будь вид запроса при снятом флажке неверен.
    script = patched(engine, [_form(active=True), _form(active=True)])

    core = engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="deadbeefdeadbeef")
    with pytest.raises(UnexpectedResponseError) as raised:
        script.run(core)

    assert "не наблюдался" in str(raised.value)


def test_the_capability_is_marked_only_after_a_landing(patched: Any) -> None:
    """Требует выставлять состояние по положительному свидетельству.

    Возвращает:
        None
    """
    from funora.capabilities import CapabilityState

    engine = _engine()
    script = patched(engine, [_form(active=False), _form(active=True)])

    script.run(
        engine.set_lot_visible(NODE, OFFER, visible=True, expected_revision="deadbeefdeadbeef")
    )

    assert engine._state.capabilities[Capability.LOTS_ACTIVATE] is CapabilityState.SUPPORTED
    # Возможность ВЫКЛЮЧЕНИЯ не трогалась: её никто не проверял.
    assert engine._state.capabilities[Capability.LOTS_DEACTIVATE] is not CapabilityState.SUPPORTED


def test_dropping_the_declaration_drops_the_consent(
    patched: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: снимут объявление - исчезнет и требование.

    Предыдущая проверка показывает, что без согласия операция отказывает. Она
    НЕ показывает, откуда взялось требование: отказ, записанный в коде правилом
    сам по себе, прошёл бы её точно так же.

    Разница видна ровно в одном опыте - убрать объявление из контракта. Правило,
    живущее в коде отдельно, пережило бы снятие и осталось бы отказывать
    неизвестно по чьему требованию. Ровно так уже вышло однажды с аудитом.

    Возвращает:
        None
    """
    import dataclasses

    # Контракт больше не говорит, что запрос непроверен, - и согласия не
    # спрашивают, хотя вызывающий его не давал.
    plain = dataclasses.replace(
        OPERATIONS["lots.deactivate"],
        request_provenance="",
        provenance_source="",
        provenance_rests_on="",
    )
    monkeypatch.setitem(OPERATIONS, "lots.deactivate", plain)

    engine = _engine(opted_in=False)
    script = patched(engine, [_form(active=True), _form(active=False)])

    result = script.run(
        engine.set_lot_visible(NODE, OFFER, visible=False, expected_revision="deadbeefdeadbeef")
    )

    assert len(script.submits) == 1, "объявление снято, а отказ остался - правило живёт в коде"
    assert result.is_active is False
