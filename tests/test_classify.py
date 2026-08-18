"""Проверки конвейера классификации ответа.

Самый важный тест здесь - тот, что проверяет порядок шагов. Страница входа,
отданная с кодом 200, обязана быть распознана как требование войти, а не уйти в
разбор и вернуть пустой список: пустой список неотличим от «данных нет», и
именно так теряются заказы.
"""

from __future__ import annotations

import pytest

from funora._classify import ResponseClass, Signature, classify

HOST = "funpay.com"


def _c(html: str = "<html><body><div>ok</div></body></html>", **kw: object) -> object:
    """Вызывает классификатор со значениями по умолчанию.

    Args:
        html (str): Тело ответа.
        **kw (object): Переопределения аргументов classify.

    Returns:
        object: Вердикт классификатора.
    """
    args = {"status": 200, "final_url": f"https://{HOST}/orders", "html": html,
            "expected_host": HOST}
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
        (429, ResponseClass.BLOCKED),
        (503, ResponseClass.MAINTENANCE),
        (500, ResponseClass.TRANSPORT_ERROR),
        (404, ResponseClass.TRANSPORT_ERROR),
    ],
)
def test_status_codes(status: int, expected: ResponseClass) -> None:
    """Проверяет разбор по коду состояния.

    Args:
        status (int): Код ответа.
        expected (ResponseClass): Ожидаемый класс.
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
        Signature(
            name="custom", verdict=ResponseClass.BLOCKED, css=(".stop",), provisional=False
        ),
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
