"""Проверяет маскировку путей в скелете.

Правило поднималось трижды, и каждый раз потому, что настоящая страница
приносила то, чего прежнее правило не ждало. Версия v3 закрыла чужие хосты
целиком - до неё имя вроде t.me/ivanpetrov проходило дословно. Версия v6 закрыла
идентификаторы без цифр: первый же снимок страницы отдельного заказа принёс
/orders/SBVZKXAF/ дословно, потому что идентификаторы площадки - восемь
заглавных латинских букв.

Отсюда правило для этого файла: каждый случай тут - не выдумка, а то, что
вправду встретилось либо вправду встретится.
"""

from __future__ import annotations

import pytest

from funora._skeleton import NUMBERED_SKELETON_FORMATS, SKELETON_FORMAT, mask_path, skeletonize

#: Что обязано быть замаскировано и почему.
MASKED: dict[str, str] = {
    "https://funpay.com/orders/SBVZKXAF/": "идентификатор заказа - заглавные без цифр",
    "/orders/12345/": "идентификатор заказа числом",
    "/users/1234/": "идентификатор пользователя",
    "/lots/AB12CD/": "идентификатор вперемешку",
    "/offer/Х5/": "идентификатор с кириллицей",
}

#: Что обязано сохраниться дословно и почему.
KEPT: dict[str, str] = {
    "/orders/": "слово маршрута",
    "/chat/": "слово маршрута",
    "/css/main.css": "путь ресурса, идентификатора в нём нет",
    "/js/app.bundle.js": "путь ресурса",
    "https://funpay.com/orders/": "свой хост со словом маршрута",
}


@pytest.mark.parametrize(("path", "why"), sorted(MASKED.items()))
def test_an_identifier_never_survives_masking(path: str, why: str) -> None:
    """Требует, чтобы идентификатор не проходил в снимок дословно.

    Args:
        path (str): Исходный путь.
        why (str): Чем этот случай важен.

    Returns:
        None
    """
    masked = mask_path(path, "funpay.com", {})
    assert "{n" in masked or "{t}" in masked, f"{path} прошёл дословно как {masked}: {why}"

    for segment in path.strip("/").split("/"):
        if segment in ("https:", "", "funpay.com") or segment.islower():
            continue
        assert segment not in masked, f"сегмент «{segment}» уцелел в {masked}: {why}"


@pytest.mark.parametrize(("path", "why"), sorted(KEPT.items()))
def test_a_route_word_survives_masking(path: str, why: str) -> None:
    """Требует, чтобы слово маршрута сохранялось.

    Без этой проверки предыдущая ничего не значила бы: маскировка, съедающая
    весь путь, тоже прячет идентификаторы - и заодно делает снимок бесполезным
    для селекторов, ради которых он и снимается.

    Args:
        path (str): Исходный путь.
        why (str): Чем этот случай важен.

    Returns:
        None
    """
    masked = mask_path(path, "funpay.com", {})
    assert masked == path, f"{path} стал {masked}, а обязан был остаться: {why}"


def test_two_identical_identifiers_get_one_number() -> None:
    """Проверяет, что различимость сохраняется после маскировки.

    Ради этого формат поднимался в v4. Пока идентификаторы схлопывались в одну
    подпись, всякая проверка, опирающаяся на их различимость, проходила впустую
    и выглядела при этом пройденной.

    Returns:
        None
    """
    ordinals: dict[str, int] = {}
    first = mask_path("/orders/SBVZKXAF/", "funpay.com", ordinals)
    same = mask_path("/orders/SBVZKXAF/", "funpay.com", ordinals)
    other = mask_path("/orders/QQWWEERR/", "funpay.com", ordinals)

    assert first == same, f"один заказ получил разные номера: {first} и {same}"
    assert first != other, f"разные заказы схлопнулись в {first}"


def test_the_current_format_numbers_identifiers() -> None:
    """Связывает нынешнюю версию формата с обещанием различимости.

    Проверки выше опираются на нумерацию. Если версию поднимут, отменив её,
    они станут проверять не то, о чём написаны.

    Returns:
        None
    """
    assert SKELETON_FORMAT in NUMBERED_SKELETON_FORMATS


def test_an_identifier_in_a_page_is_masked_too() -> None:
    """Проверяет правило целиком, на странице, а не на отдельном значении.

    Между mask_path и снимком стоит обход дерева, и правило могло бы работать в
    одном месте и не работать в другом.

    Returns:
        None
    """
    skeleton = skeletonize('<a href="/orders/SBVZKXAF/" data-href="/users/AB12/">x</a>')
    assert "SBVZKXAF" not in skeleton, skeleton
    assert "AB12" not in skeleton, skeleton
    assert "/orders/" in skeleton, "форма пути потеряна вместе с идентификатором"
