"""Сверка: объявленное контрактом либо читается, либо честно перечислено.

Набор появился после разбора, который нашёл несколько чисел и признаков,
порождённых из спецификации и не читаемых ничем. Порождённый файл при этом
выглядел исправным: значение в нём стояло правильное, проверка свежести
проходила, а поведение оставалось прежним. Правка спецификации меняла файл и не
меняла ничего больше.

Такое расхождение не ловится ни проверкой свежести порождения, ни проверками
поведения. Первая сверяет файл с источником, вторая - поведение с ожиданием, и
между ними остаётся щель: значение из источника попало в файл и никуда дальше.

Здесь эта щель и закрывается. Каждое имя, порождённое из спецификации, обязано
быть либо прочитанным где-то в пакете, либо перечисленным - в реестре
неисполненного на стороне спецификации либо в списке ниже, если оно
предназначено вызывающему, а не реализации.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path

import pytest

#: Каталог исходников пакета.
SRC = Path(__file__).resolve().parent.parent / "src" / "funora"

#: Порождённые из спецификации модули.
GENERATED = (
    "budget",
    "retry",
    "capabilities",
    "response_classes",
    "events",
    "extraction",
    "operations",
    "contract",
    # Модуль ошибок стоял вне проверки, и это стоило дорого: из 27 листовых
    # классов девять не встречались в исполняемом коде ни разу, а найти это
    # удалось только отдельным разбором. Класс ошибки, объявленный и никем не
    # возбуждаемый, - то же молчащее объявление, что и всё остальное здесь, и
    # хуже прочих: вызывающий выписывает по нему except и ждёт события,
    # которого не будет.
    "errors",
)

#: Имена, предназначенные вызывающему, а не реализации.
#:
#: Реализация ими не пользуется и пользоваться не должна: это справочники,
#: которые пакет отдаёт наружу, чтобы вызывающий мог разобрать ошибку или
#: сверить состояние. Отсутствие ссылок на них внутри пакета - не пробел.
#:
#: Список именно такой формы - перечисление с обоснованием, - а не признак у
#: каждого имени: признак пришлось бы ставить генератору, а генератор не знает,
#: кому имя предназначено.
FOR_CALLERS: frozenset[str] = frozenset(
    {
        # Базовые классы иерархии ошибок. Их не возбуждают - возбуждают
        # потомков, - но вызывающему они нужны именно для того, чтобы ловить
        # по иерархии: except TransportError ловит и таймаут, и обрыв связи,
        # не перечисляя их поимённо. Отсутствие ссылок внутри пакета здесь не
        # пробел, а устройство.
        "FunoraError",
        "AuthenticationError",
        "CapabilityError",
        "TransportError",
        "BudgetError",
        "ProtocolError",
        "DomainError",
        "StateError",
        "HandlerError",
        "UsageError",
        # Справочники, по которым вызывающий переводит устойчивый
        # идентификатор либо код ABI в класс. Читает их он, а не пакет.
        "ERROR_BY_STABLE_ID",
        "ERROR_BY_ABI_CODE",
        # Справочник соответствия возможности и страницы, на которой она
        # наблюдается. Нужен тому, кто разбирается, почему возможность в таком
        # состоянии.
        "CAPABILITY_SOURCE",
        # Перечень классов ответа строками. Реализация пользуется перечислением,
        # а строки нужны для сверки с чужой реализацией.
        "RESPONSE_CLASSES",
        # Признак того, что числа бюджета подобраны, а не измерены. Читает его
        # человек, а не код.
        "PROVISIONAL",
        # Версия и состояние контракта. Пакет обязан уметь ответить, какой
        # контракт он реализует: при шести SDK и независимом версионировании
        # спецификации это и есть тот вопрос, ради которого version.yaml
        # заведён. Отвечает он вызывающему, а не себе.
        "SPEC_VERSION",
        "SPEC_STATUS",
        # Тип записи таблицы операций. Реализация пользуется самой таблицей, а
        # тип нужен вызывающему, который разбирает её содержимое.
        "Operation",
        # То же и с реакцией на ограничение: реализация читает её значения, а
        # тип нужен тому, кто разбирает их снаружи.
        "RateLimitResponse",
        # Объявленный тип результата операции. Реализация его не читает и читать
        # не должна: она возвращает конкретный объект, а строка нужна для сверки
        # между реализациями - и такая сверка есть, в tests/test_spec_coverage.py.
        "returns",
    }
)


def _spec_dir() -> Path | None:
    """Находит рабочую копию спецификации, если она задана.

    Returns:
        Path | None: Каталог репозитория Funora-spec либо None.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / "spec" / "conformance" / "not-implemented.yaml").is_file() else None


