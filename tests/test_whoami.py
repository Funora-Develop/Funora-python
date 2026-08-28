"""Проверки чтения аккаунта, проверки сессии и профиля возможностей."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from funora._classify import ResponseClass, Verdict
from funora._result import Severity
from funora._whoami import CapabilityProfile, SessionHealth, parse_account
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Страница с виджетом переписки: там есть собственный идентификатор.
WITH_WIDGET: Final[str] = "chat.logged.ru"

#: Страница без виджета: имя есть, идентификатора нет.
WITHOUT_WIDGET: Final[str] = "account-balance.logged.ru"

#: Момент наблюдения. Постоянен нарочно: разбор обязан быть повторяемым.
WHEN: Final[datetime] = datetime(2026, 8, 24, tzinfo=UTC)


def _page(name: str) -> str:
    """Читает снимок страницы.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_the_own_account_is_read_where_the_chat_widget_is() -> None:
    """Требует прочесть себя со страницы, где виджет переписки есть.

    Возвращает:
        None
    """
    account = parse_account(_page(WITH_WIDGET), WHEN)

    assert account.user_id.is_observed, f"идентификатор не прочитан: {account.user_id.reason!r}"
    assert account.username.is_observed
    assert account.locale.is_observed
    assert not account.defects, [one.code for one in account.defects]


def test_a_page_without_the_widget_gives_no_own_id_and_says_so() -> None:
    """Требует громко заметить страницу, на которой себя не прочесть.

    Атрибут собственного идентификатора есть только там, где есть виджет
    переписки: на списке продаж и на странице баланса его нет вовсе.

    Это не поломка - это выбор страницы. Но и молчать нельзя: вызывающий принял
    бы пробел за отсутствие идентификатора у аккаунта.

    Возвращает:
        None
    """
    account = parse_account(_page(WITHOUT_WIDGET), WHEN)

    assert not account.user_id.is_observed
    assert "own_id_carrier_missing" in {one.code for one in account.defects}
    assert any(one.severity is Severity.PAGE for one in account.defects)

    # Имя и метка языка при этом читаются: страница исправна, просто другая.
    assert account.username.is_observed
    assert account.locale.value == "ru"


def test_two_username_carriers_that_disagree_give_no_name() -> None:
    """Требует отказаться от имени, когда его носители разошлись.

    Узлов имени два - настольное меню и мобильное. Взять первый попавшийся
    значило бы повторить ошибку, на которой уже спотыкались разбор списка продаж
    и разбор отзывов.

    Возвращает:
        None
    """
    original = _page(WITH_WIDGET)
    at = original.index('class="user-link-name"')
    end = original.index("</div>", at)
    broken = original[:at] + original[at:end].replace("T8:a", "ИНОЕ", 1) + original[end:]
    assert broken != original, "подмена имени не сработала"

    account = parse_account(broken, WHEN)
    assert not account.username.is_observed
    assert account.username.reason == "username_carriers_disagree"
    assert "username_carriers_disagree" in {one.code for one in account.defects}


