"""Сверка селекторов из спецификации с настоящими снимками страниц.

Проверка замыкает круг между двумя репозиториями. Funora-spec объявляет правила
извлечения и помечает часть из них как наблюдённые, но проверить наблюдение сам
не может: снимки лежат здесь, и разбирать их нечем. Здесь наоборот - есть и
снимки, и разборщик, но нет источника истины о том, какие селекторы обещаны.

Поэтому спецификация выкладывает перечень наблюдённых селекторов машинным
файлом, а этот набор проверяет каждую запись буквально. Придуманный селектор
хуже отсутствующего: отсутствующий виден сразу, придуманный тихо ломает разбор у
всех шести реализаций и обнаруживается уже в работе.

Проверка выполняется, только если задана переменная окружения FUNORA_SPEC_DIR с
путём к рабочей копии Funora-spec. Без неё набор пропускается: обязательная
зависимость от соседнего репозитория сделала бы невозможной обычную работу над
этим.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Путь к перечню наблюдённых селекторов внутри рабочей копии спецификации.
INVENTORY = Path("spec") / "extraction" / "observed-selectors.json"


def _spec_dir() -> Path | None:
    """Возвращает путь к рабочей копии спецификации, если он задан.

    Returns:
        Path | None: Каталог репозитория Funora-spec либо None, если переменная
        окружения FUNORA_SPEC_DIR не задана или указывает не туда.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / INVENTORY).is_file() else None


def _load_inventory() -> list[dict[str, object]]:
    """Читает перечень наблюдённых селекторов из спецификации.

    Returns:
        list[dict[str, object]]: Записи перечня. Пустой список, если
        спецификация недоступна.
    """
    root = _spec_dir()
    if root is None:
        return []
    return json.loads((root / INVENTORY).read_text(encoding="utf-8"))


#: Перечень, прочитанный один раз на весь набор.
ENTRIES = _load_inventory()

#: Причина пропуска, общая для всех проверок набора.
SKIP_REASON = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(not ENTRIES, reason=SKIP_REASON)


def _read(name: str) -> str:
    """Читает снимок страницы по имени.

    Args:
        name (str): Имя снимка без расширения, например ``chat.logged.ru``.

    Returns:
        str: Содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _ids() -> list[str]:
    """Строит понятные имена случаев для отчёта pytest.

    Returns:
        list[str]: Имена вида ``селектор -> снимок``.
    """
    return [f"{e['selector']} -> {','.join(e['evidence'])}" for e in ENTRIES]  # type: ignore[index]


@pytest.mark.parametrize("entry", ENTRIES, ids=_ids())
def test_declared_selector_exists_in_fixture(entry: dict[str, object]) -> None:
    """Проверяет, что объявленный селектор находится хотя бы в одном снимке.

    Args:
        entry (dict[str, object]): Запись перечня: селектор, список снимков и
            место в спецификации, откуда она взялась.

    Returns:
        None
    """
    selector = str(entry["selector"])
    evidence = [str(x) for x in entry["evidence"]]  # type: ignore[union-attr]
    where = str(entry["where"])

    missing: list[str] = []
    found_in: list[str] = []

    for name in evidence:
        path = FIXTURES / f"{name}.skeleton.txt"
        if not path.is_file():
            missing.append(name)
            continue
        if HTMLParser(_read(name)).css_first(selector) is not None:
            found_in.append(name)

    assert not missing, f"{where}: снимков нет в репозитории: {', '.join(missing)}"
    assert found_in, (
        f"{where}: селектор {selector!r} помечен наблюдённым, но не найден "
        f"ни в одном из снимков: {', '.join(evidence)}"
    )


def test_inventory_is_not_empty() -> None:
    """Проверяет, что перечень действительно прочитан.

    Проверка нужна затем, что пустой перечень сделал бы весь набор зелёным, ничего
    при этом не проверив. Молчаливо проходящий набор хуже отсутствующего.

    Returns:
        None
    """
    # Порог держится близко к настоящему числу намеренно. Прежний «не меньше
    # двадцати» пропускал урезание перечня вдвое: двадцать одна запись из сорока
    # одной давала зелёный набор, читавшийся как «спецификация сверена».
    assert len(ENTRIES) >= 38, (
        f"в перечне {len(ENTRIES)} записей, это подозрительно мало: "
        "вероятно, спецификация прочитана не полностью"
    )


#: Записи перечня, объявившие число наблюдений.
COUNTED = [e for e in ENTRIES if "count_observed" in e]


def _count_ids() -> list[str]:
    """Строит понятные имена для проверок счётчиков.

    Returns:
        list[str]: Имена в виде «селектор x число».
    """
    return [f"{e['selector']} x{e['count_observed']}" for e in COUNTED]  # type: ignore[index]


@pytest.mark.parametrize("entry", COUNTED, ids=_count_ids())
def test_declared_count_matches_the_fixture(entry: dict[str, object]) -> None:
    """Проверяет, что объявленное число наблюдений совпадает со снимком.

    Числа в спецификации протухли молча дважды. Снимки пересняли, диалогов стало
    пятьдесят вместо сорока семи, сообщений одиннадцать вместо десяти - а
    count_observed остался прежним, и сверить его было нечем: перечень
    селекторов чисел не носил, а validate.js слова count_observed не знал.

    Само по себе устаревшее число вреда не наносит. Вредит то, чем оно
    становится: на числе 47 выросло утверждение «список ограничен сорока семью»,
    из него - формулировка гарантии доставки, и всё это про предел, которого
    никто не наблюдал.

    Args:
        entry (dict[str, object]): Запись перечня со счётчиком и областью.

    Returns:
        None
    """
    selector = str(entry["selector"])
    declared = int(entry["count_observed"])  # type: ignore[arg-type]
    scope = str(entry.get("scope", "document"))
    where = str(entry["where"])
    evidence = [str(x) for x in entry["evidence"]]  # type: ignore[union-attr]

    if scope != "document":
        pytest.skip("счётчик в области строки: сверяется разбором, а не по документу")

    seen = {name: len(HTMLParser(_read(name)).css(selector)) for name in evidence}
    assert declared in seen.values(), (
        f"{where}: объявлено {declared} вхождений {selector!r}, "
        f"а в снимках {seen}. Число в спецификации устарело"
    )