#: Причина пропуска, общая для набора.
SKIP_REASON = "переменная FUNORA_SPEC_DIR не задана или не указывает на рабочую копию Funora-spec"

pytestmark = pytest.mark.skipif(_spec_dir() is None, reason=SKIP_REASON)


def _registry() -> dict[str, dict[str, object]]:
    """Читает реестр неисполненного из спецификации.

    Returns:
        dict[str, dict[str, object]]: Записи реестра по идентификатору.
    """
    import yaml

    root = _spec_dir()
    assert root is not None
    doc = yaml.safe_load(
        (root / "spec" / "conformance" / "not-implemented.yaml").read_text(encoding="utf-8")
    )
    items: dict[str, dict[str, object]] = doc["items"]
    return items


def _declared_unread() -> frozenset[str]:
    """Собирает имена, объявленные реестром как нечитаемые.

    Returns:
        frozenset[str]: Имена из поля symbols всех записей реестра.
    """
    names: set[str] = set()
    for item in _registry().values():
        for symbol in item.get("symbols") or []:
            names.add(str(symbol))
    return frozenset(names)


def _without_imports(source: str) -> list[str]:
    """Отбрасывает строки импорта, комментариев и документации.

    Имя, встречающееся только в импорте, не прочитано: импорт ссылается на него
    и ничего с ним не делает. Первая редакция проверки этого не различала, и
    возврат предела переходов к литералу проходил незамеченным - имя оставалось
    в строке импорта, и проверка считала его прочитанным.

    Docstring и комментарии выброшены по той же причине, и нашлось это
    мутацией самой проверки. Класс ошибки перестали возбуждать - проверка
    промолчала, потому что имя осталось в разделе Raises. Упоминание в
    документации это ровно то состояние, против которого проверка написана:
    объявление есть, обещание есть, исполнения нет.

    Args:
        source (str): Исходный текст модуля.

    Returns:
        list[str]: Строки исполняемого кода за вычетом импортов, комментариев
        и строк документации.
    """
    source = re.sub(r'"""(?:.|\n)*?"""', "", source)
    kept: list[str] = []
    inside = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if inside:
            if stripped.startswith(")"):
                inside = False
            continue
        if stripped.startswith(("import ", "from ")):
            if stripped.endswith("("):
                inside = True
            continue
        kept.append(line)
    return kept


def _read_somewhere(symbol: str, *, except_module: str) -> bool:
    """Проверяет, читается ли имя где-нибудь в пакете.

    Args:
        symbol (str): Имя.
        except_module (str): Имя модуля, где оно объявлено. Оно не считается.

    Returns:
        bool: True, если имя встречается в другом модуле пакета.
    """
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    for path in SRC.glob("*.py"):
        if path.name == f"{except_module}.py":
            continue
        for line in _without_imports(path.read_text(encoding="utf-8")):
            if pattern.search(line):
                return True
    return False


def _generated_fields() -> list[tuple[str, str, str]]:
    """Собирает поля структур из порождённых модулей.

    Проверка выше смотрит имена модуля, и поля структур мимо неё проходят: у
    RetryPolicy десять полей, а в __all__ стоит одно имя. Так и остались
    незамеченными два поля, порождённые из спецификации и не прочитанные
    ничем.

    Returns:
        list[tuple[str, str, str]]: Модуль, структура, поле.
    """
    import dataclasses

    found: list[tuple[str, str, str]] = []
    for module_name in GENERATED:
        module = importlib.import_module(f"funora.{module_name}")
        for symbol in getattr(module, "__all__", ()):
            value = getattr(module, symbol, None)
            if not (isinstance(value, type) and dataclasses.is_dataclass(value)):
                continue
            for field in dataclasses.fields(value):
                found.append((module_name, symbol, field.name))
    return found


def test_every_generated_name_is_read_or_declared_unread() -> None:
    """Проверяет, что порождённое имя либо читается, либо честно объявлено.

    Третьего - «объявлено и молчит» - быть не должно. Именно так в контракте
    оказались залповый предел ведра, доли классов запросов и реакция на
    ограничение площадки: все три выглядели частью контракта и не делали
    ничего.

    Returns:
        None
    """
    unread_by_design = _declared_unread()
    silent: list[str] = []

    for module_name in GENERATED:
        module = importlib.import_module(f"funora.{module_name}")
        for symbol in getattr(module, "__all__", ()):
            if symbol.startswith("_") or symbol in FOR_CALLERS or symbol in unread_by_design:
                continue
            if not _read_somewhere(symbol, except_module=module_name):
                silent.append(f"{module_name}.{symbol}")

    assert not silent, (
        f"порождено из спецификации и не читается ничем: {sorted(silent)}. "
        "Либо примените значение, либо внесите имя в spec/conformance/"
        "not-implemented.yaml - молчащее объявление хуже отсутствующего: оно "
        "выглядит работающим"
    )


