r"""Обрезает скелет страницы до фикстуры, сохраняя РАЗНООБРАЗИЕ строк.

Зачем. Снимок публичного списка предложений раздела - семь с половиной мегабайт
и три тысячи строк. В репозиторий такое не кладут: каталог фикстур весит два с
третью мегабайта целиком, и один снимок увеличил бы его вчетверо, навсегда и у
каждого, кто когда-либо склонирует репозиторий.

Обрезка - НЕ наблюдение, и путать их нельзя. Полученный файл - производный
артефакт: он годится, чтобы проверять РАЗБОР, и не годится, чтобы утверждать о
площадке. Утверждения о числах остаются за самим наблюдением, и проверки,
которым нужны числа, читают его из observations/ и пропускаются без него.

Поэтому правило обрезки написано здесь, а не выполнено руками: производный файл,
которого нельзя воспроизвести, ничем не лучше выдуманного.

ЧТО СОХРАНЯЕТСЯ. Каждая РАЗЛИЧНАЯ форма строки - по нескольку штук. Форма строки
- это набор её классов и набор имён её атрибутов; строки с одинаковой формой
взаимозаменяемы для разбора, с разной - нет.

Правило выбрано после того, как простое «первые N» потеряло бы два вида строк из
трёх: на снятой странице поднятое предложение стоит первым, ещё шестьдесят
девять - около тысячной строки, а ленивая загрузка начинается с двухсотой.

Запуск:
    .venv\Scripts\python.exe tools/trim_skeleton.py ИСХОДНИК СЕЛЕКТОР ВЫХОД [ШТУК]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Final

from selectolax.parser import HTMLParser, Node

#: Сколько строк каждой формы оставлять по умолчанию.
PER_SHAPE: Final[int] = 6


def shape_of(node: Node) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Возвращает форму строки: её классы и имена её атрибутов.

    Значения атрибутов в форму НЕ входят: они у каждой строки свои, и по ним
    всякая строка оказалась бы единственной в своём роде.

    Аргументы:
        node (Node): узел строки.

    Возвращает:
        tuple[tuple[str, ...], tuple[str, ...]]: классы и имена атрибутов.
    """
    attributes = node.attributes or {}
    return (
        tuple(sorted((attributes.get("class") or "").split())),
        tuple(sorted(one for one in attributes if one != "class")),
    )


def trim(html: str, selector: str, per_shape: int) -> tuple[str, Counter[object], Counter[object]]:
    """Убирает лишние строки, оставляя по нескольку каждой формы.

    Аргументы:
        html (str): исходный скелет.
        selector (str): селектор строки.
        per_shape (int): сколько строк каждой формы оставить.

    Возвращает:
        tuple[str, Counter[object], Counter[object]]: обрезанный скелет, формы
        исходника и формы результата.
    """
    tree = HTMLParser(html)
    rows = tree.css(selector)

    before: Counter[object] = Counter(shape_of(one) for one in rows)
    kept: Counter[object] = Counter()

    # Строки убираются с КОНЦА: удаление сдвигает разметку, и обход с начала
    # ломал бы собственные же смещения.
    for node in reversed(rows):
        shape = shape_of(node)
        if kept[shape] >= per_shape:
            node.decompose()
            continue
        kept[shape] += 1

    return tree.html or "", before, kept


def main(argv: list[str]) -> int:
    """Обрезает скелет и печатает, что осталось.

    Аргументы:
        argv (list[str]): исходник, селектор, выход и число штук на форму.

    Возвращает:
        int: код выхода.
    """
    if len(argv) < 3:
        print(__doc__)
        return 2

    source = Path(argv[0])
    selector = argv[1]
    target = Path(argv[2])
    per_shape = int(argv[3]) if len(argv) > 3 else PER_SHAPE

    if not source.is_file():
        print(f"нет файла: {source}")
        return 2

    html = source.read_text(encoding="utf-8")
    trimmed, before, kept = trim(html, selector, per_shape)
    target.write_text(trimmed, encoding="utf-8", newline="\n")

    print(f"исходник: {len(html):>9} знаков, строк {sum(before.values())}")
    print(f"результат: {len(trimmed):>8} знаков, строк {sum(kept.values())}")
    print(f"форм строки: {len(before)}, и каждая сохранена")
    for shape, count in before.most_common():
        classes, attributes = shape  # type: ignore[misc]
        print(f"  было {count:>5}, осталось {kept[shape]:>2}: классы {list(classes)}")
        print(f"         атрибуты {list(attributes)}")

    missing = set(before) - set(kept)
    if missing:
        print(f"ПОТЕРЯНЫ ФОРМЫ: {sorted(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
