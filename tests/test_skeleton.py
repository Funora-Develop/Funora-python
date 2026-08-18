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
    assert "/orders/{n}" in sk, "по форме пути должно быть видно, что это ссылка на заказ"
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
    assert 'src="/js/{n}"' in sk
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
    assert "/u/{n}" in sk


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
