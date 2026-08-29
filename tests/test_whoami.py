"""Проверки чтения аккаунта, проверки сессии и профиля возможностей."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from funora._classify import ResponseClass, Verdict
from funora._result import Severity
from funora._whoami import CapabilityProfile, SessionHealth, parse_account, parse_app_data
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


#: Снимок в формате v8: ключи объекта настроек в нём сохранены.
WITH_APP_DATA: Final[str] = "chat.logged.ru"

#: Снимок формата старше v8: атрибут настроек замаскирован целиком.
BEFORE_V8: Final[str] = "orders-trade.logged.ru"


def _with_settings(settings: dict[str, object]) -> str:
    """Собирает страницу с объектом настроек в атрибуте.

    Кавычки внутри атрибута заменяются на &quot; - ровно так их и отдаёт
    площадка, и ровно так их пишет сборщик наблюдений. Разметка, собранная
    иначе, проверяла бы разбор на том, чего в жизни не встречается.

    Аргументы:
        settings (dict[str, object]): содержимое объекта настроек.

    Возвращает:
        str: разметка страницы.
    """
    encoded = json.dumps(settings, ensure_ascii=False).replace('"', "&quot;")
    return f'<html><body data-app-data="{encoded}"></body></html>'


def test_the_csrf_token_is_read_from_the_page_settings() -> None:
    """Требует прочесть защитный токен из объекта настроек страницы.

    Все семь операций записи упираются в это одно место: без токена не собрать
    ни одного запроса. До формата скелета v8 разбор проверить было НЕ НА ЧЕМ -
    атрибут маскировался целиком, и ни одного ключа объекта в снимке не было.

    Проверяется ПУТЬ до значения, а не значение: в фикстуре на месте токена
    стоит подпись, и так и должно быть.

    Возвращает:
        None
    """
    data = parse_app_data(_page(WITH_APP_DATA))

    assert data.csrf_token is not None, [one.code for one in data.defects]
    assert data.csrf_token.reveal(), "токен прочитан пустым"
    assert not data.defects, [one.code for one in data.defects]


def test_the_token_never_shows_itself_in_any_text_form() -> None:
    """Требует, чтобы значение токена не выходило наружу само.

    Токен - часть сессии владельца. Подстановка в f-строку случайна и незаметна
    при чтении кода; вызов reveal заметен. Проверка держит именно это различие.

    Возвращает:
        None
    """
    data = parse_app_data(_page(WITH_APP_DATA))
    assert data.csrf_token is not None
    value = data.csrf_token.reveal()

    for rendered in (repr(data), str(data), repr(data.csrf_token), str(data.csrf_token)):
        assert value not in rendered, f"значение токена вышло наружу в {rendered!r}"


def test_an_older_snapshot_says_why_the_token_is_unreadable() -> None:
    """Требует отличать «токена нет» от «снимок старый».

    Атрибут, замаскированный скелетом, - это возраст снимка, а не поломка
    площадки. Названные одинаково, эти случаи повели бы читающего в разные
    стороны: первый чинится пересъёмкой, второй разбирательством с разметкой.

    Возвращает:
        None
    """
    data = parse_app_data(_page(BEFORE_V8))

    assert data.csrf_token is None
    assert "app_data_masked_by_skeleton" in {one.code for one in data.defects}
    assert not data.user_id.is_observed
    assert data.user_id.reason == "app_data_masked_by_skeleton"


def test_settings_without_the_token_key_are_reported_without_values() -> None:
    """Требует громко заметить пропажу ключа и НЕ печатать значений.

    Сообщение о повреждении читают глазами и кладут в журнал. Попади в него
    содержимое объекта настроек - там же лежит и токен.

    Возвращает:
        None
    """
    data = parse_app_data(_with_settings({"locale": "ru", "userId": "12345678"}))

    assert data.csrf_token is None
    absent = [one for one in data.defects if one.code == "csrf_token_absent"]
    assert absent, [one.code for one in data.defects]

    # Прочитанные КЛЮЧИ назвать можно, значения - нельзя.
    assert "locale" in absent[0].detail
    assert "12345678" not in absent[0].detail, "в сообщении о повреждении оказалось значение"


def test_a_settings_attribute_that_is_not_json_is_a_page_defect() -> None:
    """Требует не падать на атрибуте, который не разбирается.

    Возвращает:
        None
    """
    data = parse_app_data('<html><body data-app-data="{не json">')

    assert data.csrf_token is None
    assert "app_data_not_json" in {one.code for one in data.defects}


def test_a_page_without_the_settings_carrier_is_named_as_such() -> None:
    """Требует отличать отсутствие носителя от прочих случаев.

    Возвращает:
        None
    """
    data = parse_app_data("<html><body></body></html>")

    assert data.csrf_token is None
    assert "app_data_carrier_missing" in {one.code for one in data.defects}


def test_the_settings_carry_the_id_and_the_locale_as_second_carriers() -> None:
    """Требует прочесть идентификатор и метку языка из того же объекта.

    Оба поля есть и в других местах: идентификатор в атрибуте data-user, метка
    языка в html[lang]. Второй носитель нужен не ради значения, а ради сверки -
    разошлись, значит разметка изменилась.

    Возвращает:
        None
    """
    data = parse_app_data(_page(WITH_APP_DATA))

    assert data.user_id.is_observed, data.user_id.reason
    assert data.locale.is_observed, data.locale.reason


def test_a_numeric_id_in_the_settings_is_refused_rather_than_stringified() -> None:
    """Требует отказаться от значения не того типа, а не привести его молча.

    Число, прочитанное как строка, сравнялось бы со вторым носителем неверно, и
    разница вышла бы наружу гораздо позже места, где возникла.

    Возвращает:
        None
    """
    data = parse_app_data(_with_settings({"csrf-token": "abc", "userId": 12345678}))

    assert not data.user_id.is_observed
    assert data.user_id.reason == "key_not_a_string:user_id"
