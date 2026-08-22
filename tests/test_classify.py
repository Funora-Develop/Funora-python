"""Проверки конвейера классификации ответа.

Самый важный тест здесь - тот, что проверяет порядок шагов. Страница входа,
отданная с кодом 200, обязана быть распознана как требование войти, а не уйти в
разбор и вернуть пустой список: пустой список неотличим от «данных нет», и
именно так теряются заказы.
"""

from __future__ import annotations

import pytest

from funora._classify import DEFAULT_CONTENT_MARKERS, ResponseClass, Signature, classify

HOST = "funpay.com"


#: Страница вошедшего пользователя в том виде, в каком её отдаёт площадка.
#: Признак взят из наблюдения 18.08.2026, а не придуман.
LOGGED = '<html><body><button class="navbar-toggle navbar-toggle-logged"></button></body></html>'


def _c(html: str = LOGGED, **kw: object) -> object:
    """Вызывает классификатор со значениями по умолчанию.

    Args:
        html (str): Тело ответа.
        **kw (object): Переопределения аргументов classify.

    Returns:
        object: Вердикт классификатора.
    """
    args = {
        "status": 200,
        "final_url": f"https://{HOST}/orders",
        "html": html,
        "expected_host": HOST,
    }
    args.update(kw)
    return classify(**args)  # type: ignore[arg-type]


def test_ok_page() -> None:
    """Проверяет, что обычная страница проходит конвейер."""
    v = _c()
    assert v.cls is ResponseClass.OK
    assert v.is_ok


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ResponseClass.LOGIN_REQUIRED),
        (403, ResponseClass.BLOCKED),
        (429, ResponseClass.RATE_LIMITED),
        (503, ResponseClass.MAINTENANCE),
        (500, ResponseClass.TRANSPORT_ERROR),
        (404, ResponseClass.TRANSPORT_ERROR),
    ],
)
def test_status_codes(status: int, expected: ResponseClass) -> None:
    """Проверяет разбор по коду состояния.

    Код 429 раньше отображался в blocked, и этот набор закреплял такое
    поведение. Оно было ошибкой: blocked трактуется как отказ с закрытым замком,
    поэтому первое же попадание в ограничение частоты остановило бы опрос
    навсегда, а политика повторов для RateLimitedError осталась бы недостижимым
    кодом. «Слишком быстро» и «вам сюда нельзя» - разные ответы.

    Args:
        status (int): Код ответа.
        expected (ResponseClass): Ожидаемый класс.

    Returns:
        None
    """
    assert _c(status=status).cls is expected


def test_login_page_with_status_200() -> None:
    """Проверяет главный опасный случай: страница входа с кодом 200.

    Без этого шага она уходит в парсер как валидная и возвращает пустой результат,
    неотличимый от отсутствия данных.
    """
    html = '<html><body><form action="/account/login"><input type="password"></form></body></html>'
    v = _c(html=html)
    assert v.cls is ResponseClass.LOGIN_REQUIRED
    assert v.matched == "login_form"
    assert not v.is_ok


def test_challenge_detected_before_fingerprint() -> None:
    """Проверяет, что страница проверки распознаётся отдельно от поломки разметки."""
    html = '<html><body><div class="g-recaptcha" data-sitekey="x"></div></body></html>'
    v = _c(html=html)
    assert v.cls is ResponseClass.CHALLENGE
    assert v.provisional, "непроверенная сигнатура обязана помечаться"


def test_challenge_by_text() -> None:
    """Проверяет текстовый детектор проверки."""
    html = "<html><body><p>Подтвердите, что вы не робот</p></body></html>"
    assert _c(html=html).cls is ResponseClass.CHALLENGE


def test_blocked_by_text() -> None:
    """Проверяет текстовый детектор блокировки."""
    html = "<html><body><p>Ваш аккаунт заблокирован</p></body></html>"
    assert _c(html=html).cls is ResponseClass.BLOCKED


def test_maintenance_by_text() -> None:
    """Проверяет текстовый детектор технических работ."""
    html = "<html><body><p>Ведутся технические работы</p></body></html>"
    assert _c(html=html).cls is ResponseClass.MAINTENANCE


