"""Сверка: каждый файл спецификации чем-то связан с реализацией.

Набор появился после того, как разбор посчитал: генератор читает семь файлов из
сорока девяти. Проверка свежести покрывает эти семь. Правка любого из остальных
сорока двух - а равно правка непрочитанного раздела внутри самих семи - не
давала ни одного признака ни в одной сборке.

Расхождение при этом обнаруживается не сразу и не здесь: спецификация зелёная,
сборка зелёная, а второй SDK, написанный по правленому файлу, ведёт себя иначе
первого. Обнаружит это тот, кто подключит обе реализации.

Реестр spec/conformance/coverage.yaml объявляет для каждого файла один из трёх
механизмов - порождение, проверка, обоснование, - а этот набор сверяет реестр с
действительностью: с составом каталога, с перечнем источников генератора и с
существованием названных проверок.

Слабое место такого реестра известно заранее: запись «verified» с названием
несуществующего набора выглядит как связь, не будучи ею. Поэтому существование
названного проверяется отдельно.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

#: Корень репозитория с реализацией.
ROOT = Path(__file__).resolve().parent.parent


def _spec_dir() -> Path | None:
    """Находит рабочую копию спецификации, если она задана.

    Returns:
        Path | None: Каталог репозитория Funora-spec либо None.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / "spec" / "conformance" / "coverage.yaml").is_file() else None


#: Причина пропуска, общая для набора.
SKIP_REASON = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(_spec_dir() is None, reason=SKIP_REASON)


def _registry() -> dict[str, dict[str, Any]]:
    """Читает реестр покрытия.

    Returns:
        dict[str, dict[str, Any]]: Записи по пути файла.
    """
    import yaml

    root = _spec_dir()
    assert root is not None
    doc = yaml.safe_load(
        (root / "spec" / "conformance" / "coverage.yaml").read_text(encoding="utf-8")
    )
    files: dict[str, dict[str, Any]] = doc["files"]
    return files


def _spec_files() -> set[str]:
    """Собирает состав спецификации.

    Returns:
        set[str]: Пути файлов относительно корня репозитория спецификации.
    """
    root = _spec_dir()
    assert root is not None
    return {
        str(path.relative_to(root)).replace("\\", "/")
        for path in (root / "spec").rglob("*")
        if path.is_file()
    }


def test_every_spec_file_declares_its_mechanism() -> None:
    """Проверяет, что ни один файл спецификации не остался вне учёта.

    Молчащий файл спецификации - это обещание, которое никто не держит, и
    заметить его нечем: он просто существует.

    Returns:
        None
    """
    registry = _registry()
    files = _spec_files()

    forgotten = sorted(files - set(registry))
    assert not forgotten, (
        f"файлы спецификации вне реестра покрытия: {forgotten}. Объявите механизм "
        "в spec/conformance/coverage.yaml - порождение, проверку либо обоснование"
    )

    ghosts = sorted(set(registry) - files)
    assert not ghosts, (
        f"реестр покрытия называет несуществующие файлы: {ghosts}. Запись, "
        "пережившая свой файл, описывает связь, которой нет"
    )


def test_generated_entries_match_what_codegen_reads() -> None:
    """Сверяет реестр с перечнем источников генератора.

    Расхождение здесь означает одно из двух: либо реестр обещает порождение,
    которого нет, либо генератор читает файл, о котором реестр не знает. Оба
    случая делают покрытие мнимым.

    Returns:
        None
    """
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    from codegen import SOURCES  # type: ignore[import-not-found]

    declared = {path for path, item in _registry().items() if item["mechanism"] == "generated"}
    assert declared == set(SOURCES), (
        f"реестр объявляет порождаемыми {sorted(declared - set(SOURCES))}, "
        f"а генератор читает {sorted(set(SOURCES) - declared)}"
    )


def test_verified_entries_name_something_that_exists() -> None:
    """Проверяет, что названная проверка вправду существует.

    Слабое место реестра: запись «verified» с названием несуществующего набора
    выглядит как связь, не будучи ею, - и выглядит убедительнее прочерка.

    Returns:
        None
    """
    spec_root = _spec_dir()
    assert spec_root is not None

    missing: list[str] = []
    for path, item in _registry().items():
        if item["mechanism"] != "verified":
            continue
        note = str(item.get("note") or "")
        named = re.findall(r"tests/[a-z_]+\.py", note) + re.findall(r"scripts/[a-z]+\.js", note)
        assert named, f"{path}: механизм verified, а проверка не названа"
        for name in named:
            base = ROOT if name.startswith("tests/") else spec_root
            if not (base / name).is_file():
                missing.append(f"{path} -> {name}")

    assert not missing, (
        f"реестр ссылается на несуществующие проверки: {missing}. Запись, "
        "называющая несуществующий набор, выглядит связью убедительнее прочерка"
    )


def test_prose_entries_justify_themselves() -> None:
    """Проверяет, что обоснование объясняет, почему проверять нечего.

    Корзина «обоснование» - единственная, куда можно сложить что угодно, и
    потому единственная, которую надо стеречь. Запись без объяснения означает
    «мы не придумали, как это проверить», а выглядит как «проверять нечего».

    Returns:
        None
    """
    for path, item in _registry().items():
        if item["mechanism"] != "prose":
            continue
        note = str(item.get("note") or "").strip()
        assert len(note) > 60, (
            f"{path}: обоснование короче шестидесяти знаков. Объясните, почему "
            "проверять нечего ПО УСТРОЙСТВУ, а не по трудности"
        )


def test_adapter_family_matches_the_spec() -> None:
    """Сверяет семейство адаптера с объявленным в spec/version.yaml.

    Состояние, снятое с другой площадки, бессмысленно здесь целиком: совпадение
    идентификаторов было бы случайным, а последствия - молчаливым гашением чужих
    событий. Имя семейства жило рукописной константой и с контрактом не
    сверялось.

    Returns:
        None
    """
    import yaml

    from funora._state import ADAPTER_FAMILY

    root = _spec_dir()
    assert root is not None
    version = yaml.safe_load((root / "spec" / "version.yaml").read_text(encoding="utf-8"))

    assert version["adapter_family"] == ADAPTER_FAMILY, (
        f"реализация объявляет семейство {ADAPTER_FAMILY!r}, спецификация - "
        f"{version['adapter_family']!r}"
    )


def test_supported_locales_match_the_spec() -> None:
    """Сверяет перечень локалей с объявленным спецификацией.

    Локаль привязана к аккаунту, а не к адресу, и переключить её запросом
    нельзя. Реализация обязана знать, для каких локалей у неё есть снимки: при
    локали вне перечня она обязана вернуть типизированную ошибку, но никогда -
    пустой результат.

    Returns:
        None
    """
    import yaml

    root = _spec_dir()
    assert root is not None
    version = yaml.safe_load((root / "spec" / "version.yaml").read_text(encoding="utf-8"))

    fixtures = ROOT / "tests" / "fixtures" / "pages"
    # Имя снимка: страница.состояние.локаль.skeleton.txt - локаль предпоследняя
    # в части ДО расширения, а не в имени файла целиком.
    captured = {
        path.name.removesuffix(".skeleton.txt").rsplit(".", 1)[-1]
        for path in fixtures.glob("*.skeleton.txt")
    }
    declared = set(version["supported_locales"])

    assert captured <= declared, (
        f"есть снимки для локалей {sorted(captured - declared)}, а спецификация "
        "их не объявляет поддерживаемыми"
    )
