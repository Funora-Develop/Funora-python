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
from funora._skeleton import (
    NUMBERED_SKELETON_FORMATS,
    SUPPORTED_SKELETON_FORMATS,
    SkeletonError,
    _self_check,
    skeletonize,
)

#: Каталог с фикстурами страниц.
PAGES = Path(__file__).parent / "fixtures" / "pages"

#: Ожидаемый вердикт для каждой фикстуры.
EXPECTED = {
    "order.logged.ru": ResponseClass.OK,
    "orders-trade.logged.ru": ResponseClass.OK,
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

    system, human = [], []
    for node in messages:
        (system if node.css_first("a.chat-msg-author-link") is None else human).append(node)

    # Числами здесь не проверяется ничего: точный состав снимка меняется при
    # каждой пересъёмке и сам по себе ни о чём не говорит. Проверяется другое -
    # что оба вида сообщений в снимке есть, иначе согласованность признаков
    # ниже подтвердилась бы на пустом множестве.
    assert system, "в снимке нет системных сообщений, проверять признак не на чем"
    assert human, "в снимке нет сообщений пользователя, сравнивать не с чем"

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


def test_rows_are_distinguishable_in_current_format() -> None:
    """Проверяет, что строки снимка отличимы друг от друга.

    Ради этого формат и поднимался дважды. Пока идентификаторы схлопывались в
    одну подпись, всякая проверка курсора, гашения и порождения событий
    проходила впустую - и выглядела при этом пройденной. Дважды на это
    наступали.

    Проверка идёт по снимкам НУМЕРУЮЩИХ версий, а не одной текущей. Снятые до
    нумерации различимость восстановить не могут, и требовать её от них
    нечестно; снятые после - обязаны, сколько бы версий с тех пор ни прошло.

    Прежде здесь стояло равенство текущему формату, и первый же подъём версии
    по другой причине оставил проверку без единого снимка. Она честно упала на
    «проверять нечего» - но это и была вся её защита: сравнение с одной версией
    отменяет проверку при каждом подъёме.

    Returns:
        None
    """
    checked = 0
    for name in sorted(EXPECTED):
        data = json.loads((PAGES / f"{name}.provenance.json").read_text(encoding="utf-8"))
        if data["format"] not in NUMBERED_SKELETON_FORMATS:
            continue

        tree = HTMLParser(_read(name))
        for selector, attribute in (("a.tc-item", "href"), (".contact-item", "href")):
            rows = tree.css(selector)
            if len(rows) < 2:
                continue
            values = [(r.attributes.get(attribute) or "") for r in rows]
            assert len(set(values)) == len(values), (
                f"{name}: {len(rows)} строк {selector}, "
                f"а различимых значений {attribute} только {len(set(values))}"
            )
            checked += 1

    assert checked, "ни одного снимка нумерующей версии со строками - проверять нечего"


#: Пары «что подсунули - что должно случиться».
#:
#: Кириллица выбрана не для красоты: аудитория площадки русскоязычная, и утечка
#: текста выглядела бы именно так. Латиница здесь ничего не доказала бы - её
#: полно в именах классов и в путях, которые формат сохраняет намеренно.
LEAKS: dict[str, str] = {
    "кириллица в имени класса": '<div class="кнопка">x</div>',
    "кириллица в имени атрибута": '<div class="a" данные="1">x</div>',
}


@pytest.mark.parametrize(("label", "html"), sorted(LEAKS.items()))
def test_cyrillic_in_a_structural_line_is_refused(label: str, html: str) -> None:
    """Проверяет, что кириллица в структурной строке отвергается.

    Формат обещает: текстов в скелете нет. Обещание держится не тем, что при
    захвате всё вычистили, а тем, что скелет проверяет себя сам - разбор
    приходит от стороннего парсера, и смена его поведения не должна тихо
    превратить безопасный формат в небезопасный.

    Ветка про кириллицу существовала и не проверялась ничем: снять её можно
    было незаметно, и весь набор оставался зелёным. Между тем это ровно то
    место, где утечка и произошла бы: класс и имя атрибута сохраняются
    дословно, потому что без них не написать селектора.

    Args:
        label (str): Что подсунули.
        html (str): Разметка.

    Returns:
        None
    """
    with pytest.raises(SkeletonError, match="кириллиц"):
        skeletonize(html)


def test_cyrillic_text_does_not_reach_the_skeleton() -> None:
    """Проверяет обратную половину: текст на кириллице маскируется, а не падает.

    Отвергать страницу из-за русского текста было бы негодно - он там всегда.
    Текст обязан превратиться в подпись, и в скелете кириллицы не остаться.

    Returns:
        None
    """
    skeleton = skeletonize('<div class="a">Алёша купил ключ</div>')

    leaked = sorted({ch for ch in skeleton if "Ѐ" <= ch <= "ӿ"})
    assert not leaked, f"кириллица дошла до скелета: {''.join(leaked)}"
    assert "Алёша" not in skeleton
    assert "T16:cs" in skeleton or "T" in skeleton, "текст не превратился в подпись"
