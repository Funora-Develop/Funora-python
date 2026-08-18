"""Структурный скелет страницы.

Фикстуры нужны, чтобы чинить парсер и ловить изменения разметки. Сохранять для
этого настоящий HTML со страницы под авторизацией нельзя: там имена покупателей,
тексты переписки, номера заказов. Редактирование такого HTML - подход, при котором
вопрос «достаточно ли сработала редакция» остаётся открытым навсегда, а git-историю
из форков и кэшей потом не вычистить.

Скелет решает это иначе: он сохраняет всё, что нужно для селекторов, и физически
не может содержать персональных данных.

  * Структура сохраняется целиком: теги, вложенность, порядок, атрибут class.
  * Каждый текстовый узел заменяется подписью ``T{длина}:{классы символов}``.
  * Путь в ссылке сохраняется, но сегменты с цифрами становятся ``{n}``, а
    непечатаемые латиницей - ``{t}``. ``/orders/12345`` превращается в
    ``/orders/{n}``: видно, что это ссылка на заказ, но не видно какой.
  * Содержимое script и style выбрасывается целиком.
  * Результат остаётся разбираемым HTML: пустые элементы записываются парой
    тегов, и только настоящие void-теги - одиночным. Это позволяет применять
    скелет как фикстуру и проверять на нём селекторы тем же парсером, которым
    разбирается настоящая страница.

Классы символов в подписи: ``d`` цифры, ``a`` латиница, ``c`` кириллица,
``s`` пробельные, ``p`` пунктуация ASCII, ``o`` прочее.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from selectolax.parser import HTMLParser, Node

__all__ = ["skeletonize", "text_signature", "SkeletonError", "SKELETON_FORMAT"]

#: Имя формата, записываемое в описание происхождения фикстуры.
#:
#: Версия 1 записывала пустые элементы как ``<div/>`` и потому не разбиралась
#: HTML-парсером. Файлы той версии несовместимы с этой и требуют преобразования.
SKELETON_FORMAT: Final[str] = "structural-skeleton-v2"

#: Атрибуты, значение которых обрабатывается как путь.
_URL_ATTRS: Final[frozenset[str]] = frozenset({"href", "src", "action", "formaction", "data-url"})

#: Атрибуты, значение которых сохраняется как есть.
_VERBATIM_ATTRS: Final[frozenset[str]] = frozenset({"class"})

#: Теги, содержимое которых не сохраняется вообще.
_OPAQUE_TAGS: Final[frozenset[str]] = frozenset({"script", "style", "noscript", "template"})

#: Теги, которые в HTML не имеют закрывающей пары.
#:
#: Список нужен затем, что запись ``<div/>`` в HTML не означает пустой элемент:
#: разбор откроет div и вложит в него весь остаток документа. Для script это ещё
#: хуже - его содержимое считается сырым текстом до закрывающего тега, которого
#: нет, и в дерево не попадает вообще ничего. Скелет обязан читаться тем же
#: парсером, что и страница, иначе он не годится ни в фикстуры, ни в проверку.
_VOID_TAGS: Final[frozenset[str]] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Строгая форма подписи текстового узла.
#:
#: Проверка по этому выражению, а не по «начинается с T и содержит двоеточие»:
#: строка вида ``Total: 500`` подходит под нестрогое условие и прошла бы
#: самопроверку как подпись, унеся с собой настоящий текст страницы.
_SIGNATURE_RE: Final[re.Pattern[str]] = re.compile(r"^T[1-9][0-9]*:[dacspo]+$")

#: Подпись сегмента пути, содержащего цифры.
_SEG_NUM: Final[str] = "{n}"

#: Подпись сегмента пути, содержащего символы вне латиницы.
_SEG_TEXT: Final[str] = "{t}"

_RE_LATIN = re.compile(r"[A-Za-z]")
_RE_DIGIT = re.compile(r"[0-9]")


class SkeletonError(RuntimeError):
    """Скелет не удалось построить или он не прошёл самопроверку."""


def _char_class(ch: str) -> str:
    """Определяет класс одного символа.

    Args:
        ch (str): Символ.

    Returns:
        str: Односимвольный код класса: d, a, c, s, p или o.
    """
    if ch.isspace():
        return "s"
    if ch.isdigit():
        return "d"
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return "a"
    if "Ѐ" <= ch <= "ӿ":
        return "c"
    if ch.isascii() and not ch.isalnum():
        return "p"
    return "o"


def text_signature(text: str) -> str:
    """Строит подпись текстового узла.

    Подпись сохраняет длину и состав текста, но не сам текст. Этого достаточно,
    чтобы заметить изменение разметки и написать селектор, и недостаточно, чтобы
    восстановить содержимое.

    Args:
        text (str): Исходный текст узла.

    Returns:
        str: Подпись вида ``T14:dps`` или пустая строка, если текст состоит
        только из пробельных символов.
    """
    stripped = text.strip()
    if not stripped:
        return ""
    normalized = unicodedata.normalize("NFC", stripped)
    classes = "".join(sorted({_char_class(c) for c in normalized}))
    return f"T{len(normalized)}:{classes}"


def _mask_path(value: str) -> str:
    """Обезличивает значение атрибута, содержащего путь.

    Сохраняет форму пути, потому что по ней узнаётся назначение ссылки. Сегменты
    с цифрами и сегменты с нелатинскими символами заменяются подписями: именно там
    живут идентификаторы и имена.

    Args:
        value (str): Исходное значение атрибута.

    Returns:
        str: Обезличенный путь.
    """
    if not value:
        return value

    query = ""
    body = value
    if "?" in body:
        body, _, _ = body.partition("?")
        query = "?{q}"

    parts = body.split("/")
    out: list[str] = []
    for part in parts:
        if not part:
            out.append(part)
        elif _RE_DIGIT.search(part):
            out.append(_SEG_NUM)
        elif not part.isascii():
            out.append(_SEG_TEXT)
        else:
            out.append(part)
    return "/".join(out) + query


def _mask_attr(name: str, value: str) -> str:
    """Обезличивает значение произвольного атрибута.

    Args:
        name (str): Имя атрибута в нижнем регистре.
        value (str): Исходное значение.

    Returns:
        str: Значение, пригодное для хранения в репозитории.
    """
    if name in _VERBATIM_ATTRS:
        return value
    if name in _URL_ATTRS:
        return _mask_path(value)
    if not value:
        return value
    sig = text_signature(value)
    return sig or ""


def _render(node: Node, out: list[str], depth: int) -> None:
    """Рекурсивно записывает узел в выходной буфер.

    Args:
        node (Node): Узел документа.
        out (list[str]): Буфер строк результата.
        depth (int): Текущая глубина вложенности, для отступов.

    Returns:
        None: Результат накапливается в out.
    """
    tag = node.tag
    pad = "  " * depth

    if tag == "-text":
        sig = text_signature(node.text_content or "")
        if sig:
            out.append(f"{pad}{sig}")
        return

    if tag in ("-comment", "_comment"):
        return

    if tag in _OPAQUE_TAGS:
        out.append(f"{pad}<{tag}></{tag}>")
        return

    attrs = node.attributes or {}
    rendered: list[str] = []
    for name in sorted(attrs):
        raw = attrs[name]
        if raw is None:
            rendered.append(name)
            continue
        masked = _mask_attr(name.lower(), raw)
        rendered.append(f'{name}="{masked}"')

    head = tag if not rendered else tag + " " + " ".join(rendered)

    children = list(node.iter(include_text=True))
    if not children:
        if tag in _VOID_TAGS:
            out.append(f"{pad}<{head}/>")
        else:
            out.append(f"{pad}<{head}></{tag}>")
        return

    out.append(f"{pad}<{head}>")
    for child in children:
        _render(child, out, depth + 1)
    out.append(f"{pad}</{tag}>")


def _self_check(skeleton: str) -> None:
    """Проверяет, что в скелете не осталось человекочитаемого текста.

    Самопроверка нужна, потому что скелет строится обходом дерева, а дерево
    приходит от стороннего парсера: изменение его поведения не должно тихо
    превратить безопасный формат в небезопасный.

    Args:
        skeleton (str): Готовый скелет.

    Raises:
        SkeletonError: Если найдена последовательность, похожая на текст.
    """
    for line in skeleton.splitlines():
        body = line.strip()
        if _SIGNATURE_RE.match(body):
            continue
        if not body.startswith("<"):
            raise SkeletonError(f"строка не является ни подписью, ни тегом: {body[:60]}")
        if any("Ѐ" <= ch <= "ӿ" for ch in body):
            raise SkeletonError(f"кириллица в структурной строке: {body[:60]}")


def skeletonize(html: str) -> str:
    """Строит структурный скелет страницы.

    Args:
        html (str): Исходный HTML страницы.

    Returns:
        str: Скелет: дерево тегов с подписями текстовых узлов вместо текста.

    Raises:
        SkeletonError: Если разбор не удался или самопроверка нашла текст.
    """
    if not html or not html.strip():
        raise SkeletonError("пустой документ")

    try:
        tree = HTMLParser(html)
    except Exception as exc:
        raise SkeletonError("документ не разбирается как HTML") from exc

    root = tree.root
    if root is None:
        raise SkeletonError("документ не содержит корневого узла")

    out: list[str] = []
    _render(root, out, 0)
    skeleton = "\n".join(out) + "\n"
    _self_check(skeleton)
    return skeleton