def test_host_mismatch_wins_over_everything() -> None:
    """Проверяет, что чужой хост отвергается до разбора содержимого.

    Ответ с чужого адреса не заслуживает разбора, как бы он ни выглядел: это же
    ловит перепутанные cookie при работе с несколькими аккаунтами.
    """
    v = _c(final_url="https://evil.example/orders", html="<html><body>ok</body></html>")
    assert v.cls is ResponseClass.WRONG_IDENTITY
    assert v.detail["actual"] == "evil.example"


def test_subdomain_is_accepted() -> None:
    """Проверяет, что поддомен ожидаемого хоста считается своим."""
    assert _c(final_url=f"https://support.{HOST}/x").cls is ResponseClass.OK


def test_empty_body_is_unknown_not_ok() -> None:
    """Проверяет, что пустое тело не считается успешным ответом."""
    v = _c(html="   ")
    assert v.cls is ResponseClass.UNKNOWN
    assert v.reason == "empty_body"


def test_identity_marker_present() -> None:
    """Проверяет подтверждение личности по маркеру."""
    html = '<html><body><a class="user-link">x</a></body></html>'
    v = _c(html=html, identity_css="a.user-link")
    assert v.cls is ResponseClass.OK
    assert v.reason == "identity_confirmed"


def test_identity_marker_absent_is_unknown() -> None:
    """Проверяет, что отсутствие маркера даёт unknown, а не «не вошли».

    Отсутствие маркера может означать и изменившуюся разметку. Это разные
    диагнозы, и склеивать их нельзя: во втором случае повторный вход не поможет.
    """
    v = _c(html="<html><body><div>x</div></body></html>", identity_css="a.user-link")
    assert v.cls is ResponseClass.UNKNOWN
    assert v.reason == "identity_marker_absent"


def test_interstitial_wins_over_missing_identity() -> None:
    """Проверяет приоритет детекторов над проверкой маркера.

    На странице входа маркера вошедшего пользователя нет по определению. Если бы
    проверка маркера шла раньше, вердиктом был бы unknown вместо login_required,
    и пользователь не узнал бы, что надо просто войти заново.
    """
    html = '<html><body><input type="password"></body></html>'
    v = _c(html=html, identity_css="a.user-link")
    assert v.cls is ResponseClass.LOGIN_REQUIRED


def test_custom_signature_registry() -> None:
    """Проверяет, что реестр сигнатур подменяется без правки кода."""
    sig = (
        Signature(name="custom", verdict=ResponseClass.BLOCKED, css=(".stop",), provisional=False),
    )
    v = _c(html='<html><body><div class="stop"></div></body></html>', signatures=sig)
    assert v.cls is ResponseClass.BLOCKED
    assert v.matched == "custom"
    assert not v.provisional


def test_verdict_carries_no_page_content() -> None:
    """Проверяет, что в вердикт не попадает содержимое страницы.

    Вердикт уходит в диагностику и в issue, поэтому текста страницы в нём быть
    не должно ни при каких обстоятельствах.
    """
    html = "<html><body><p>Иван Петров заказ 98765 заблокирован</p></body></html>"
    v = _c(html=html)
    blob = repr(v)
    for secret in ("Иван", "Петров", "98765"):
        assert secret not in blob


def test_broken_html_does_not_crash() -> None:
    """Проверяет устойчивость к битой разметке."""
    v = _c(html="<html><body><div><<>>unclosed")
    assert v.cls in (ResponseClass.OK, ResponseClass.UNKNOWN)


def test_guest_navbar_is_confirmed_signature() -> None:
    """Проверяет распознавание выхода из сессии по подтверждённому признаку.

    Наблюдение 18.08.2026: у вошедшего в шапке стоит navbar-toggle-logged, у
    гостя - navbar-toggle-guest. Признак структурный и не зависит от языка, что
    здесь принципиально: локаль привязана к аккаунту, а не к адресу, и через URL
    её не переключить.
    """
    html = '<html><body><button class="navbar-toggle navbar-toggle-guest"></button></body></html>'
    v = _c(html=html)
    assert v.cls is ResponseClass.LOGIN_REQUIRED
    assert v.matched == "guest_navbar"
    assert not v.provisional, "признак подтверждён наблюдением и не может быть provisional"


