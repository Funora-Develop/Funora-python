"""Проверки структурного скелета.

Главное, что здесь проверяется, - не форматирование, а невозможность утечки:
после обработки в результате не должно остаться ни одного фрагмента исходного
текста, включая имена, суммы и содержимое переписки.
"""

from __future__ import annotations

import pytest

from funora._skeleton import SkeletonError, skeletonize, text_signature

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
        "Иван", "Петров", "Заказ", "98765", "55512",
        "1 234,10", "a1b2c3d4e5f6", "sessionvalue", "комментарий",
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
    assert "<script/>" in sk
    assert "var secret" not in sk


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
