r"""Формат структурного скелета страницы.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/conformance/skeleton-format.yaml в репозитории Funora-spec.
Перестроить: .venv\Scripts\python.exe tools/codegen.py

Формат снимков страниц. Снимки - общая проверочная база: по ним
сверяется, что объявленный селектор вправду присутствует на
наблюдённой странице.

Версия формата и набор классов знаков порождаются, а не пишутся здесь.
Прежде версия была литералом в _skeleton.py, а описание формата жило
только в README фикстур эталонной реализации: вторая реализация не
могла ни построить такой же скелет, ни проверить, что построила.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "SKELETON_FORMAT",
    "ACCEPTED_SKELETON_FORMATS",
    "NUMBERED_SKELETON_FORMATS",
    "CHARACTER_CLASSES",
]


#: Версия формата, которой снимаются новые скелеты.
SKELETON_FORMAT: Final[str] = "structural-skeleton-v9"

#: Версии, которые разрешено читать.
#:
#: Старые версии принимаются, а не отвергаются: снимок стоит
#: живого запроса под сессией, и переснять его бывает нечем -
#: истечение сессии воспроизводится не по желанию.
ACCEPTED_SKELETON_FORMATS: Final[frozenset[str]] = frozenset(
    {
        "structural-skeleton-v3",
        "structural-skeleton-v4",
        "structural-skeleton-v5",
        "structural-skeleton-v6",
        "structural-skeleton-v7",
        "structural-skeleton-v8",
        "structural-skeleton-v9",
    }
)

#: Версии, в которых идентификаторы различимы между собой.
#:
#: Пока они схлопывались в одну подпись, всякая проверка курсора,
#: гашения и порождения событий проходила впустую и выглядела при
#: этом пройденной. Требовать различимости от снимка версии v3
#: нечестно - восстановить её он не может, - а от прочих обязательно.
NUMBERED_SKELETON_FORMATS: Final[frozenset[str]] = frozenset(
    {
        "structural-skeleton-v4",
        "structural-skeleton-v5",
        "structural-skeleton-v6",
        "structural-skeleton-v7",
        "structural-skeleton-v8",
        "structural-skeleton-v9",
    }
)

#: Классы знаков, из которых складывается подпись текста.
#:
#: Две реализации с разными наборами дадут разные подписи одному
#: тексту, и снимок одной перестанет годиться другой.
CHARACTER_CLASSES: Final[dict[str, str]] = {
    "a": "Латиница любого регистра.",
    "c": "Кириллица любого регистра.",
    "d": "Цифры.",
    "o": "Всё прочее.",
    "p": "Пунктуация ASCII.",
    "s": "Пробельные знаки.",
}