def test_login_page_container_is_confirmed() -> None:
    """Проверяет распознавание страницы входа по её контейнеру."""
    html = '<html><body><div class="content-account content-account-login"></div></body></html>'
    v = _c(html=html)
    assert v.cls is ResponseClass.LOGIN_REQUIRED
    assert not v.provisional


def test_default_identity_marker_is_required() -> None:
    """Проверяет, что без маркера вошедшего страница не считается пригодной.

    До наблюдения маркер был неизвестен и проверка пропускалась. Теперь она
    включена по умолчанию: страница без маркера может оказаться чем угодно, и
    считать её пригодной для разбора - это тот самый тихий отказ.
    """
    v = _c(html="<html><body><div>нечто</div></body></html>")
    assert v.cls is ResponseClass.UNKNOWN
    assert v.reason == "identity_marker_absent"


#: Сообщения, которые собеседник может написать в переписку не злонамеренно.
#: Каждое из них останавливало бота до исправления.
HOSTILE_TEXTS = [
    "там captcha вылезла, что делать",
    "мой аккаунт заблокирован, верните деньги",
    "выдали бан ни за что",
    "подтвердите, что вы продавец этого товара",
    "у вас доступ запрещён к разделу?",
    "ведутся технические работы у вас?",
]


@pytest.mark.parametrize("text", HOSTILE_TEXTS)
def test_counterparty_text_cannot_stop_the_client(text: str) -> None:
    """Проверяет, что сообщение собеседника не останавливает клиента.

    Это исправление уязвимости, а не украшение. Текстовые сигнатуры искались по
    всей странице, включая переписку, которую пишет покупатель. Шести обычных
    сообщений из шести хватало, чтобы конвейер признал страницу блокировкой или
    проверкой: собеседник останавливал чужого бота одним словом, не имея доступа
    ни к аккаунту, ни к площадке.

    Args:
        text (str): Сообщение, написанное собеседником.

    Returns:
        None
    """
    html = (
        f'<html><body>{LOGGED}<div class="chat-message-list">'
        f'<div class="chat-msg-text">{text}</div></div></body></html>'
    )
    v = classify(status=200, final_url=f"https://{HOST}/chat/", html=html, expected_host=HOST)
    assert v.cls is ResponseClass.OK, f"сообщение собеседника дало вердикт {v.cls}"


@pytest.mark.parametrize("text", HOSTILE_TEXTS)
def test_same_text_outside_user_containers_still_detected(text: str) -> None:
    """Проверяет, что вне контейнеров чужого ввода сигнатуры работают.

    Исключение обязано сузить область поиска, а не отключить его. Если бы вместе
    с уязвимостью пропало и распознавание, настоящая страница блокировки прошла
    бы как обычная, и клиент продолжил бы стучаться, подтверждая подозрение.

    Args:
        text (str): Тот же текст, но в теле страницы, а не в переписке.

    Returns:
        None
    """
    v = classify(
        status=200,
        final_url=f"https://{HOST}/",
        html=f"<html><body><p>{text}</p></body></html>",
        expected_host=HOST,
    )
    assert v.cls is not ResponseClass.OK


def test_order_description_is_not_searched() -> None:
    """Проверяет, что описание заказа не участвует в поиске сигнатур.

    Описание приходит от контрагента так же, как сообщение в переписке.

    Returns:
        None
    """
    html = (
        f'<html><body>{LOGGED}<div class="orders-table">'
        '<div class="order-desc">разблокировка и снятие бан</div>'
        "</div></body></html>"
    )
    v = classify(
        status=200, final_url=f"https://{HOST}/orders/trade", html=html, expected_host=HOST
    )
    assert v.cls is ResponseClass.OK


def test_content_markers_are_not_empty_by_default() -> None:
    """Проверяет, что защита включена по умолчанию.

    Пустой перечень вернул бы уязвимость всем, кто не задал его сам, - то есть
    всем.

    Returns:
        None
    """
    assert DEFAULT_CONTENT_MARKERS
    assert ".orders-table" in DEFAULT_CONTENT_MARKERS
    assert ".chat-message-list" in DEFAULT_CONTENT_MARKERS


