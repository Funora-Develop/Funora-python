"""Проверки смены валюты показа.

ЧЕМ ЭТА ОПЕРАЦИЯ ОПАСНА, И ПОЧЕМУ ОБ ЭТОМ ЦЕЛЫЙ АБЗАЦ. Она меняет валюту
ГЛОБАЛЬНО: после неё каждая страница отдаёт другие числа.

Дороже всего это для снимков рынка. Сравнение двух снимков, снятых по разные
стороны от смены, объявит сменившейся КАЖДУЮ цену - без единой ошибки, без строки
в журнале, без следа.

Отсюда главные проверки набора:

  подтверждение НЕ даётся за пользователя - в поле подтверждения всегда уходит
  отрицание, и вернувшееся окно означает, что смены НЕ БЫЛО;
  курс из окна НЕ разбирается: сторонняя реализация достаёт его регулярным
  выражением из текста на локали, а локаль привязана к аккаунту;
  валюта вне наблюдённого набора не уходит вовсе.

Наблюдено нами: переключатель в шапке с кодами ISO 4217 в data-cy.
Известно от FunPayAPI (FunPayCardinal): адрес, имена полей, ключи ответа.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._currency_switch import (
    NEVER_CONFIRMED,
    SWITCH_CURRENCY_PATH,
    parse_currency_switch,
)
from funora._engine import Engine, Fetch, Submit
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError, UsageError, ValidationError
from funora.operations import OPERATIONS

WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)

BALANCE_HTML: Final[str] = (
    "<body data-app-data='"
    + json.dumps({"csrf-token": "0123456789abcdef", "userId": "8524891"}, ensure_ascii=False)
    + "'>"
    '<button class="navbar-toggle-logged"></button>'
    '<a class="user-link-dropdown" href="/users/8524891/"></a>'
    '<a class="user-cy-switcher menu-item-currency" data-cy="usd"></a>'
    '<a class="user-cy-switcher menu-item-currency" data-cy="eur"></a>'
    "</body>"
)

#: Окно подтверждения на локали. Курс внутри - и разбирать его мы не станем.
MODAL: Final[str] = '<div class="modal"><p class="lead">1 USD = 95,40 ₽</p></div>'


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
    """Отвечает страницей баланса и телом ответа на смену."""

    def __init__(self, answer: str | None = None, *, html: str = BALANCE_HTML) -> None:
        """Готовит сценарий.

        Аргументы:
            answer (str | None): Тело ответа на смену.
            html (str): Разметка страницы.

        Возвращает:
            None
        """
        self.html = html
        self.answer = answer if answer is not None else json.dumps({"url": ""})
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
        engine._state.opted_in = frozenset({Capability.ACCOUNT_SWITCH_CURRENCY})
    return engine


def test_confirmation_is_never_given_on_the_users_behalf() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: подтверждение не даётся за пользователя.

    В поле подтверждения всегда уходит отрицание. Отправить туда согласие
    значило бы согласиться на смену за человека, который её ещё не видел.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().switch_currency("USD"))

    sent = script.submits[0]
    assert sent.path == SWITCH_CURRENCY_PATH
    assert sent.fields["confirmed"] == NEVER_CONFIRMED
    assert sent.fields["confirmed"] == "false", "в поле подтверждения ушло согласие"
    assert sent.fields["cy"] == "usd", "код ушёл не строчными"


def test_a_returned_window_means_the_switch_did_not_happen() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: вернулось окно - смены НЕ БЫЛО.

    Прочитать эту ветку успехом значило бы сказать вызывающему, что суммы
    теперь в другой валюте, - а они в прежней. Дальше он сравнил бы два снимка
    рынка и получил бы сменившейся каждую цену.

    Возвращает:
        None
    """
    script = _Scripted(json.dumps({"modal": MODAL}, ensure_ascii=False))
    result = script.run(_engine().switch_currency("USD"))

    assert result.switched is False, "окно подтверждения прочитано как смена"
    assert result.confirmation_required is True
    assert result.requested == "USD"


