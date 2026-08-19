"""Регрессия классификатора на настоящих страницах площадки.

Фикстуры в tests/fixtures/pages - структурные скелеты реальных ответов, снятые
инструментом funora-observe. Сырого HTML в них нет по построению формата, но
разметка сохранена целиком, поэтому селекторы проверяются ровно те же, что
работают на живой странице.

Смысл этих тестов в том, что признаки в DEFAULT_SIGNATURES выведены из этих
самых снимков. Без регрессии ничто не мешает позже поправить признак так, что он
перестанет узнавать страницу, ради которой был написан, и отказ будет тихим.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from funora._classify import ResponseClass, classify
from funora._skeleton import SUPPORTED_SKELETON_FORMATS, _self_check

#: Каталог с фикстурами страниц.
PAGES = Path(__file__).parent / "fixtures" / "pages"

#: Ожидаемый вердикт для каждой фикстуры.
EXPECTED = {
    "orders-trade.logged.ru": ResponseClass.OK,
    "orders-trade.states.logged.ru": ResponseClass.OK,
    "chat.logged.ru": ResponseClass.OK,
    "chat-thread.logged.ru": ResponseClass.OK,
    "orders-trade.guest.ru": ResponseClass.LOGIN_REQUIRED,
}


def _read(name: str) -> str:
    """Читает скелет фикстуры.

    Args:
        name (str): Имя фикстуры без расширения.

    Returns:
        str: Содержимое скелета.
    """
    return (PAGES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_parses(name: str) -> None:
    """Проверяет, что фикстура разбирается и содержит узлы.

    Args:
        name (str): Имя фикстуры.
    """
    tree = HTMLParser(_read(name))
    assert tree.body is not None, "тело документа не разобралось"
    assert len(tree.css("*")) > 100, "дерево подозрительно мелкое"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_classification(name: str) -> None:
    """Проверяет вердикт классификатора на снимке настоящей страницы.

    Args:
        name (str): Имя фикстуры.
    """
    verdict = classify(
        status=200,
        final_url="https://funpay.com/orders/trade",
        html=_read(name),
        expected_host="funpay.com",
    )
    assert verdict.cls is EXPECTED[name]
    assert not verdict.provisional, (
        "вердикт на настоящей странице не должен опираться на непроверенный признак"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_contains_no_text(name: str) -> None:
    """Проверяет, что фикстура удовлетворяет формату скелета.

    Фикстуры лежат в открытом репозитории, поэтому проверка повторяется здесь, а
    не считается выполненной один раз при захвате.

    Args:
        name (str): Имя фикстуры.
    """
    _self_check(_read(name))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_has_provenance(name: str) -> None:
    """Проверяет наличие и состав описания происхождения.

    Args:
        name (str): Имя фикстуры.
    """
    data = json.loads((PAGES / f"{name}.provenance.json").read_text(encoding="utf-8"))
    for key in ("path", "captured_at", "http_status", "locale", "format"):
        assert key in data, f"в описании нет поля {key}"
    # Версия сверяется с кодом, а не записана числом: иначе она расходится
    # с форматом молча, и снимок начинает читаться правилами, по которым
    # его не снимали.
    assert data["format"] in SUPPORTED_SKELETON_FORMATS


def test_logged_and_guest_markers_do_not_overlap() -> None:
    """Проверяет, что признаки вошедшего и гостя взаимно исключают друг друга.

    Это условие важнее самих селекторов: если признак встречается на обеих
    страницах, он не различает состояния, и классификатор построен на песке.
    """
    logged = HTMLParser(_read("orders-trade.logged.ru"))
    guest = HTMLParser(_read("orders-trade.guest.ru"))

    only_logged = (".navbar-toggle-logged",)
    only_guest = (
        ".navbar-toggle-guest",
        ".menu-item-login",
        ".menu-item-register",
        ".content-account-login",
    )
    for sel in only_logged:
        assert logged.css_first(sel) is not None, f"{sel} пропал у вошедшего"
        assert guest.css_first(sel) is None, f"{sel} нашёлся у гостя"
    for sel in only_guest:
        assert guest.css_first(sel) is not None, f"{sel} пропал у гостя"
        assert logged.css_first(sel) is None, f"{sel} нашёлся у вошедшего"


def test_system_message_is_recognized_structurally() -> None:
    """Проверяет, что системное сообщение отличается от пользовательского разметкой.

    Это самая дорогая проверка во всём наборе. Если системное сообщение об оплате
    отличается от обычного только текстом, покупатель отправляет сообщение с
    таким же текстом, и бот, доверяющий тексту, выдаёт товар. Признак обязан быть
    структурным, и подделать его отправитель не должен уметь.

    Признаков три, и на снимке они согласованы полностью: обёртка alert в теле,
    отсутствие ссылки на автора и ярлык label-primary.
    """
    tree = HTMLParser(_read("chat-thread.logged.ru"))
    messages = tree.css(".chat-msg-item")
    assert len(messages) == 10, "снимок содержит десять сообщений"

    system, human = [], []
    for node in messages:
        (system if node.css_first("a.chat-msg-author-link") is None else human).append(node)

    assert len(system) == 6
    assert len(human) == 4

    for node in system:
        assert node.css_first(".chat-msg-body .alert") is not None, (
            "у системного сообщения обязана быть обёртка alert"
        )
        label = node.css_first(".chat-msg-author-label")
        assert label is not None
        assert "label-primary" in (label.attributes.get("class") or "")

    for node in human:
        assert node.css_first(".chat-msg-body .alert") is None, (
            "у сообщения пользователя обёртки alert быть не должно"
        )


def test_author_link_and_alert_never_coincide() -> None:
    """Проверяет, что два признака системного сообщения не пересекаются.

    Правило в спецификации фиксирует закрытый отказ: системным сообщение
    считается, только если обёртка есть и ссылки на автора нет. Проверка нужна
    затем, что при пересечении признаков такое правило не даст ни одного
    срабатывания, и отказ будет тихим.
    """
    for node in HTMLParser(_read("chat-thread.logged.ru")).css(".chat-msg-item"):
        has_alert = node.css_first(".chat-msg-body .alert") is not None
        has_author = node.css_first("a.chat-msg-author-link") is not None
        assert has_alert != has_author, "признаки обязаны быть взаимно исключающими"


def test_message_carries_own_dom_id() -> None:
    """Проверяет, что у каждого сообщения есть собственный идентификатор в разметке.

    Без него позиция переигрывания не адресует ничего конкретного: знать, что
    после нашей отметки что-то появилось, и уметь показать, что именно, - разные
    возможности.
    """
    for node in HTMLParser(_read("chat-thread.logged.ru")).css(".chat-msg-item"):
        assert node.attributes.get("id"), "у сообщения нет идентификатора"


def test_message_text_may_contain_foreign_links() -> None:
    """Проверяет наличие внешних ссылок в тексте сообщения.

    Проверка закрепляет факт, а не желаемое: текст сообщения содержит ссылки,
    которые ввёл собеседник. Ни один SDK не имеет права по ним ходить, и правило
    в спецификации опирается на это наблюдение.
    """
    links = HTMLParser(_read("chat-thread.logged.ru")).css(".chat-msg-text a[href]")
    external = [
        a for a in links if not (a.attributes.get("href") or "").startswith("https://funpay.com")
    ]
    assert external, "в снимке есть сообщение со ссылкой на сторонний сайт"


def test_every_snapshot_is_registered() -> None:
    """Проверяет, что ни один снимок не лежит в каталоге без проверок.

    Перечень EXPECTED заполняется руками, и это его слабое место: снимок,
    положенный рядом и забытый, не проверяется ни на утечку текста, ни на
    вердикт классификатора, ни на формат. Он при этом лежит в открытом
    репозитории и выглядит проверенным - как и все соседние файлы.

    Проверка идёт по каталогу, а не по перечню: только так она замечает то,
    чего в перечне нет.

    Returns:
        None
    """
    on_disk = {p.name.removesuffix(".skeleton.txt") for p in PAGES.glob("*.skeleton.txt")}
    assert on_disk == set(EXPECTED), (
        f"снимки без проверок: {sorted(on_disk - set(EXPECTED))}; "
        f"проверки без снимков: {sorted(set(EXPECTED) - on_disk)}"
    )


def test_every_snapshot_has_provenance() -> None:
    """Проверяет, что у каждого снимка есть описание захвата.

    Без описания снимок нельзя ни повторить, ни датировать, ни объяснить: он
    превращается в разметку неизвестного происхождения, которой почему-то верят.

    Returns:
        None
    """
    for snapshot in sorted(PAGES.glob("*.skeleton.txt")):
        beside = snapshot.with_name(snapshot.name.replace(".skeleton.txt", ".provenance.json"))
        assert beside.exists(), f"{snapshot.name}: рядом нет provenance"


def test_provenance_says_whether_the_file_was_converted() -> None:
    """Проверяет, что описание захвата различает снимок и его преобразование.

    Поле format говорит, каков файл сейчас. Само по себе оно вводит в
    заблуждение: четыре снимка помечены текущим форматом, а сняты были прежними
    и преобразованы повторной маскировкой. Расхождение между таким файлом и
    снятым нативно объясняется сменой формата с тем же успехом, что и сменой
    разметки, - и решить, что важнее, можно только зная происхождение.

    Returns:
        None
    """
    for snapshot in sorted(PAGES.glob("*.provenance.json")):
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        assert "captured_format" in data, f"{snapshot.name}: не сказано, в чём снят"
        assert "converted" in data, f"{snapshot.name}: не сказано, преобразован ли"
        assert data["converted"] is (data["captured_format"] != data["format"]), (
            f"{snapshot.name}: пометка преобразования расходится с форматами"
        )