def test_registry_does_not_list_what_is_already_read() -> None:
    """Проверяет, что реестр не держит уже реализованное.

    Правило работает в обе стороны. Реестр, из которого ничего не выбывает,
    через полгода объявляет неисполненным то, что давно исполнено, - и
    следующий читатель поверит ему, а не коду.

    Returns:
        None
    """
    unread_by_design = _declared_unread()
    stale: list[str] = []
    for module_name in GENERATED:
        module = importlib.import_module(f"funora.{module_name}")
        for symbol in getattr(module, "__all__", ()):
            if symbol in unread_by_design and _read_somewhere(symbol, except_module=module_name):
                stale.append(f"{module_name}.{symbol}")

    # Поля структур проверяются здесь же. Прежде проверка смотрела только имена
    # модуля, и поле, которое начали читать, оставалось в реестре навсегда: у
    # RetryPolicy десять полей, а в __all__ стоит одно имя.
    for module_name, structure, field in _generated_fields():
        if field in unread_by_design and _read_somewhere(field, except_module=module_name):
            stale.append(f"{module_name}.{structure}.{field}")

    assert not stale, (
        f"реестр неисполненного держит уже читаемое: {sorted(stale)}. "
        "Уберите записи из spec/conformance/not-implemented.yaml"
    )


def test_registry_entries_explain_themselves() -> None:
    """Проверяет, что каждая запись реестра объясняет себя.

    Запись без объяснения - это строка «мы это не делаем», по которой нельзя
    решить, стоит ли на неё полагаться и что будет, если положиться.

    Returns:
        None
    """
    for name, item in _registry().items():
        for field in ("declared_in", "what", "why_not", "user_impact"):
            value = str(item.get(field) or "").strip()
            assert len(value) > 20, f"запись {name}: поле {field} пусто или бессодержательно"


def test_every_generated_field_is_read_or_declared_unread() -> None:
    """Проверяет, что поле порождённой структуры кем-то читается.

    Поле, порождённое из спецификации и не прочитанное ничем, - это обещание
    контракта, которого реализация не держит, и заметить его нечем: файл
    перестраивается, проверка свежести зелёная, значение стоит правильное.

    Так в RetryPolicy оказались fail_closed и account_scoped: первое требует
    остановки работы до вмешательства человека, второе - учёта ограничения на
    весь аккаунт. Ни то, ни другое не делается.

    Returns:
        None
    """
    unread_by_design = _declared_unread()
    silent: list[str] = []

    for module_name, structure, field in _generated_fields():
        if field.startswith("_") or field in unread_by_design or field in FOR_CALLERS:
            continue
        if not _read_somewhere(field, except_module=module_name):
            silent.append(f"{module_name}.{structure}.{field}")

    assert not silent, (
        f"поля порождены из спецификации и не читаются ничем: {sorted(silent)}. "
        "Либо примените значение, либо внесите имя поля в spec/conformance/"
        "not-implemented.yaml"
    )


def test_every_covered_operation_is_named_in_its_own_entry() -> None:
    """Требует, чтобы статья реестра объясняла ИМЕННО ТО, что покрывает.

    ПОЙМАНО ЭТИМ ПРАВИЛОМ 31.08.2026, и поймано задним числом.

    Объяснение про market.offers - про модель Offer, про идентификатор в строке
    запроса, про постраничную навигацию - лежало в статье про ДИАЛОГИ. А статья
    про рынок, та самая, что покрывает market.offers, несла причину «наблюдений
    пока мало, семь снимков на 24.08.2026» - снятую наблюдением тремя днями
    раньше.

    Читающий реестр узнавал о рынке неправду. Ни одни ворота этого не видели:
    перечень covers был верен, проза была верна, неверно было СООТВЕТСТВИЕ между
    ними - а его никто не проверял.

    Правило простое и механическое: если статья объявляет, что покрывает
    операцию, она обязана эту операцию НАЗВАТЬ. Не русскими словами, а
    идентификатором - тем же, что стоит в covers.

    Оно не требует, чтобы статья молчала о чужих операциях: ссылаться друг на
    друга статьям можно и нужно. Оно требует обратного - чтобы своя не осталась
    неупомянутой.

    Returns:
        None
    """
    for name, item in _registry().items():
        covers = item.get("covers") or []
        if not covers:
            continue

        prose = " ".join(str(item.get(field) or "") for field in ("what", "why_not", "user_impact"))
        silent = [one for one in covers if one not in prose]
        assert not silent, (
            f"статья {name} объявляет, что покрывает {silent}, и ни разу их не "
            "называет. Значит объяснение к ним либо отсутствует, либо лежит в "
            "другой статье - и там оно относится к чужой операции"
        )
