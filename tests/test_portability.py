"""Проверки переносимости: пакет обязан работать у того, кто его скачал.

Набор появился после прямого вопроса: точно ли это заработает у всех, кто
поставит пакет. Вопрос правильный, и ответ на него должен давать не человек, а
проверка.

Опасность здесь тихая. Абсолютный путь, попавший в исходник, работает у автора и
только у автора; у остальных он даёт FileNotFoundError - но не сразу, а на той
ветке, куда впервые дошли. То же с разделителем каталогов: обратная косая
проходит на Windows и ломается везде ещё.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Корень репозитория.
ROOT = Path(__file__).resolve().parent.parent

#: Что попадает пользователю.
PACKAGE = ROOT / "src" / "funora"

#: Признаки абсолютного пути, привязанного к машине автора.
#:
#: Ищутся именно в исходниках пакета. В проверках и в инструментах сборки такие
#: пути тоже нежелательны, но там они ломают только сборку, а здесь - работу у
#: пользователя.
#:
#: Сетевые пути UNC не ищутся намеренно: две обратные косые встречаются в
#: docstring всякий раз, когда речь идёт об экранировании, и проверка ловила бы
#: рассуждение о подделке хоста вместо настоящего пути.
_ABSOLUTE = re.compile(
    r"""
    (?<![A-Za-z])[A-Za-z]:[\/]   # буква диска: C:\ либо D:/
                                   # отрицательный просмотр назад отсекает схемы
                                   # адресов: в https:// перед двоеточием стоит
                                   # буква, а перед буквой диска - нет
    | /home/[^/\s"]             # домашний каталог Linux
    | /Users/[^/\s"]            # домашний каталог macOS
    """,
    re.VERBOSE,
)


def _sources() -> list[Path]:
    """Собирает исходники пакета.

    Returns:
        list[Path]: Файлы, попадающие пользователю.
    """
    return sorted(PACKAGE.rglob("*.py"))


def test_no_absolute_paths_in_the_package() -> None:
    """Проверяет, что в пакете нет путей с машины автора.

    Такой путь работает у автора и только у него. У остальных он даёт отказ - и
    не сразу, а на той ветке, куда впервые дошли, то есть в работе.

    Returns:
        None
    """
    found: list[str] = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _ABSOLUTE.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}")

    assert not found, f"абсолютные пути в пакете: {found}"


def test_package_does_not_read_the_filesystem_by_itself() -> None:
    """Проверяет, что пакет не ищет свои файлы сам.

    Модуль, читающий файл рядом с собой, работает из распакованного дерева и
    ломается из архива либо из замороженной сборки. Пакет обязан обходиться тем,
    что ему передали: путь к состоянию приходит аргументом, спецификация
    превращена в порождённые модули ещё при сборке.

    Исключение одно - файл состояния, и путь к нему даёт вызывающий.

    Returns:
        None
    """
    marks = ("Path(__file__)", "os.getcwd(", "Path.cwd(", "__file__)")
    allowed = {"_state.py"}

    found: list[str] = []
    for path in _sources():
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for mark in marks:
            if mark in text:
                found.append(f"{path.relative_to(ROOT)}: {mark}")

    assert not found, (
        f"пакет ищет файлы сам: {found}. Такой модуль работает из распакованного "
        "дерева и ломается из архива"
    )


def test_wheel_carries_only_the_package() -> None:
    """Проверяет состав собранного колеса.

    Проверки, инструменты сборки и черновики пользователю не нужны и не должны
    попадать: они тянут за собой зависимости, которых у него нет.

    Собирать колесо здесь незачем - проверяется объявление сборки. Оно короче
    колеса и меняется реже, а несоответствие между ними поймает сама сборка.

    Returns:
        None
    """
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages == ["src/funora"], (
        f"в колесо объявлено к упаковке {packages}; всё, кроме src/funora, "
        "пользователю не нужно и тянет чужие зависимости"
    )


def test_runtime_dependencies_are_few_and_named() -> None:
    """Проверяет, что пакет не тянет лишнего.

    Каждая зависимость времени работы - это чужой код у пользователя и чужой
    график обновлений. Инструменты сборки и проверок сюда не входят: они живут в
    дополнительной группе.

    Returns:
        None
    """
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = config["project"]["dependencies"]

    assert len(runtime) <= 3, f"зависимостей времени работы стало {len(runtime)}: {runtime}"
    names = {item.split(">")[0].split("=")[0].split("[")[0].strip() for item in runtime}
    assert names == {"httpx", "selectolax"}, f"состав зависимостей изменился: {sorted(names)}"