def test_lot_title_cannot_stop_the_client() -> None:
    """Проверяет случай, который вернул уязвимость после первой починки.

    Первая версия защиты перечисляла контейнеры, где пишут люди. Заголовок лота
    в боковой панели переписки в перечень не попал, и одного слова в нём хватало,
    чтобы весь конвейер признал страницу блокировкой и остановил бота.

    Перечислять пользовательские участки значит перечислять их все, а любой
    пропущенный открывает дыру заново. Поэтому перечень перевёрнут: текстовые
    сигнатуры применяются только к странице, которую мы не узнали.

    Returns:
        None
    """
    html = (
        f'<html><body>{LOGGED}<div class="chat-message-list"></div>'
        '<div class="param-item chat-panel">'
        '<a href="https://funpay.com/lots/offer?x=1">мой аккаунт заблокирован</a>'
        "</div></body></html>"
    )
    v = _c(html=html, final_url=f"https://{HOST}/chat/")
    assert v.cls is ResponseClass.OK


def test_any_text_on_a_known_page_is_ignored() -> None:
    """Проверяет правило целиком, а не отдельный его случай.

    Проверка намеренно не перечисляет места: она утверждает, что на узнанной
    странице текстовые сигнатуры не применяются вообще. Так проверка не устареет
    при появлении нового пользовательского участка - а именно это и случилось с
    предыдущей защитой.

    Returns:
        None
    """
    for phrase in ("captcha", "аккаунт заблокирован", "технические работы", "доступ запрещён"):
        html = (
            f'<html><body>{LOGGED}<div class="orders-table">'
            f"<span>{phrase}</span></div></body></html>"
        )
        assert _c(html=html).cls is ResponseClass.OK, f"текст «{phrase}» повлиял на вердикт"


def test_unknown_page_still_uses_text_signatures() -> None:
    """Проверяет, что защита сузила область, а не отключила её.

    Настоящая страница проверки заменяет содержимое целиком: ни таблицы заказов,
    ни виджета переписки на ней нет. Не распознать её опаснее, чем сработать по
    тексту.

    Returns:
        None
    """
    for phrase, expected in (
        ("Подтвердите, что вы не робот", ResponseClass.CHALLENGE),
        ("Ваш аккаунт заблокирован", ResponseClass.BLOCKED),
        ("Ведутся технические работы", ResponseClass.MAINTENANCE),
    ):
        html = f"<html><body><h1>{phrase}</h1></body></html>"
        assert _c(html=html).cls is expected


def test_every_builtin_selector_is_usable() -> None:
    """Проверяет, что каждый встроенный селектор вправду применяется.

    Непригодный селектор пропускается на живой странице - иначе опечатка
    превратилась бы в отказ читать площадку. Цена этой мягкости в том, что
    сломанный селектор выключает свою подпись насовсем: страница входа
    перестаёт узнаваться, вердикт уходит в «разметка изменилась», и виноватой
    выглядит площадка.

    Значит, поймать сломанный селектор надо здесь, до выпуска, а не там.

    Returns:
        None
    """
    from selectolax.parser import HTMLParser

    from funora._classify import DEFAULT_CONTENT_MARKERS, DEFAULT_SIGNATURES

    tree = HTMLParser("<html><body><div></div></body></html>")

    selectors = [
        *DEFAULT_CONTENT_MARKERS,
        *(selector for sig in DEFAULT_SIGNATURES for selector in sig.css),
    ]
    assert len(selectors) > 5, "селекторов не набралось - проверять нечего"

    for selector in selectors:
        try:
            tree.css_first(selector)
        except Exception as exc:  # noqa: BLE001 - имя виноватого важнее типа
            pytest.fail(f"селектор {selector!r} непригоден: {type(exc).__name__}")


