"""Проверки структурного скелета.

Главное, что здесь проверяется, - не форматирование, а невозможность утечки:
после обработки в результате не должно остаться ни одного фрагмента исходного
текста, включая имена, суммы и содержимое переписки.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from funora._skeleton import SkeletonError, _self_check, skeletonize, text_signature

#: Страница, похожая по составу на настоящую: имена, сумма, ссылки, скрипт.
SAMPLE = """
<html>
  <body>
    <div class="order-block" id="order-98765" data-offer="55512">
      <a href="/orders/98765" class="order-link">Заказ №98765</a>
      <span class="username">Иван Петров</span>
      <span class="price">1 234,10 &#8381;</span>
      <input type="hidden" name="csrf" value="a1b2c3d4e5f6">
      <script>var secret = "sessionvalue";</script>
      <!-- комментарий с именем Иван -->
    </div>
  </body>
</html>
"""


def test_no_source_text_survives() -> None:
    """Проверяет, что ни один фрагмент исходного текста не попал в скелет."""
    sk = skeletonize(SAMPLE)
    forbidden = [
        "Иван",
        "Петров",
        "Заказ",
        "98765",
        "55512",
        "1 234,10",
        "a1b2c3d4e5f6",
        "sessionvalue",
        "комментарий",
    ]
    leaked = [f for f in forbidden if f in sk]
    assert not leaked, f"в скелете найдены исходные данные: {leaked}"


def test_structure_survives() -> None:
    """Проверяет, что структура, нужная для селекторов, сохранена."""
    sk = skeletonize(SAMPLE)
    assert "div" in sk
    assert 'class="order-block"' in sk
    assert 'class="username"' in sk
    assert 'class="price"' in sk
    assert "<a " in sk
    assert "input" in sk


def test_url_shape_survives_but_id_does_not() -> None:
    """Проверяет, что форма ссылки видна, а идентификатор в ней - нет."""
    sk = skeletonize(SAMPLE)
    assert "/orders/{n1}" in sk, "по форме пути должно быть видно, что это ссылка на заказ"
    assert "/orders/98765" not in sk


def test_opaque_tags_are_emptied() -> None:
    """Проверяет, что содержимое script не сохраняется."""
    sk = skeletonize(SAMPLE)
    assert "<script></script>" in sk
    assert "var secret" not in sk


def test_opaque_tags_keep_attributes() -> None:
    """Проверяет, что у script сохраняются атрибуты, а пропадает содержимое.

    Путь скрипта обезличивается тем же правилом, что и любая другая ссылка, и
    ничего личного не несёт. Зато по составу скриптов видно, чем страница
    обновляет себя, а это ровно то, что нужно для канала обновлений.
    """
    sk = skeletonize('<html><body><script src="/js/runner-12.js">var x=1;</script></body></html>')
    assert "<script " in sk
    assert 'src="/js/{n1}"' in sk
    assert "var x" not in sk


def test_path_segment_is_masked_whole() -> None:
    """Проверяет, что сегмент с цифрами обезличивается целиком, а не по цифрам.

    Соблазн заменять только цифры велик: путь остался бы читаемым. Но сегмент
    вида ``ivan123`` превратился бы тогда в ``ivan{n}`` и выдал имя. Цифра в
    сегменте - признак того, что сегмент опознаёт кого-то, и опознающая часть
    может оказаться не только цифрами.
    """
    sk = skeletonize('<html><body><a href="/u/ivan123">t</a></body></html>')
    assert "ivan" not in sk
    assert "/u/{n1}" in sk


def test_class_attribute_is_verbatim() -> None:
    """Проверяет, что class сохраняется дословно: без него селекторы не написать."""
    sk = skeletonize('<div class="a b c">x</div>')
    assert 'class="a b c"' in sk


def test_other_attributes_are_masked() -> None:
    """Проверяет, что значения прочих атрибутов заменяются подписью."""
    sk = skeletonize('<input name="csrf" value="a1b2c3d4e5f6">')
    assert "a1b2c3d4e5f6" not in sk
    assert "name=" in sk and "value=" in sk


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12345", "T5:d"),
        ("abc", "T3:a"),
        ("Иван", "T4:c"),
        ("  \n\t ", ""),
        ("a1", "T2:ad"),
        ("a b", "T3:as"),
    ],
)
def test_text_signature(text: str, expected: str) -> None:
    """Проверяет подпись текстового узла на характерных примерах.

    Args:
        text (str): Исходный текст.
        expected (str): Ожидаемая подпись.
    """
    assert text_signature(text) == expected


def test_signature_length_is_after_normalization() -> None:
    """Проверяет, что длина считается после нормализации Unicode.

    Без нормализации одна и та же строка в разных формах даёт разную подпись, и
    сравнение фикстур между машинами перестаёт быть надёжным.
    """
    composed = "й"
    decomposed = "й"
    assert text_signature(composed) == text_signature(decomposed)


def test_empty_document_rejected() -> None:
    """Проверяет, что пустой документ не принимается."""
    with pytest.raises(SkeletonError):
        skeletonize("")
    with pytest.raises(SkeletonError):
        skeletonize("   \n  ")


def test_deterministic() -> None:
    """Проверяет, что один и тот же вход даёт побайтово один и тот же скелет.

    Недетерминированный скелет сделал бы бесполезным сравнение фикстур: любой
    повторный захват давал бы ложное изменение.
    """
    assert skeletonize(SAMPLE) == skeletonize(SAMPLE)


def test_attribute_order_is_stable() -> None:
    """Проверяет, что порядок атрибутов не зависит от порядка в исходнике."""
    a = skeletonize('<div id="x" class="c" data-k="v">t</div>')
    b = skeletonize('<div data-k="v" class="c" id="x">t</div>')
    assert a == b


def test_skeleton_is_parseable_html() -> None:
    """Проверяет, что скелет разбирается тем же парсером, что и страница.

    Это не косметика. Запись ``<div/>`` в HTML не означает пустой элемент: разбор
    откроет div и вложит в него весь остаток документа, а ``<script/>`` проглотит
    документ целиком как сырой текст. Скелет в такой записи выглядит правильно
    глазами и оказывается пустым для селекторов, то есть непригоден ни как
    фикстура, ни как материал для проверки разметки.
    """
    sk = skeletonize(SAMPLE)
    tree = HTMLParser(sk)
    assert tree.body is not None
    assert tree.css_first(".order-block") is not None
    assert tree.css_first(".username") is not None
    assert tree.css_first("a.order-link") is not None
    assert tree.css_first("script") is not None


def test_script_does_not_swallow_the_document() -> None:
    """Проверяет, что элементы после script остаются видимы парсеру.

    Отдельный тест, потому что это самый разрушительный случай: script - элемент
    с сырым текстовым содержимым, и незакрытый он уносит весь остаток документа.
    """
    sk = skeletonize('<html><body><script>x</script><div class="after">t</div></body></html>')
    assert HTMLParser(sk).css_first(".after") is not None


def test_void_tags_stay_self_closing() -> None:
    """Проверяет, что void-теги записываются одиночными, а прочие - парой."""
    sk = skeletonize('<html><body><br><div class="d"></div></body></html>')
    assert "<br/>" in sk
    assert '<div class="d"></div>' in sk


def test_self_check_rejects_text_that_looks_like_signature() -> None:
    """Проверяет, что самопроверка не принимает текст за подпись.

    Нестрогое условие «начинается с T и содержит двоеточие» пропускает строку
    вида ``Total: 500``, то есть настоящий текст страницы. Самопроверка -
    последний рубеж формата, и дыра в ней обесценивает весь остальной разбор.
    """
    looks_like_signature = """<div>
