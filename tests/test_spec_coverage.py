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


def test_the_checker_understands_every_schema() -> None:
    """Проверяет, что сверка понимает каждую схему целиком.

    Сверка отказывается работать со схемой, которую не понимает: неизвестное
    ключевое слово - ошибка, а не пропуск. Свойство это ценно ровно настолько,
    насколько его применяют: схема, которую никто не сверял, может годами
    содержать слово, о котором сверка не знает, - и выяснится это в тот день,
    когда по схеме начнут проверять.

    Проверка обходит ВСЕ схемы событий и моделей, включая те, которых ни одна
    реализация не собирает.

    Returns:
        None
    """
    import json

    from _schema_check import UnsupportedKeyword, check

    root = _spec_dir()
    assert root is not None

    schemas = sorted((root / "spec").rglob("*.schema.json"))
    assert len(schemas) > 20, "схем не набралось - проверять нечего"

    for path in schemas:
        doc = json.loads(path.read_text(encoding="utf-8"))
        try:
            # Значение заведомо не подойдёт - проверяется не оно, а понятность
            # самой схемы. SchemaError означает «схема понята, значение не
            # подошло»; UnsupportedKeyword - «схема не понята».
            check({}, doc, where=path.name)
        except UnsupportedKeyword as exc:
            raise AssertionError(f"{path.name}: {exc}") from exc
        except AssertionError:
            continue


def test_domain_types_come_from_the_spec() -> None:
    """Проверяет, что словарь доменных типов не переписан копией.

    Копия разошлась бы со spec/types.yaml молча, и сверка начала бы отвергать
    тип, который спецификация объявила, либо принимать тот, которого она не
    знает.

    Returns:
        None
    """
    import yaml
    from _schema_check import KNOWN_TYPES

    root = _spec_dir()
    assert root is not None
    doc = yaml.safe_load((root / "spec" / "types.yaml").read_text(encoding="utf-8"))

    assert set(doc["types"]) == KNOWN_TYPES, (
        f"сверка знает {sorted(KNOWN_TYPES)}, спецификация объявляет {sorted(doc['types'])}"
    )


#: Какой метод отвечает за какую операцию.
#:
#: Соответствие не механическое: chats.history выполняется методом thread, имена
#: разные. Поэтому таблица рукописная - а рукописная таблица устаревает молча,
#: и её стережёт проверка ниже: множество ключей обязано совпадать с множеством
#: выполняемых операций.
OPERATION_METHOD: dict[str, tuple[str, str]] = {
    "chats.list": ("ChatsService", "list"),
    "chats.history": ("ChatsService", "thread"),
    "orders.list": ("OrdersService", "list"),
}


def test_declared_return_type_matches_what_is_returned() -> None:
    """Сверяет объявленный тип результата с настоящим.

    Поле returns порождалось в таблицу операций и не читалось никем: расхождение
    между «что обещано» и «что возвращается» не давало ни одного признака.

    Расхождение было, и вдвойне вредное. Спецификация объявляла у chats.list и
    orders.list ГОЛЫЙ МАССИВ записей, а возвращается страница. Голый список
    делает неполноту незаметной: вызывающий берёт его и не спрашивает, всё ли
    прочитано, а неполный список неотличим от полного - то есть спецификация
    предписывала ровно то, против чего написана вся остальная её часть.

    Returns:
        None
    """
    import funora._client as client_module
    from funora._engine import IMPLEMENTED
    from funora.operations import OPERATIONS

    expected = {
        name
        for name, operation in OPERATIONS.items()
        if operation.capability in {item.value for item in IMPLEMENTED}
    }
    assert set(OPERATION_METHOD) == expected, (
        f"таблица соответствия разошлась с выполняемыми операциями: "
        f"лишние {sorted(set(OPERATION_METHOD) - expected)}, "
        f"недостающие {sorted(expected - set(OPERATION_METHOD))}"
    )

    for name, (service, method_name) in OPERATION_METHOD.items():
        method = getattr(getattr(client_module, service), method_name)
        actual = method.__annotations__.get("return")
        assert actual is not None, f"{service}.{method_name}: тип результата не объявлен"
        assert OPERATIONS[name].returns == actual, (
            f"{name}: спецификация обещает {OPERATIONS[name].returns}, "
            f"а {service}.{method_name} возвращает {actual}"
        )