def test_unusable_selector_is_logged_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Проверяет, что непригодный селектор оставляет след в журнале.

    Молча пропущенный селектор выключает свою подпись насовсем, и узнать об этом
    неоткуда: вердикт выглядит правдоподобно, просто он не тот.

    Args:
        caplog (pytest.LogCaptureFixture): Перехват журнала.

    Returns:
        None
    """
    broken = Signature(
        name="сломанная",
        verdict=ResponseClass.LOGIN_REQUIRED,
        css=("::=не селектор=::",),
        provisional=False,
    )

    with caplog.at_level("WARNING", logger="funora.classify"):
        verdict = classify(
            status=200,
            html="<html><body><div class='content-account'></div></body></html>",
            final_url=f"https://{HOST}/orders/trade",
            expected_host=HOST,
            signatures=(broken,),
            identity_css=None,
        )

    assert verdict.cls is not ResponseClass.LOGIN_REQUIRED
    assert any("сломанная" in record.getMessage() for record in caplog.records), (
        "непригодный селектор исчез молча - подпись выключена, и узнать неоткуда"
    )


def test_unusable_selector_does_not_break_classification() -> None:
    """Проверяет, что непригодный селектор не роняет разбор целиком.

    Классификация идёт по живой странице, и уронить её из-за одного выражения
    значило бы превратить опечатку в отказ читать площадку вовсе. Остальные
    селекторы подписи обязаны отработать.

    Returns:
        None
    """
    mixed = Signature(
        name="наполовину_сломанная",
        verdict=ResponseClass.LOGIN_REQUIRED,
        css=("::=не селектор=::", ".menu-item-login"),
        provisional=False,
    )

    verdict = classify(
        status=200,
        html="<html><body><a class='menu-item-login'>вход</a></body></html>",
        final_url=f"https://{HOST}/orders/trade",
        expected_host=HOST,
        signatures=(mixed,),
        identity_css=None,
    )

    assert verdict.cls is ResponseClass.LOGIN_REQUIRED
    assert verdict.detail == {"selector": ".menu-item-login"}


def test_backslash_host_is_not_ours() -> None:
    """Проверяет самый коварный вид подделки хоста.

    Адрес `https://evil.example\\.funpay.com/` разборщик Python видит как один
    хост, оканчивающийся на `.funpay.com`, - и сравнение с суффиксом объявляет
    его нашим. Браузер по такому адресу идёт на `evil.example`.

    Классификатор держал свою копию правила о хосте и на этом попадался. Копия
    была четвёртой: ровно из-за таких расхождений `_host.py` и появился.

    Returns:
        None
    """
    verdict = classify(
        status=200,
        html="<html><body><div class='navbar-toggle-logged'></div></body></html>",
        final_url="https://evil.example\\.funpay.com/orders/trade",
        expected_host=HOST,
    )

    assert verdict.cls is ResponseClass.WRONG_IDENTITY
    assert verdict.reason in {"host_mismatch", "host_unreadable"}


def test_prefix_host_is_not_ours() -> None:
    """Проверяет подделку приставкой.

    `https://funpay.com.evil.example/` содержит имя площадки и ведёт совсем не
    туда. Сравнение подстрокой на этом попадается; сравнение суффиксом - нет,
    но проверка стоит здесь затем, чтобы попытка «упростить» правило обратно к
    подстроке падала.

    Returns:
        None
    """
    verdict = classify(
        status=200,
        html="<html><body><div class='navbar-toggle-logged'></div></body></html>",
        final_url="https://funpay.com.evil.example/orders/trade",
        expected_host=HOST,
    )

    assert verdict.cls is ResponseClass.WRONG_IDENTITY
    assert verdict.reason == "host_mismatch"


def test_unreadable_host_is_refused_not_trusted() -> None:
    """Проверяет адрес, о происхождении которого сказать нечего.

    Прежде пустой хост проверку личности проходил: условие начиналось с «если
    хост есть». Ответу, о происхождении которого сказать нечего, доверять нельзя
    тем более, чем ответу с чужого хоста.

    Returns:
        None
    """
    verdict = classify(
        status=200,
        html="<html><body><div class='navbar-toggle-logged'></div></body></html>",
        final_url="не адрес вовсе",
        expected_host=HOST,
    )

    assert verdict.cls is ResponseClass.WRONG_IDENTITY
    assert verdict.reason == "host_unreadable"


def test_subdomain_of_ours_is_still_ours() -> None:
    """Проверяет, что строгость не задела свои поддомены.

    Returns:
        None
    """
    verdict = classify(
        status=200,
        html="<html><body><div class='navbar-toggle-logged'></div></body></html>",
        final_url=f"https://support.{HOST}/orders/trade",
        expected_host=HOST,
    )

    assert verdict.cls is not ResponseClass.WRONG_IDENTITY
