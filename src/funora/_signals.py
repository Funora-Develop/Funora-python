"""Сравнение изменяемых значений страницы между двумя чтениями.

Зачем понадобился отдельный модуль. Структурный скелет намеренно прячет значения
атрибутов: это то, что делает его безопасным. Но ровно из-за этого по скелетам
нельзя ответить на главный вопрос спецификации - отдаёт ли площадка устойчивую
позицию, по которой можно переиграть пропущенные события. Монотонный счётчик и
хеш состояния в скелете выглядят одинаково, потому что различает их не форма, а
поведение во времени.

Как это решено. Значения извлекаются, сравниваются в памяти и уничтожаются
вместе с процессом. На диск не попадает ничего. Наружу выходит только характер
изменения - выросло, уменьшилось, изменилось, не изменилось - и величина шага
для числовых значений. Гарантия скелета при этом не ослабляется, потому что сам
формат скелета не меняется.

Белый список атрибутов задан явно. Смысл списка не в защите - защита в том, что
значения не сохраняются, - а в том, чтобы сравнение не превращалось в обход всей
страницы: сравнивать нужно кандидатов в позицию канала обновлений, а не разметку.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import blake2s
from typing import Final

from selectolax.parser import HTMLParser, Node

__all__ = [
    "ATTR_ALLOWLIST",
    "Change",
    "ChangeKind",
    "collect",
    "compare",
    "format_report",
    "Relations",
    "relations",
    "format_relations",
]

#: Атрибуты, значения которых сравниваются между чтениями.
#:
#: Список выведен из снимков от 18.08.2026 и обоснован в docs/observations.md.
#: Это кандидаты в позицию канала обновлений и идентификаторы, по которым
#: элементы опознаются между чтениями.
ATTR_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "data-app-data",
        "data-chat",
        "data-orders",
        "data-bookmarks-tag",
        "data-user",
        "data-id",
        "data-node-msg",
        "data-user-msg",
    }
)

#: Длина короткого отпечатка, которым элемент опознаётся в отчёте.
_FINGERPRINT_LEN: Final[int] = 6


class ChangeKind(StrEnum):
    """Характер изменения значения между двумя чтениями."""

    UNCHANGED = "не изменилось"
    GREW = "выросло"
    SHRANK = "уменьшилось"
    CHANGED = "изменилось"
    APPEARED = "появилось"
    DISAPPEARED = "исчезло"


@dataclass(frozen=True, slots=True)
class Change:
    """Изменение одного значения.

    Args:
        key (str): Обезличенный ключ элемента: тег, класс и отпечаток.
        attr (str): Имя атрибута.
        kind (ChangeKind): Характер изменения.
        delta (int | None): Величина шага для числовых значений. Для нечисловых
            и для отсутствующих значений равна None.
        numeric (bool): Было ли значение полностью числовым.
    """

    key: str
    attr: str
    kind: ChangeKind
    delta: int | None = None
    numeric: bool = False


def _fingerprint(value: str) -> str:
    """Строит короткий необратимый отпечаток значения.

    Отпечаток нужен, чтобы опознать один и тот же элемент в двух чтениях после
    того, как список переупорядочился. Позиция в списке для этого не годится:
    пришедшее сообщение поднимает диалог наверх, и сравнение по индексу
    сопоставило бы разные диалоги.

    Args:
        value (str): Исходное значение.

    Returns:
        str: Шестнадцатеричный отпечаток фиксированной длины.
    """
    return blake2s(value.encode("utf-8"), digest_size=8).hexdigest()[:_FINGERPRINT_LEN]


def _element_key(node: Node, index: int) -> str:
    """Строит обезличенный ключ элемента.

    Args:
        node (Node): Узел документа.
        index (int): Порядковый номер элемента среди однотипных. Используется
            только если у элемента нет собственного идентификатора.

    Returns:
        str: Ключ вида ``a.contact-item#1f4c9e``, пригодный для отчёта.
    """
    attrs = node.attributes or {}
    tag = node.tag or "?"
    classes = (attrs.get("class") or "").split()
    label = tag + ("." + classes[0] if classes else "")
    own = attrs.get("data-id") or attrs.get("id")
    mark = _fingerprint(own) if own else "i" + str(index)
    return label + "#" + mark


def collect(html: str, attrs: frozenset[str] = ATTR_ALLOWLIST) -> dict[tuple[str, str], str]:
    """Извлекает значения атрибутов из белого списка.

    Результат существует только в памяти вызывающего кода. Записывать его на
    диск нельзя: в нём настоящие значения со страницы под авторизацией.

    Args:
        html (str): Тело ответа.
        attrs (frozenset[str]): Имена атрибутов, значения которых собираются.

    Returns:
        dict[tuple[str, str], str]: Отображение пары «ключ элемента, атрибут» в
        значение.
    """
    tree = HTMLParser(html)
    found: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}

    for node in tree.css("*"):
        node_attrs = node.attributes or {}
        present = [name for name in node_attrs if name in attrs]
        if not present:
            continue
        tag = node.tag or "?"
        counters[tag] = counters.get(tag, 0) + 1
        key = _element_key(node, counters[tag])
        for name in present:
            value = node_attrs[name]
            if value is None:
                continue
            found[(key, name)] = value
    return found


def compare(
    before: dict[tuple[str, str], str],
    after: dict[tuple[str, str], str],
) -> list[Change]:
    """Сравнивает два набора значений.

    Args:
        before (dict[tuple[str, str], str]): Значения первого чтения.
        after (dict[tuple[str, str], str]): Значения второго чтения.

    Returns:
        list[Change]: Изменения, упорядоченные по ключу и имени атрибута.
    """
    changes: list[Change] = []
    for slot in sorted(set(before) | set(after)):
        key, attr = slot
        old = before.get(slot)
        new = after.get(slot)

        if old is None:
            changes.append(Change(key=key, attr=attr, kind=ChangeKind.APPEARED))
            continue
        if new is None:
            changes.append(Change(key=key, attr=attr, kind=ChangeKind.DISAPPEARED))
            continue
        if old == new:
            changes.append(Change(key=key, attr=attr, kind=ChangeKind.UNCHANGED))
            continue

        if old.isdigit() and new.isdigit():
            delta = int(new) - int(old)
            kind = ChangeKind.GREW if delta > 0 else ChangeKind.SHRANK
            changes.append(Change(key=key, attr=attr, kind=kind, delta=abs(delta), numeric=True))
            continue

        changes.append(Change(key=key, attr=attr, kind=ChangeKind.CHANGED))
    return changes


def format_report(changes: list[Change], *, only_changed: bool = True) -> str:
    """Готовит отчёт о сравнении.

    В отчёте нет значений: только характер изменения и величина шага для
    числовых. Отчёт можно показывать и прикладывать к обсуждению.

    Args:
        changes (list[Change]): Изменения.
        only_changed (bool): Показывать только изменившееся. Неизменившееся
            сводится в итоговую строку.

    Returns:
        str: Готовый текст отчёта.
    """
    shown = [c for c in changes if not only_changed or c.kind is not ChangeKind.UNCHANGED]
    stable = sum(1 for c in changes if c.kind is ChangeKind.UNCHANGED)

    lines: list[str] = []
    for change in sorted(shown, key=lambda x: (x.attr, x.key)):
        tail = "" if change.delta is None else " на " + str(change.delta)
        lines.append("  " + change.attr.ljust(20) + change.key.ljust(26) + str(change.kind) + tail)

    if not lines:
        lines.append("  изменений нет")
    lines.append("")
    lines.append("  всего значений: " + str(len(changes)) + ", без изменений: " + str(stable))
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Relations:
    """Соотношения между значениями внутри одного снимка.

    Наружу выходят только количества. Ни одно значение атрибута в результат не
    попадает, поэтому режим не ослабляет защиту, ради которой скелет прячет
    значения.

    Args:
        contacts (int): Сколько элементов диалога найдено.
        equal (int): У скольких data-node-msg совпадает с data-user-msg.
        differing (int): У скольких значения различаются.
        incomplete (int): У скольких одного из атрибутов нет.
        unread_badge (str): Состояние счётчика непрочитанного в шапке:
            ``"скрыт"``, ``"виден"`` либо ``"не найден"``.
    """

    contacts: int
    equal: int
    differing: int
    incomplete: int
    unread_badge: str


def relations(html: str) -> Relations:
    """Считает соотношения пары позиций по всем диалогам снимка.

    Режим отвечает на вопрос, который сравнением двух чтений не решается.
    data-user-msg может означать «последнее прочитанное этим аккаунтом» либо
    «последнее написанное этим аккаунтом», и своя отправка двигает поле при
    обеих трактовках. Но по списку целиком версии расходятся: если счётчик
    непрочитанного пуст, а значения расходятся у многих диалогов, то поле не
    может быть отметкой прочтения.

    Args:
        html (str): Тело страницы списка диалогов.

    Returns:
        Relations: Количества и состояние счётчика непрочитанного.
    """
    tree = HTMLParser(html)

    equal = differing = incomplete = 0
    contacts = tree.css("a.contact-item")
    for node in contacts:
        attrs = node.attributes or {}
        node_msg = attrs.get("data-node-msg")
        user_msg = attrs.get("data-user-msg")
        if not node_msg or not user_msg:
            incomplete += 1
        elif node_msg == user_msg:
            equal += 1
        else:
            differing += 1

    badge = tree.css_first("span.badge-chat")
    if badge is None:
        state = "не найден"
    else:
        state = "скрыт" if "hidden" in (badge.attributes.get("class") or "") else "виден"

    return Relations(
        contacts=len(contacts),
        equal=equal,
        differing=differing,
        incomplete=incomplete,
        unread_badge=state,
    )


def format_relations(rel: Relations) -> str:
    """Составляет отчёт о соотношениях и о том, что из них следует.

    Толкование печатается рядом с числами намеренно. Без него отчёт выглядит
    набором цифр, а решение по нему всё равно придётся принимать, и принято оно
    будет по памяти.

    Args:
        rel (Relations): Посчитанные соотношения.

    Returns:
        str: Готовый к печати отчёт.
    """
    lines = [
        f"  диалогов:                  {rel.contacts}",
        f"  позиции совпадают:         {rel.equal}",
        f"  позиции различаются:       {rel.differing}",
        f"  атрибут отсутствует:       {rel.incomplete}",
        f"  счётчик непрочитанного:    {rel.unread_badge}",
        "",
    ]

    if rel.contacts == 0:
        lines.append("  диалогов не найдено, толковать нечего")
        return "\n".join(lines)

    if rel.unread_badge == "скрыт" and rel.differing > 0:
        lines += [
            f"  Непрочитанного нет, а позиции расходятся у {rel.differing} диалогов.",
            "  Значит data-user-msg не отметка прочтения: будь она отметкой,",
            "  при пустом счётчике расхождений не было бы ни одного.",
            "  Остаётся трактовка «последнее написанное этим аккаунтом», и",
            "  признак непрочитанного придётся искать иначе.",
        ]
    elif rel.unread_badge == "скрыт" and rel.differing == 0:
        lines += [
            "  Непрочитанного нет, и позиции совпадают у всех диалогов.",
            "  Это отвечает трактовке «последнее прочитанное». Обратная",
            "  трактовка потребовала бы, чтобы последнее сообщение во всех",
            f"  {rel.contacts} диалогах было написано вами, что неправдоподобно.",
        ]
    elif rel.unread_badge == "виден":
        lines += [
            "  Непрочитанное есть. Число расхождений имеет смысл сравнить с",
            "  числом непрочитанных диалогов в интерфейсе: совпало - отметка",
            "  прочтения, расхождений заметно больше - последнее написанное.",
        ]
    else:
        lines.append("  счётчик непрочитанного не найден, вывод сделать нельзя")

    return "\n".join(lines)
