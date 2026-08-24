"""Проверка скелета на утечки перед переносом в фикстуры.

Скелет снимается с авторизованной страницы, а фикстуры лежат в открытом
репозитории. Между этими двумя фактами стоит одна проверка, и до сих пор она
была человеческой: «прочесть глазами». Четыре тысячи строк глазами не читаются,
и такая проверка означает «не проверено».

ЧТО ИЩЕТСЯ. Ровно одно: значение, которое НЕ заменено подписью и НЕ является
безобидной разметкой. Подпись имеет вид T{длина}:{состав}[#{номер}] - она
говорит, сколько было знаков и каких классов, но не какие именно.

ЧЕГО ЭТА ПРОВЕРКА НЕ ДЕЛАЕТ. Она не решает, утечка перед ней или нет. Она
сокращает четыре тысячи строк до десятка неповторяющихся значений, которые
человек прочтёт за минуту. Решение остаётся за человеком - и это намеренно:
список безобидного здесь неполон по определению, новая страница вправе принести
незнакомый вид значения.

Запуск:

    .venv\\Scripts\\python.exe tools/check_skeleton.py observations/имя.skeleton.txt
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Final

#: Подпись, которой сборщик заменяет текст: длина и состав знаков.
_SIGNATURE: Final[re.Pattern[str]] = re.compile(r"^T\d+:[adcpso]+(#\d+)?$")

#: Обезличенный кусок адреса: {n7}, {q12}, {t}.
_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"^\{[a-z]\d*\}$")

#: Атрибуты, значения которых сборщик хранит дословно НАМЕРЕННО: они говорят о
#: разметке, а не о человеке. Их дословность - условие разбора: по классу
#: находят узел, по имени поля собирают запрос.
_VERBATIM: Final[frozenset[str]] = frozenset({"class", "name", "type", "lang", "hreflang", "id"})

#: Схемы адресов, которые ничего не сообщают о владельце страницы.
_PUBLIC_HOSTS: Final[tuple[str, ...]] = (
    "https://funpay.com/",
    "https://sfunpay.com/",
    "https://support.funpay.com/",
    "https://mc.yandex.ru/",
    "/",
)


def _is_masked_url(value: str) -> bool:
    """Адрес безопасен, если все его опознавательные куски обезличены.

    Аргументы:
        value (str): значение атрибута href, src или action.

    Возвращает:
        bool: True, если в адресе не осталось ни цифрового сегмента, ни строки
            запроса, ни имени файла с идентификатором.
    """
    if not value.startswith(_PUBLIC_HOSTS):
        return False
    tail = value.split("?", 1)
    if len(tail) == 2 and not _PLACEHOLDER.match(tail[1]):
        return False
    # Сегмент из одних цифр - неподставленный идентификатор.
    return not any(part.isdigit() for part in tail[0].split("/"))


def _scan(text: str) -> tuple[Counter[str], Counter[str]]:
    """Собирает всё, что не является подписью.

    Аргументы:
        text (str): содержимое файла скелета.

    Возвращает:
        tuple[Counter[str], Counter[str]]: неподписанный текст узлов и
            неподписанные значения атрибутов, каждое со счётчиком повторов.
    """
    leaked_text: Counter[str] = Counter()
    for match in re.finditer(r">([^<>]+)<", text):
        value = match.group(1).strip()
        if value and not _SIGNATURE.match(value):
            leaked_text[value] += 1

    leaked_attrs: Counter[str] = Counter()
    for match in re.finditer(r'([\w-]+)="([^"]*)"', text):
        name, value = match.groups()
        if not value or name in _VERBATIM or _SIGNATURE.match(value):
            continue
        if _PLACEHOLDER.match(value) or _is_masked_url(value):
            continue
        leaked_attrs[f"{name}={value}"] += 1

    return leaked_text, leaked_attrs


def main(argv: list[str]) -> int:
    """Печатает всё непомаскированное и возвращает код выхода.

    Аргументы:
        argv (list[str]): пути к файлам скелетов.

    Возвращает:
        int: 0, если ничего непомаскированного не нашлось; иначе 1 - и это НЕ
            приговор, а список для чтения глазами.
    """
    if not argv:
        print(__doc__)
        return 2

    dirty = 0
    for name in argv:
        path = Path(name)
        if not path.is_file():
            print(f"нет файла: {path}")
            return 2

        text = path.read_text(encoding="utf-8")
        leaked_text, leaked_attrs = _scan(text)
        print(f"=== {path.name} ({text.count(chr(10)) + 1} строк)")

        if not leaked_text and not leaked_attrs:
            print("    непомаскированного не найдено")
            continue

        dirty += 1
        if leaked_text:
            print(f"    текст узлов, {len(leaked_text)} различных:")
            for value, count in leaked_text.most_common():
                print(f"      {count:4} | {value[:120]}")
        if leaked_attrs:
            print(f"    значения атрибутов, {len(leaked_attrs)} различных:")
            for value, count in leaked_attrs.most_common():
                print(f"      {count:4} | {value[:120]}")

    if dirty:
        print()
        print("ПРОЧТИ СПИСОК ВЫШЕ. Если там нет ничего, что читается как имя,")
        print("сумма, адрес или переписка, - снимок можно переносить в фикстуры.")
        print()
        print("Ненулевой код выхода здесь значит «есть что прочесть», а не")
        print("«найдена утечка». На всех семи нынешних снимках остаток - ссылки")
        print("подвала площадки: они одинаковы на каждой странице и ничего о")
        print("владельце не сообщают.")
    return 1 if dirty else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