def test_the_exchange_rate_is_not_parsed_out_of_the_window() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: курс из окна не разбирается.

    Сторонняя реализация достаёт его регулярным выражением из абзаца окна - то
    есть из ТЕКСТА НА ЛОКАЛИ ИНТЕРФЕЙСА. Локаль привязана к аккаунту, а не к
    адресу, и смена языка ломает такой разбор молча.

    Текст отдаётся как есть, и полей курса у исхода нет вовсе.

    Возвращает:
        None
    """
    script = _Scripted(json.dumps({"modal": MODAL}, ensure_ascii=False))
    result = script.run(_engine().switch_currency("USD"))

    assert result.confirmation_text.or_none() == MODAL, "текст окна изменился при чтении"

    fields = set(type(result).__dataclass_fields__)
    assert not {one for one in fields if "rate" in one or "курс" in one}, (
        f"у исхода завелось поле курса: {sorted(fields)}. Разобрать его можно "
        "только из текста на локали, и это запрещено правилом"
    )


def test_an_empty_url_is_the_positive_sign_of_a_switch() -> None:
    """Требует читать смену положительным признаком.

    Ключ адреса присутствует и пуст - делать больше нечего.

    Возвращает:
        None
    """
    script = _Scripted(json.dumps({"url": ""}))
    result = script.run(_engine().switch_currency("EUR"))

    assert result.switched is True
    assert result.confirmation_required is False
    assert result.confirmation_text.or_none() is None


def test_a_non_empty_url_is_neither_branch_and_is_refused() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: непустой адрес не сходит за успех.

    Наблюдались две ветки. Третья означает, что мы читаем не тот ответ, и
    объявлять её успехом дорого: вызывающий поверит, что суммы теперь другие.

    Возвращает:
        None
    """
    script = _Scripted(json.dumps({"url": "https://funpay.com/куда-то"}, ensure_ascii=False))
    with pytest.raises(ProtocolChangedError) as raised:
        script.run(_engine().switch_currency("USD"))
    assert "не из них" in str(raised.value)


def test_a_body_with_neither_key_is_refused() -> None:
    """Требует отвергать ответ без обоих ключей.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_currency_switch({"что-то": 1}, requested="USD", observed_at=WHEN)


@pytest.mark.parametrize("currency", ["JPY", "GBP", "", "  ", "рубль", "US"])
def test_a_currency_outside_the_observed_set_never_leaves(currency: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: валюта вне набора не уходит.

    Набор валют площадки замкнут тремя - наблюдено дважды независимо. Четвёртой
    мы не видели, и отправлять непроверенное не станем.

    Аргументы:
        currency (str): Код вне набора.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().switch_currency(currency))
    assert script.fetches == [], "валюта непригодна, а страница всё равно прочитана"


@pytest.mark.parametrize("currency", ["rub", "RUB", "  usd  ", "Eur"])
def test_the_case_of_the_code_does_not_matter(currency: str) -> None:
    """Требует принимать код в любом регистре.

    Площадка ждёт строчные, переключатель отдаёт прописные, а вызывающий пишет
    как придётся. Приведение делает реализация - иначе на этом спотыкались бы
    все шестеро.

    Аргументы:
        currency (str): Код в разном виде.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().switch_currency(currency))
    assert script.submits[0].fields["cy"] == currency.strip().lower()


def test_without_consent_nothing_leaves_at_all() -> None:
    """Требует отказа без согласия, и отказа ДО сети.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(UsageError) as raised:
        script.run(_engine(opted_in=False).switch_currency("USD"))

    assert script.submits == [] and script.fetches == []
    assert "FunPayAPI" in str(raised.value)


def test_dropping_the_declaration_drops_the_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Требует, чтобы снятие объявления снимало и требование согласия.

    Возвращает:
        None
    """
    import dataclasses

    plain = dataclasses.replace(
        OPERATIONS["account.switch_currency"],
        request_provenance="",
        provenance_source="",
        provenance_rests_on="",
    )
    monkeypatch.setitem(OPERATIONS, "account.switch_currency", plain)

    script = _Scripted()
    script.run(_engine(opted_in=False).switch_currency("USD"))
    assert len(script.submits) == 1, "объявление снято, а отказ остался"


def test_the_capability_is_marked_after_a_parsed_answer() -> None:
    """Требует выставлять состояние по разобранному ответу.

    Возвращает:
        None
    """
    engine = _engine()
    _Scripted().run(engine.switch_currency("USD"))
    assert (
        engine._state.capabilities[Capability.ACCOUNT_SWITCH_CURRENCY] is CapabilityState.SUPPORTED
    )


def test_a_body_that_is_not_json_is_refused() -> None:
    """Требует отвергать неразобравшееся тело.

    Возвращает:
        None
    """
    script = _Scripted("<html>нет</html>")
    with pytest.raises(ProtocolChangedError):
        script.run(_engine().switch_currency("USD"))


@pytest.mark.parametrize("payload", ["строка", 42, None, []])
def test_an_unusable_payload_is_refused(payload: Any) -> None:
    """Требует отвергать непригодное тело, а не толковать его.

    Аргументы:
        payload (Any): Непригодное тело.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_currency_switch(payload, requested="USD", observed_at=WHEN)