def test_declared_return_schema_exists_and_names_the_same_type() -> None:
    """Проверяет ссылку на схему результата.

    Поле returns_schema не читает ни валидатор спецификации, ни генератор:
    первый его не знает, второй принимает и роняет. Опечатка в пути молчала бы.

    Проверка сегодня зелёная - это не находка, а сторож, и он оправдан ровно
    тем, что сторожить сейчас некому.

    Returns:
        None
    """
    import json

    import yaml

    root = _spec_dir()
    assert root is not None

    checked = 0
    for path in sorted((root / "spec" / "services").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, body in (doc.get("operations") or {}).items():
            reference = body.get("returns_schema")
            if reference is None:
                continue
            target = root / reference
            assert target.is_file(), f"{name}: схема результата {reference} не существует"

            schema = json.loads(target.read_text(encoding="utf-8"))
            declared = str(body["returns"]).removesuffix("[]")
            assert schema.get("title") == declared, (
                f"{name}: объявлен тип {declared}, а схема {reference} названа "
                f"{schema.get('title')!r}"
            )
            checked += 1

    assert checked >= 3, f"сверено схем результата: {checked} - слишком мало"


def test_every_declared_operation_is_implemented_or_registered() -> None:
    """Требует, чтобы объявленная операция была реализована либо перечислена.

    Правило проекта не знает третьего состояния между «реализовано» и
    «перечислено в реестре неисполненного». Для операций оно не проверялось
    ничем, и девятнадцать объявленных из двадцати двух не имели метода, при том
    что реестр называл поимённо одну.

    Вызывающий читает spec/services как перечень того, что можно позвать. Позвав
    отсутствующее, он получает встроенную ошибку языка, а не FunoraError: общий
    перехват её не поймает, и остановится не операция, а весь его цикл.

    Реализованным считается метод службы клиента, названный второй частью
    идентификатора операции. Судить по существованию имени, а не по поведению,
    здесь достаточно: отсутствующий метод отсутствует однозначно.

    Returns:
        None
    """
    import yaml

    from funora import _client
    from funora.operations import OPERATIONS

    root = _spec_dir()
    if root is None:
        pytest.skip(SKIP_REASON)

    services = {
        name.removesuffix("Service").lower(): getattr(_client, name)
        for name in dir(_client)
        if name.endswith("Service") and isinstance(getattr(_client, name), type)
    }
    assert services, (
        "у клиента не нашлось ни одной службы. Проверка сочла бы нереализованным "
        "всё подряд и прошла бы на любом реестре"
    )

    implemented = {
        f"{service}.{method}"
        for service, cls in services.items()
        for method in dir(cls)
        if not method.startswith("_")
    }

    registry = yaml.safe_load(
        (root / "spec" / "conformance" / "not-implemented.yaml").read_text(encoding="utf-8")
    )
    # Покрытием считается ТОЛЬКО covers. Указатель declared_in говорит, где
    # объявлен механизм, а не что операции нет: записи об отдельных свойствах -
    # об аудите правки цены, о полосе транспорта у витрины - указывают на
    # операцию, ничего не говоря о её существовании.
    #
    # Первая редакция засчитывала и declared_in, и проверка молчала при полном
    # удалении записи о службе: две её операции «покрывались» записями про их
    # свойства. Читатель, нашедший запись про полосу транспорта, заключил бы,
    # что операция есть, а не хватает ей только полосы.
    covered: set[str] = set()
    for entry in (registry.get("items") or {}).values():
        covered.update(str(one) for one in (entry or {}).get("covers") or [])

    orphans = sorted(one for one in OPERATIONS if one not in implemented and one not in covered)
    assert not orphans, (
        f"объявлены, не реализованы и не перечислены в реестре: {orphans}. "
        "Правило проекта не знает третьего состояния: вызывающий позовёт такую "
        "операцию и получит встроенную ошибку языка вместо FunoraError - "
        "остановится не операция, а весь его цикл"
    )

    # Обратное: запись реестра о реализованной операции протухла и вводит в
    # заблуждение сильнее умолчания - читатель решит, что звать нечего.
    stale = sorted(one for one in OPERATIONS if one in implemented and one in covered)
    assert not stale, (
        f"перечислены в реестре как неисполненные, а метод есть: {stale}. "
        "Запись, пережившая реализацию, отговаривает звать то, что работает"
    )