Total: 500
</div>"""
    real_signature = """<div>
T9:adps
</div>"""
    with pytest.raises(SkeletonError):
        _self_check(looks_like_signature)
    _self_check(real_signature)


def test_output_has_no_carriage_returns() -> None:
    """Проверяет, что в скелете нет CR: иначе байты зависят от системы.

    Фикстуры сравниваются между машиной разработчика и сборкой, и перевод строк
    в стиле Windows превратил бы каждое такое сравнение в ложное расхождение.
    """
    assert chr(13) not in skeletonize(SAMPLE)


def test_foreign_path_is_masked_whole() -> None:
    """Проверяет, что путь на чужом хосте маскируется целиком.

    Ссылки на чужие адреса пишут люди в переписке, и в них живёт то, ради чего
    их и пишут. Прежнее правило маскировало сегмент только при наличии цифр либо
    нелатинских знаков, поэтому имя вида ``t.me/ivanpetrov`` прошло бы дословно
    в снимок, который лежит в открытом репозитории.

    Returns:
        None
    """
    sk = skeletonize('<html><body><a href="https://t.me/ivanpetrov">т</a></body></html>')
    assert "ivanpetrov" not in sk
    assert "t.me" in sk, "имя хоста публично и остаётся"
    assert "https" in sk, "по схеме видно, защищено ли соединение"


def test_own_path_keeps_its_shape() -> None:
    """Проверяет, что защита не съела форму путей площадки.

    Форма нужна: по ней узнаётся назначение ссылки, и без неё правила извлечения
    писать не из чего.

    Returns:
        None
    """
    sk = skeletonize('<html><body><a href="https://funpay.com/orders/12345/">з</a></body></html>')
    assert "/orders/{n1}/" in sk
    assert "12345" not in sk


def test_no_foreign_login_survives_in_the_fixtures() -> None:
    """Проверяет опубликованные снимки на остатки чужих имён.

    Проверка идёт по самим файлам репозитория, а не по образцу: именно они
    лежат в открытом доступе, и именно их читает первый пришедший человек.

    Returns:
        None
    """
    import re as _re
    from pathlib import Path as _Path

    folder = _Path(__file__).parent / "fixtures" / "pages"
    link = _re.compile(r'href="(https?://[^"]*)"')

    for snapshot in folder.glob("*.skeleton.txt"):
        for url in link.findall(snapshot.read_text(encoding="utf-8")):
            if "funpay.com" in url:
                continue
            tail = url.split("//", 1)[-1]
            path = tail.split("/", 1)[1] if "/" in tail else ""
            for segment in path.split("/"):
                if not segment or segment.startswith("?"):
                    continue
                assert segment == "{t}" or _re.fullmatch(r"\{n\d*\}", segment) or not segment, (
                    f"{snapshot.name}: сегмент {segment!r} чужого адреса не замаскирован"
                )


def test_distinct_ids_stay_distinct() -> None:
    """Проверяет, что разные идентификаторы получают разные номера.

    Ради этого формат и поднят до v4. Без номеров восемь заказов на странице
    несут один и тот же адрес, и всякая проверка, опирающаяся на различимость,
    проходит впустую - выглядя при этом пройденной. На это уже наступали.

    Returns:
        None
    """
    sk = skeletonize(
        "<html><body>"
        '<a href="https://funpay.com/orders/QN2CW7HY/">a</a>'
        '<a href="https://funpay.com/orders/Q7YXFYJV/">b</a>'
        "</body></html>"
    )
    assert "/orders/{n1}/" in sk
    assert "/orders/{n2}/" in sk


def test_the_same_id_gets_the_same_number() -> None:
    """Проверяет, что одно значение всюду получает один номер.

    Иначе номера были бы просто счётчиком вхождений и о совпадении не говорили
    бы ничего.

    Returns:
        None
    """
    sk = skeletonize(
        "<html><body>"
        '<a data-href="https://funpay.com/users/777/">a</a>'
        '<a data-href="https://funpay.com/users/888/">b</a>'
        '<a data-href="https://funpay.com/users/777/">c</a>'
        "</body></html>"
    )
    assert sk.count("/users/{n1}/") == 2
    assert sk.count("/users/{n2}/") == 1


def test_numbering_does_not_reach_foreign_hosts() -> None:
    """Проверяет, что чужие адреса остаются неразличимыми.

    Номер говорит, совпадают ли два значения. Для заказов это нужно и безобидно.
    Для ссылок, написанных людьми в переписке, - нет: там совпадение само по
    себе сведение о третьем лице, а никакой проверке оно не нужно.

    Returns:
        None
    """
    sk = skeletonize(
        "<html><body>"
        '<a href="https://t.me/ivanpetrov">a</a>'
        '<a href="https://t.me/otherguy">b</a>'
        '<a href="https://t.me/ivanpetrov">c</a>'
        "</body></html>"
    )
    assert sk.count('href="https://t.me/{t}"') == 3
    assert "{t1}" not in sk


def test_numbering_is_repeatable() -> None:
    """Проверяет, что нумерация не зависит от запуска.

    Недетерминированная нумерация сделала бы бесполезным сравнение фикстур:
    любой повторный захват давал бы ложное изменение во всех ссылках сразу.

    Returns:
        None
    """
    doc = (
        "<html><body>"
        '<a href="https://funpay.com/orders/AAA1/">a</a>'
        '<a href="https://funpay.com/orders/BBB2/">b</a>'
        "</body></html>"
    )
    assert skeletonize(doc) == skeletonize(doc)


def test_numbering_does_not_cross_documents() -> None:
    """Проверяет, что номера в разных документах между собой не связаны.

    Это ловушка, которую вводит сам формат, и назвать её надо вслух: один и тот
    же заказ получает разные номера в двух снимках, а разные заказы - одинаковые.
    Сравнивать снимки по номерам нельзя.

    Returns:
        None
    """
    first = skeletonize('<html><body><a href="/orders/111/">a</a></body></html>')
    second = skeletonize('<html><body><a href="/orders/222/">a</a></body></html>')
    assert "/orders/{n1}/" in first
    assert "/orders/{n1}/" in second, "номер отсчитывается заново в каждом документе"


def test_dialogs_are_distinguishable_by_query() -> None:
    """Проверяет, что диалоги различимы, хотя идентификатор лежит в запросе.

    Ради этого формат поднят до v5. В v4 нумеровался только путь, а у диалога
    путь один на всех - ``/chat/`` - и весь идентификатор сидит в строке
    запроса. Пятьдесят диалогов снимка были неразличимы полностью, и проверки
    курсора по ним проходили впустую, выглядя пройденными.

    Returns:
        None
    """
    sk = skeletonize(
        "<html><body>"
        '<a href="/chat/?node=281916231">a</a>'
        '<a href="/chat/?node=999000111">b</a>'
        '<a href="/chat/?node=281916231">c</a>'
        "</body></html>"
    )
    assert sk.count("/chat/?{q1}") == 2
    assert sk.count("/chat/?{q2}") == 1


def test_attribute_signature_keeps_its_shape_and_gains_a_number() -> None:
    """Проверяет, что номер добавляется к подписи, а не заменяет её.

    Длина и состав нужны: именно на них строились выводы о позициях сообщений -
    девять знаков против десяти. Заменить подпись номером значило бы выиграть
    различимость ценой того, ради чего формат затевался.

    Returns:
        None
    """
    sk = skeletonize(
        "<html><body>"
        '<i data-id="281916231" data-node-msg="1234567890">a</i>'
        '<i data-id="999000111" data-node-msg="1234567890">b</i>'
        "</body></html>"
    )
    assert 'data-id="T9:d#1"' in sk
    assert 'data-id="T9:d#2"' in sk
    # Одинаковая позиция у обоих - один номер, и подпись по-прежнему T10.
    assert sk.count('data-node-msg="T10:d#1"') == 2


def test_numbering_is_separate_per_attribute() -> None:
    """Проверяет, что разряды нумерации не пересекаются.

    Совпадение номера пути с номером атрибута ничего не значило бы и только
    вводило бы в заблуждение: это разные пространства значений.

    Returns:
        None
    """
    sk = skeletonize(
        '<html><body><a data-id="111" data-node-msg="222" href="/orders/333/">a</a></body></html>'
    )
    assert 'data-id="T3:d#1"' in sk
    assert 'data-node-msg="T3:d#1"' in sk
    assert "/orders/{n1}/" in sk


def test_foreign_query_is_not_numbered() -> None:
    """Проверяет, что строка запроса чужого адреса остаётся неразличимой.

    По той же причине, что и путь: там совпадение само по себе сведение о
    третьем лице, а никакой проверке оно не нужно.

    Returns:
        None
    """
    sk = skeletonize(
        "<html><body>"
        '<a href="https://t.me/ivan?ref=1">a</a>'
        '<a href="https://t.me/other?ref=2">b</a>'
        "</body></html>"
    )
    assert sk.count('href="https://t.me/{t}?{q}"') == 2
    assert "{q1}" not in sk


def test_text_nodes_are_not_numbered() -> None:
    """Проверяет, что содержимое равенством не выдаётся.

    Граница проведена по различию «разметка против содержимого». Атрибут -
    разметка, и равенство двух его значений нужно проверкам. Текст -
    содержимое, и равенство двух сообщений переписки никакой проверке не нужно,
    а сведением о переписке является.

    Returns:
        None
    """
    sk = skeletonize("<html><body><p>одно и то же</p><p>одно и то же</p></body></html>")
    assert sk.count("T12:cs") == 2
    assert "#1" not in sk
