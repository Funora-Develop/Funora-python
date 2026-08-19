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
