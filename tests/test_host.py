"""Проверки правила «тот ли это хост».

Правило было написано в проекте трижды и по-разному: верно в классификаторе,
подстрокой в разборе переписки, отсутствовало в транспорте. Последнее стоило
сессионного ключа.

Набор закрепляет единственную оставшуюся копию. Ошибка здесь стоит аккаунта, и
сравнение подстрокой коварно именно тем, что выглядит работающим.
"""

from __future__ import annotations

import pytest

from funora._host import host_of, is_safe_hop, same_host


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://funpay.com/orders", True),
        ("https://FunPay.COM/orders", True),
        ("https://support.funpay.com/tickets", True),
        ("https://funpay.com./orders", True),
        ("https://funpay.com:443/orders", True),
    ],
)
def test_own_addresses_pass(url: str, expected: bool) -> None:
    """Проверяет, что свои адреса и поддомены считаются своими.

    Отвергать поддомены значило бы отвергать страницы самой площадки.

    Args:
        url (str): Проверяемый адрес.
        expected (bool): Ожидаемый ответ.

    Returns:
        None
    """
    assert same_host(url, "funpay.com") is expected


@pytest.mark.parametrize(
    "url",
    [
        "https://funpay.com.evil.example/steal",
        "https://xfunpay.com/steal",
        "https://evil.example/funpay.com",
        "https://evil.example/?next=funpay.com",
        "https://funpay.com@evil.example/steal",
        "https://notfunpay.com/steal",
        "",
        "не адрес вовсе",
    ],
)
def test_foreign_addresses_are_refused(url: str) -> None:
    """Проверяет отказ на чужих адресах, похожих на свои.

    Первый случай самый важный: адрес funpay.com.evil.example содержит имя
    площадки как подстроку и проходил прежнюю проверку.

    Последний с собачкой - обычная подмена: всё до неё браузер считает
    учётными данными, а хостом служит то, что после.

    Args:
        url (str): Проверяемый адрес.

    Returns:
        None
    """
    assert not same_host(url, "funpay.com")


def test_empty_expectation_refuses_everything() -> None:
    """Проверяет, что пустое ожидание не пропускает всё подряд.

    Пустая строка в сравнении подстрокой содержится в любом адресе, и такая
    ошибка отключила бы защиту целиком.

    Returns:
        None
    """
    assert not same_host("https://funpay.com/x", "")
    assert not same_host("https://evil.example/x", "")


def test_port_in_expectation_is_ignored() -> None:
    """Проверяет, что порт в ожидаемом хосте не мешает.

    В клиенте ожидаемый хост брался вместе с портом и сравнивался с хостом без
    порта. На площадке это не проявлялось, но делало невозможной проверку
    транспорта локальным сервером - и ровно поэтому таких проверок не было.

    Returns:
        None
    """
    assert same_host("https://funpay.com/x", "funpay.com:443")
    assert same_host("http://127.0.0.1:8080/x", "127.0.0.1:8080")


def test_scheme_downgrade_is_unsafe() -> None:
    """Проверяет запрет на понижение схемы.

    Переход с https на http отдаёт секрет открытым текстом любому, кто видит
    трафик, и заметить это по поведению клиента невозможно.

    Returns:
        None
    """
    assert not is_safe_hop("https://funpay.com/a", "http://funpay.com/b", "funpay.com")
    assert is_safe_hop("https://funpay.com/a", "https://funpay.com/b", "funpay.com")
    assert is_safe_hop("http://127.0.0.1/a", "http://127.0.0.1/b", "127.0.0.1")


def test_foreign_hop_is_unsafe_even_with_https() -> None:
    """Проверяет, что защищённая схема не оправдывает чужой хост.

    Returns:
        None
    """
    assert not is_safe_hop("https://funpay.com/a", "https://evil.example/b", "funpay.com")


def test_host_of_extracts_without_port() -> None:
    """Проверяет извлечение хоста.

    Returns:
        None
    """
    assert host_of("https://FunPay.com:8443/x") == "funpay.com"
    assert host_of("не адрес") == ""


def test_backslash_cannot_pretend_to_be_our_host() -> None:
    """Проверяет, что обратная косая черта не делает чужой адрес своим.

    Питон разбирает адрес по RFC 3986, браузер - по правилам WHATWG, и здесь они
    расходятся. Для питона у ``https://evil.example\\.funpay.com/`` хост
    ``evil.example\\.funpay.com``, и правило «оканчивается на .funpay.com» его
    принимало. Браузер по тому же адресу идёт на ``evil.example``.

    Расхождение стоило дорого дважды: ссылка в переписке числилась своей, а путь
    такой ссылки сохранялся в снимке дословно - вместе с написанным там именем.

    Returns:
        None
    """
    hostile = "https://evil.example\\.funpay.com/ivanpetrov"

    assert host_of(hostile) == "", "имя с обратной косой чертой не является именем хоста"
    assert not same_host(hostile, "funpay.com")
    assert not is_safe_hop("https://funpay.com/", hostile, "funpay.com")


def test_hostname_shape_is_checked() -> None:
    """Проверяет отбор имён, недопустимых в DNS.

    Направление отказа безопасное во всех трёх местах, где правило применяется:
    секрет не уходит, ссылка считается чужой, путь маскируется целиком.

    Returns:
        None
    """
    for bad in (
        "https://evil.example\\.funpay.com/",
        "https://funpay.com_evil.example/",
        "https://-funpay.com/",
        "https://funpay.com-/",
        "https://пример.рф/",
    ):
        assert host_of(bad) == "", f"{bad} принято за имя хоста"

    for good in (
        "https://funpay.com/",
        "https://support.funpay.com/",
        "https://xn--e1afmkfd.xn--p1ai/",
    ):
        assert host_of(good), f"{good} отвергнуто, хотя допустимо"
