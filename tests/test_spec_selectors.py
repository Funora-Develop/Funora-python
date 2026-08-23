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
    """Проверяет, что объявленный селектор находится во ВСЕХ названных снимках.

    Прежде хватало одного из перечисленных. Список снимков - это утверждение
    «селектор наблюдался здесь, здесь и здесь», и выполнение его на треть
    оставляло две трети непроверенными: автор второй реализации прочёл бы
    список как гарантию и построил бы на ней разбор страницы, где селектора
    нет.

    Если селектор вправду есть только на одной странице, в списке должна стоять
    одна.

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

    absent = [name for name in evidence if name not in found_in]
    assert not absent, (
        f"{where}: селектор {selector!r} помечен наблюдённым, но найден не во "
        f"всех названных снимках. Нет в: {', '.join(absent)}. Список снимков - "
        f"утверждение «наблюдался здесь», и выполненное наполовину оно вводит "
        f"в заблуждение сильнее, чем отсутствующее"
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


def _absent_claims() -> list[dict[str, object]]:
    """Собирает утверждения об отсутствии селектора в снимке.

    Returns:
        list[dict[str, object]]: Записи перечня, у которых есть absent_in.
    """
    return [entry for entry in ENTRIES if entry.get("absent_in")]


@pytest.mark.parametrize(
    "entry",
    _absent_claims(),
    ids=lambda e: f"{e['selector']} отсутствует в {','.join(e['absent_in'])}",
)
def test_declared_absence_holds(entry: dict[str, object]) -> None:
    """Проверяет, что селектор вправду отсутствует в названных снимках.

    Отсутствие - вторая половина наблюдения, и до сих пор её не проверял никто.
    Признак вошедшего, который вдруг нашёлся бы и на гостевой странице,
    перестал бы различать сессии - а спецификация продолжала бы утверждать, что
    различает. Клиент решал бы, что сессия жива, на странице входа.

    Args:
        entry (dict[str, object]): Запись перечня: селектор, снимки, место.

    Returns:
        None
    """
    selector = str(entry["selector"])
    absent_in = [str(x) for x in entry["absent_in"]]  # type: ignore[union-attr]
    where = str(entry["where"])

    missing: list[str] = []
    found_in: list[str] = []
    for name in absent_in:
        path = FIXTURES / f"{name}.skeleton.txt"
        if not path.is_file():
            missing.append(name)
            continue
        if HTMLParser(_read(name)).css_first(selector) is not None:
            found_in.append(name)

    assert not missing, f"{where}: снимков нет в репозитории: {', '.join(missing)}"
    assert not found_in, (
        f"{where}: селектор {selector!r} объявлен отсутствующим в "
        f"{', '.join(absent_in)}, а найден в {', '.join(found_in)}. Признак, "
        "который есть на обеих сторонах, различает не то, что обещает"
    )


def _attribute_claims() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """Собирает объявленные имена атрибутов вместе с носителем и снимками.

    Носитель ищется рядом, а не сверху. Блок ``attributes`` объявлен СОСЕДОМ
    записи ``item``, а не её потомком: у списка диалогов селектор строки лежит в
    item, а имена атрибутов этой строки - в attributes. Наследование сверху дало
    бы пустого носителя, а сверять имя атрибута не на чем значит не сверять
    вовсе.

    Снимков у объявления бывает несколько, и проверяются ВСЕ. Перечень снимков -
    утверждение «наблюдался здесь, здесь и здесь», и выполненное наполовину оно
    вводит в заблуждение ровно так же, как у селекторов.

    Returns:
        list[tuple[str, str, str, tuple[str, ...]]]: Ключ, имя атрибута,
        селектор носителя и снимки. Пустой список, если спецификация недоступна.
    """
    root = _spec_dir()
    if root is None:
        return []

    import yaml

    out: list[tuple[str, str, str, tuple[str, ...]]] = []

    def snapshots(node: object) -> tuple[str, ...]:
        """Приводит поле свидетельства к набору имён снимков.

        Args:
            node (object): Узел, у которого спрашивается свидетельство.

        Returns:
            tuple[str, ...]: Имена снимков. Пустой набор, если их нет.
        """
        if not isinstance(node, dict):
            return ()
        raw = node.get("evidence")
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, list):
            return tuple(str(one) for one in raw)
        return ()

    def carrier(holder: dict[str, object]) -> tuple[str, tuple[str, ...]]:
        """Находит носителя атрибутов и его снимки.

        Args:
            holder (dict[str, object]): Узел, содержащий блок attributes.

        Returns:
            tuple[str, tuple[str, ...]]: Селектор носителя и снимки.
        """
        item = holder.get("item")
        if isinstance(item, dict) and isinstance(item.get("selector"), str):
            return str(item["selector"]), snapshots(item)
        if isinstance(holder.get("selector"), str):
            return str(holder["selector"]), snapshots(holder)
        return "", ()

    def walk(node: object, path: str, origin: str) -> None:
        """Обходит документ, собирая имена атрибутов.

        Args:
            node (object): Узел документа.
            path (str): Путь до узла.
            origin (str): Имя файла без расширения.

        Returns:
            None
        """
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else key
                if (key == "attribute" or key.endswith("_attribute")) and isinstance(value, str):
                    holder = str(node.get("selector") or "")
                    out.append((f"{origin}.{here}", value, holder, snapshots(node)))
                    continue
                if key == "attributes" and isinstance(value, dict):
                    holder, evidence = carrier(node)
                    for name, body in value.items():
                        if not isinstance(body, dict) or not isinstance(body.get("name"), str):
                            continue
                        own = snapshots(body) or evidence
                        out.append(
                            (
                                f"{origin}.{here}.{name}",
                                str(body["name"]),
                                str(body.get("selector") or holder),
                                own,
                            )
                        )
                    continue
                walk(value, here, origin)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", origin)

    for source in sorted((root / "spec" / "extraction").glob("*.yaml")):
        walk(yaml.safe_load(source.read_text(encoding="utf-8")), "", source.stem)
    return out


#: Объявленные атрибуты, прочитанные один раз на весь набор.
ATTRIBUTE_CLAIMS = _attribute_claims()


@pytest.mark.parametrize(
    "claim",
    ATTRIBUTE_CLAIMS,
    ids=[f"{one[0]} -> {one[1]}" for one in ATTRIBUTE_CLAIMS],
)
def test_declared_attribute_exists_in_fixture(claim: tuple[str, str, str, str]) -> None:
    """Проверяет, что объявленное имя атрибута вправду есть на снимке.

    Имя атрибута - такой же договор с площадкой, как и селектор: разбор списка
    диалогов стоит на трёх из них целиком. Сверять их со снимками было нечем, и
    объявление могло назвать любое имя.

    Проверяется на носителе, а не по всему документу: атрибут с тем же именем
    вправе встретиться и в другом месте страницы, и находка там ничего не
    доказывает о строке списка.

    Args:
        claim (tuple[str, str, str, tuple[str, ...]]): Ключ, имя атрибута,
            селектор носителя и снимки-свидетельства.

    Returns:
        None
    """
    key, name, holder, evidence = claim

    assert evidence, (
        f"{key}: имя атрибута объявлено без свидетельства. Наблюдение без "
        "снимка не отличается от догадки, а на этом имени стоит разбор"
    )
    assert holder, (
        f"{key}: у атрибута не нашлось носителя. Искать его пришлось бы по "
        "всему документу, а находка в другом месте страницы ничего не "
        "доказывает о нужном узле"
    )

    absent: list[str] = []
    for snapshot in evidence:
        path = FIXTURES / f"{snapshot}.skeleton.txt"
        assert path.is_file(), f"{key}: снимка {snapshot} нет в репозитории"

        tree = HTMLParser(_read(snapshot))
        nodes = tree.css(holder) if not holder.startswith("self") else [tree.body]
        if not any(one is not None and name in (one.attributes or {}) for one in nodes):
            absent.append(snapshot)

    assert not absent, (
        f"{key}: атрибут {name!r} объявлен наблюдённым, а на узле {holder!r} его "
        f"нет в снимках: {', '.join(absent)}. Разбор, построенный на этом имени, "
        "вернёт пустоту и объявит её наблюдением"
    )


@pytest.mark.skipif(not ATTRIBUTE_CLAIMS, reason=SKIP_REASON)
def test_every_declared_attribute_is_checked() -> None:
    """Требует, чтобы сверялись ВСЕ объявленные атрибуты, а не сколько нашлось.

    Проверка выше сама выбирает, что проверять: она обходит объявления и
    собирает те, у которых есть имя. Запись, потерявшая имя, из перебора просто
    исчезает - счёт сходится, а проверяется на одно меньше. Мутация это и
    показала: атрибут, у которого поле name заменили на другое, прошёл молча.

    Здесь набор ключей сверяется с порождённым словарём. Генератор строит его
    своим обходом и падает на записи без имени, значит два независимых взгляда
    обязаны совпасть.

    Returns:
        None
    """
    from funora.extraction import ATTRIBUTES

    collected = {one[0] for one in ATTRIBUTE_CLAIMS}
    generated = set(ATTRIBUTES)
    assert collected == generated, (
        f"перебор и порождённый словарь разошлись: только в переборе "
        f"{sorted(collected - generated)}, только в словаре "
        f"{sorted(generated - collected)}. Расхождение означает, что часть "
        "объявленных атрибутов не сверяется со снимками ни одной проверкой"
    )


@pytest.mark.skipif(not ATTRIBUTE_CLAIMS, reason=SKIP_REASON)
def test_declared_attributes_do_not_share_a_name() -> None:
    """Запрещает двум ролям читать один и тот же атрибут.

    Проверка присутствия имени на снимке такого не ловит: оба имени на странице
    есть, и оба находятся. А смысл при этом вывернут.

    Так и вышло на мутации: позиция последнего сообщения и отметка прочтения
    получили одно имя. Обе читали бы отметку прочтения, они всегда совпадали бы,
    и признак непрочитанного оказался бы вечно ложным - события о новых
    сообщениях перестали бы приходить вовсе, и тихо.

    Returns:
        None
    """
    seen: dict[str, str] = {}
    for key, name, _holder, _evidence in ATTRIBUTE_CLAIMS:
        block = key.rsplit(".", 1)[0]
        where = f"{block}:{name}"
        assert where not in seen, (
            f"атрибут {name!r} объявлен и у «{seen[where]}», и у «{key}». Две "
            "роли, читающие один атрибут, всегда дают одно значение: их "
            "сравнение вечно истинно, а выведенный из него признак - вечно ложен"
        )
        seen[where] = key