def test_a_page_with_no_identity_at_all_is_a_protocol_change() -> None:
    """Требует громкого отказа на странице, отданной не нам.

    Пустой ответ вернуть нельзя: он неотличим от гостевой страницы, а разница
    между «мы не разобрали» и «нас не узнали» решает, стоит ли перезаходить.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError, match="отданной не нам"):
        parse_account(_page("orders-trade.guest.ru"), WHEN)


def test_the_session_is_usable_only_on_the_ok_class() -> None:
    """Требует считать сессию годной РОВНО при классе ok.

    Правило «всё, кроме явного отказа, годится» однажды приняло бы страницу
    проверки за рабочую.

    Возвращает:
        None
    """
    for cls in ResponseClass:
        verdict = Verdict(cls=cls, reason="проба", matched=None, provisional=False, detail={})
        health = SessionHealth.of(verdict, WHEN, from_cache=False)
        assert health.is_usable is (cls is ResponseClass.OK), (
            f"класс {cls} признан {'годным' if health.is_usable else 'негодным'}"
        )
        assert health.response_class is cls
        assert health.from_cache is False


def test_a_provisional_verdict_is_reported_as_provisional() -> None:
    """Требует передавать признак непроверенной сигнатуры вызывающему.

    Страницы блокировки, проверки и обслуживания никто не видел, и часть
    сигнатур составлена умозрительно. Решение по такой сигнатуре отдаётся с
    пометкой, а не выдаётся за наблюдение.

    Возвращает:
        None
    """
    guessed = Verdict(
        cls=ResponseClass.BLOCKED, reason="проба", matched="догадка", provisional=True, detail={}
    )
    assert SessionHealth.of(guessed, WHEN, from_cache=False).provisional is True

    seen = Verdict(cls=ResponseClass.OK, reason="проба", matched=None, provisional=False, detail={})
    assert SessionHealth.of(seen, WHEN, from_cache=False).provisional is False


def test_the_profile_names_every_capability_of_the_contract() -> None:
    """Требует, чтобы профиль называл КАЖДУЮ возможность.

    Профиль, умалчивающий о возможности, читался бы как «её нет», а это другой
    ответ: «нет» и «не знаем» ведут к разным решениям.

    Возвращает:
        None
    """
    profile = CapabilityProfile(
        observed_at=WHEN, _states={one: CapabilityState.UNKNOWN for one in Capability}
    )
    states = profile.states()

    assert set(states) == set(Capability), (
        f"в профиле нет: {sorted(str(one) for one in set(Capability) - set(states))}"
    )
    assert len(states) == len(list(Capability))

    # Снимок отдаётся копией: правка ответа не должна менять состояние клиента.
    states[next(iter(Capability))] = CapabilityState.SUPPORTED
    assert profile.states() != states, "профиль отдал ссылку на своё состояние"


def test_the_profile_refuses_to_invent_a_missing_capability() -> None:
    """Требует громко упасть на возможности, которой в профиле нет.

    Пробел здесь - дефект сборки, а не отсутствие возможности, и молча вернуть
    unknown значило бы выдать одно за другое.

    Возвращает:
        None
    """
    partial = CapabilityProfile(observed_at=WHEN, _states={})
    with pytest.raises(KeyError):
        partial.state_of(Capability.ORDERS_LIST)


def test_the_throttle_interval_is_declared_by_the_contract() -> None:
    """Требует, чтобы у срока дросселя было ЧИСЛО, а не только имя.

    До 0.14.0 имя было, числа не было: операция обещала кэш, механизма не
    существовало, и договориться о частоте шесть реализаций не смогли бы при
    всём желании.

    Возвращает:
        None
    """
    from funora.budget import MIN_HEALTH_INTERVAL_MS

    assert isinstance(MIN_HEALTH_INTERVAL_MS, int)
    assert MIN_HEALTH_INTERVAL_MS > 0, "срок дросселя обязан быть положительным"


def _ok_page() -> str:
    """Возвращает разметку страницы, которую классификатор признаёт годной.

    Возвращает:
        str: содержимое снимка с виджетом переписки.
    """
    return _page(WITH_WIDGET)


def test_a_stale_verdict_is_never_reported_as_a_fresh_check() -> None:
    """Требует обнулять вердикт перед каждой проверкой сессии.

    Проверка отчитывается по вердикту классификатора. Если до неё был удачный
    поход на площадку, а сама она до классификации не дошла - ответа не было
    вовсе, - то несвежий вердикт выглядел бы свежим: проверка сказала бы «сессия
    годна», не получив ни одного ответа.

    Это худший из возможных исходов для операции, которую зовут именно затем,
    чтобы узнать о неприятности заранее.

    Возвращает:
        None
    """
    from funora._budget import Budget
    from funora._classify import ResponseClass as _Class
    from funora._engine import Engine, Fetch
    from funora._transport import TransportSettings
    from funora.errors import TransportError

    engine = Engine(TransportSettings(), Budget())

    # Так, будто удачный поход уже был: вердикт на месте, кэш просрочен.
    engine._state.last_verdict = Verdict(
        cls=_Class.OK, reason="прежний поход", matched=None, provisional=False, detail={}
    )
    engine._state.health_cached = None
    engine._state.health_checked_at = 0.0

    # Движок повторяет запросы и просит пауз, поэтому цикл, а не один вызов:
    # ответа не будет ни разу, и проверка обязана дойти до конца сама.
    core = engine.read_health()
    health = None
    pending: BaseException | None = None
    for _ in range(64):
        try:
            request = core.throw(pending) if pending else core.send(None)
        except StopIteration as stopped:
            health = stopped.value
            break
        except TransportError:
            pytest.skip("движок пропустил сетевой отказ наружу - проверка о другом")
        # На просьбу об обращении отвечаем отказом сети; на просьбу о паузе -
        # ничем, время в проверке не идёт.
        pending = TransportError("сети нет") if isinstance(request, Fetch) else None

    assert health is not None, "проверка ничего не вернула"
    assert health.response_class is not _Class.OK, (
        f"проверка отчиталась классом {health.response_class}, не получив ответа. "
        "Похоже, взят вердикт прошлого похода"
    )
    assert health.is_usable is False
    assert health.reason == "no_response"
