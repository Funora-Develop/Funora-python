"""Проверки решения о переходе.

Решение вынесено из транспорта ради асинхронного близнеца: без этого правило
безопасности пришлось бы написать второй раз, а именно оно однажды уже стоило
сессионного ключа. Здесь оно проверяется отдельно от сети, потому что теперь
может быть проверено отдельно от сети.
"""

from __future__ import annotations

import pytest

from funora._hops import Follow, Reject, Stop, next_hop

#: Ожидаемый хост площадки во всех проверках набора.
HOST = "funpay.com"


def _hop(**over: object):  # type: ignore[no-untyped-def]
    """Вызывает решение с разумными значениями по умолчанию.

    Args:
        **over (object): Переопределяемые аргументы.

    Returns:
        Hop: Принятое решение.
    """
    kwargs: dict[str, object] = {
        "current": "https://funpay.com/orders/trade",
        "is_redirect": True,
        "location": "/login",
        "redirects": 0,
        "max_redirects": 5,
        "expected": HOST,
    }
    kwargs.update(over)
    return next_hop(**kwargs)  # type: ignore[arg-type]


def test_plain_answer_stops() -> None:
    """Проверяет, что обычный ответ переходов не порождает.

    Returns:
        None
    """
    assert isinstance(_hop(is_redirect=False), Stop)


def test_own_host_is_followed() -> None:
    """Проверяет, что переход внутри площадки разрешается.

    Returns:
        None
    """
    hop = _hop()
    assert isinstance(hop, Follow)
    assert hop.url == "https://funpay.com/login"


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        # Подстрока ожидаемого хоста, но ведёт совсем не туда. Проверка по
        # вхождению приняла бы этот адрес за свой.
        "https://funpay.com.evil.example/steal",
        "http://funpay.com/login",
    ],
)
def test_dangerous_hops_are_rejected(location: str) -> None:
    """Проверяет, что опасный переход отвергается, а не выполняется.

    Args:
        location (str): Значение заголовка Location.

    Returns:
        None
    """
    hop = _hop(location=location)
    assert isinstance(hop, Reject), f"переход на {location} обязан быть отвергнут"


def test_rejected_target_is_reported() -> None:
    """Проверяет, что отвергнутый адрес сообщается вызывающему.

    Это не мелочь. С отвергнутым адресом классификатор видит чужой хост и ставит
    диагноз «нас пытались увести»; с исходным он увидел бы пустое тело и сказал
    бы «разметка изменилась» - то есть отправил бы разбираться не туда.

    Returns:
        None
    """
    hop = _hop(location="https://evil.example/steal")
    assert isinstance(hop, Reject)
    assert hop.url == "https://evil.example/steal"


def test_redirect_without_location_stops() -> None:
    """Проверяет, что перенаправление без адреса никуда не ведёт.

    Придумывать адрес самим - последнее, чем стоит заниматься с секретом в руках.

    Returns:
        None
    """
    assert isinstance(_hop(location=""), Stop)


def test_limit_stops_the_chain() -> None:
    """Проверяет, что исчерпанный предел переходов останавливает цепочку.

    Returns:
        None
    """
    assert isinstance(_hop(redirects=5, max_redirects=5), Stop)
    assert isinstance(_hop(redirects=4, max_redirects=5), Follow)


def test_subdomain_is_own() -> None:
    """Проверяет, что поддомен площадки считается своим.

    У площадки есть поддомены, и отвергать их значило бы отвергать её же
    страницы.

    Returns:
        None
    """
    assert isinstance(_hop(location="https://support.funpay.com/help"), Follow)


def test_scheme_upgrade_is_allowed() -> None:
    """Проверяет, что переход с http на https разрешён.

    Запрещено понижение схемы, а не повышение: повышение как раз то, чего от
    перехода и ждут.

    Returns:
        None
    """
    hop = _hop(current="http://funpay.com/a", location="https://funpay.com/b")
    assert isinstance(hop, Follow)
