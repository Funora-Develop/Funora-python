"""Правило «тот ли это хост».

Файл появился после разбора, который нашёл это правило написанным в проекте
трижды и по-разному: верно в классификаторе, подстрокой в разборе переписки и
отсутствующим в транспорте. Последнее стоило сессионного ключа: переход по
заголовку Location уводил запрос на чужой адрес вместе с секретом.

Сравнение подстрокой особенно коварно тем, что выглядит работающим. Адрес
``https://funpay.com.evil.example/`` содержит ``funpay.com`` и проходит такую
проверку, а ведёт совсем не туда.

Правило одно на весь пакет и живёт здесь: разъехаться трём копиям было легко,
одной копии - не с чем.
"""

from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["host_of", "same_host", "is_safe_hop"]


def host_of(url: str) -> str:
    """Извлекает хост из адреса.

    Args:
        url (str): Адрес.

    Returns:
        str: Хост без порта, в нижнем регистре. Пустая строка, если адреса нет.
    """
    return (urlparse(url).hostname or "").lower()


def same_host(url: str, expected: str) -> bool:
    """Сообщает, принадлежит ли адрес ожидаемому хосту.

    Поддомен ожидаемого хоста считается своим: у площадки есть поддомены, и
    отвергать их значило бы отвергать её же страницы. Сравнение идёт по границе
    точки, а не подстрокой: адрес вида ``funpay.com.evil.example`` содержит
    ожидаемый хост как подстроку и ведёт не туда.

    Args:
        url (str): Проверяемый адрес.
        expected (str): Ожидаемый хост. Порт, если он есть, отбрасывается.

    Returns:
        bool: True, если адрес принадлежит ожидаемому хосту либо его поддомену.
    """
    want = expected.split("@")[-1].split(":")[0].strip().lower().rstrip(".")
    if not want:
        return False

    actual = host_of(url).rstrip(".")
    if not actual:
        return False

    return actual == want or actual.endswith("." + want)


def is_safe_hop(current: str, target: str, expected: str) -> bool:
    """Решает, можно ли отправить секрет по следующему переходу.

    Два условия, и оба обязательны.

    Целевой адрес принадлежит ожидаемому хосту. Иначе секрет уходит туда, где
    ему делать нечего, и одного заголовка Location достаточно для угона
    аккаунта.

    Схема не понижается. Переход с https на http отдаёт секрет открытым текстом
    любому, кто видит трафик, и заметить это по поведению клиента невозможно.

    Args:
        current (str): Адрес, с которого выполняется переход.
        target (str): Адрес перехода.
        expected (str): Ожидаемый хост площадки.

    Returns:
        bool: True, если переход безопасен.
    """
    if not same_host(target, expected):
        return False

    was_secure = urlparse(current).scheme == "https"
    goes_secure = urlparse(target).scheme == "https"
    return goes_secure or not was_secure
