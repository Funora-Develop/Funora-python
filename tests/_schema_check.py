"""Проверка значения по схеме JSON - подмножеству, которое умеет спецификация.

Зачем свой, а не библиотека. Библиотека умеет всё и молчит о том, чего не
понимает, - точнее, понимает всё и потому молчать ей не о чем. Проблема в
другом: полная библиотека - это зависимость ради проверок, а пакет держит
зависимости в двух строках намеренно.

Своя проверка опасна ровно одним: она тихо не проверит то, чего не умеет. Схема
дописывается ключевым словом, проверка его не знает, пропускает - и правило,
ради которого слово дописали, не действует. Молча.

Поэтому здесь всё наоборот: НЕИЗВЕСТНОЕ КЛЮЧЕВОЕ СЛОВО - ОШИБКА. Проверка
отказывается работать со схемой, которую не понимает целиком, и называет слово.
Дописавший увидит отказ в тот же день, а не через полгода на живых деньгах.

Поддерживаемое подмножество перечислено в _SCHEMA_KEYWORDS и _PROPERTY_KEYWORDS.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SchemaError", "check", "UnsupportedKeyword", "use_types"]

#: Ключевые слова, допустимые на верхнем уровне схемы.
#:
#: Слова с приставкой x-funora- проверкой значения не занимаются: они говорят
#: генератору кода и человеку, а не валидатору. Но перечислены они всё равно -
#: иначе опечатка в приставке прошла бы незамеченной.
_SCHEMA_KEYWORDS: frozenset[str] = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "x-funora-spec-version",
        "x-funora-event-type",
        "x-funora-not-implemented",
    }
)

#: Ключевые слова, допустимые внутри описания свойства.
_PROPERTY_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "description",
        "enum",
        "minimum",
        "maximum",
        "minItems",
        "items",
        "pattern",
        "properties",
        "required",
        "additionalProperties",
        "x-funora-type",
        "x-funora-plain",
        "x-funora-closed",
        "x-funora-observability",
        "x-funora-sensitivity",
        "x-funora-nullable",
        "x-funora-observed-value",
        "$ref",
    }
)

#: Как имена типов JSON Schema отображаются в типы Python.
#:
#: bool проверяется до int намеренно: в Python bool - подкласс int, и порядок
#: проверок решает, пройдёт ли True там, где схема требует целое.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}

#: Доменные типы спецификации, известные проверке.
#:
#: Перечень задаётся снаружи вызывающим, а не переписывается здесь: копия
#: словаря типов разошлась бы с spec/types.yaml молча, и проверка начала бы
#: отвергать тип, который спецификация объявила, либо принимать тот, которого
#: она не знает.
#:
#: По умолчанию пусто: сверка, не получившая словаря, отказывается работать со
#: всяким доменным типом. Отказ громкий - см. UnsupportedKeyword.
KNOWN_TYPES: set[str] = set()


def use_types(names: object) -> None:
    """Задаёт словарь доменных типов, известных проверке.

    Args:
        names (object): Имена типов из spec/types.yaml.

    Returns:
        None
    """
    KNOWN_TYPES.clear()
    KNOWN_TYPES.update(str(name) for name in names)  # type: ignore[union-attr]


class SchemaError(AssertionError):
    """Значение не соответствует схеме."""


class UnsupportedKeyword(AssertionError):
    """Схема пользуется ключевым словом, которого проверка не понимает.

    Отдельный тип, а не SchemaError: это не поломка данных, а поломка самой
    проверки. Отличать их нужно затем, что лечение разное - здесь дописывают
    проверку, там правят данные.
    """


def _fail(where: str, what: str) -> None:
    """Поднимает отказ с указанием места.

    Args:
        where (str): Путь до места в значении, например payload.status.
        what (str): Что не так.

    Returns:
        None

    Raises:
        SchemaError: Всегда.
    """
    raise SchemaError(f"{where}: {what}")


def _check_keywords(schema: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Убеждается, что схема не пользуется незнакомыми словами.

    Args:
        schema (dict[str, Any]): Тело схемы либо свойства.
        allowed (frozenset[str]): Допустимые ключевые слова.
        where (str): Путь до места в схеме.

    Returns:
        None

    Raises:
        UnsupportedKeyword: Если встретилось незнакомое слово.
    """
    unknown = sorted(set(schema) - allowed)
    if unknown:
        raise UnsupportedKeyword(
            f"{where}: проверка не понимает ключевые слова {unknown}. "
            "Допишите их в _schema_check.py либо не пользуйтесь ими: пропустить "
            "непонятое значило бы выключить правило молча"
        )


def _types_of(schema: dict[str, Any]) -> list[str]:
    """Возвращает объявленные типы значения списком.

    Args:
        schema (dict[str, Any]): Тело схемы либо свойства.

    Returns:
        list[str]: Имена типов. Пустой список, если тип не объявлен.
    """
    declared = schema.get("type")
    if declared is None:
        return []
    return list(declared) if isinstance(declared, list) else [declared]


def _check_value(value: Any, schema: dict[str, Any], where: str) -> None:
    """Проверяет одно значение по описанию свойства.

    Args:
        value (Any): Проверяемое значение.
        schema (dict[str, Any]): Описание свойства.
        where (str): Путь до значения.

    Returns:
        None

    Raises:
        SchemaError: Если значение не соответствует описанию.
        UnsupportedKeyword: Если описание пользуется незнакомым словом.
    """
    _check_keywords(schema, _PROPERTY_KEYWORDS, where)

    domain = schema.get("x-funora-type")
    if domain is not None:
        if domain not in KNOWN_TYPES:
            raise UnsupportedKeyword(f"{where}: проверка не знает x-funora-type «{domain}»")
        if not isinstance(value, str) or not value:
            _fail(where, f"доменный тип {domain} требует непустую строку, получено {value!r}")
        return

    # Пустота обязана объявить, что означает. Смыслов два, и они приводят к
    # разным решениям вызывающего: «не наблюдали» - значение, возможно, есть, а
    # прочитать не удалось; «неприменимо» - в этом состоянии поля не бывает.
    #
    # Здесь проверяется не выбор смысла (это дело валидатора спецификации), а
    # согласованность: объявленная пустота обязана быть выразимой типом.
    declared_null = schema.get("x-funora-nullable")
    if declared_null is not None and declared_null != "not_applicable":
        raise UnsupportedKeyword(f"{where}: проверка не знает x-funora-nullable «{declared_null}»")

    types = _types_of(schema)
    says_empty = declared_null == "not_applicable" or (
        schema.get("x-funora-observability") == "unobserved-possible"
    )
    if says_empty and types and "null" not in types:
        _fail(where, f"пустота объявлена, а тип её не допускает: {types}")
    if types and "null" in types and not says_empty and "$ref" not in schema:
        _fail(where, "тип допускает null, а смысл пустоты не объявлен")

    if types:
        # bool до int: bool - подкласс int, и без этого True прошёл бы как целое.
        if isinstance(value, bool) and "boolean" not in types:
            _fail(where, f"логическое значение там, где схема требует {types}")
        allowed: tuple[type, ...] = tuple(t for name in types for t in _JSON_TYPES[name])
        if not isinstance(value, allowed):
            _fail(where, f"тип {type(value).__name__} не входит в объявленные {types}")

    if "enum" in schema and value not in schema["enum"]:
        _fail(where, f"значение {value!r} вне перечисления {schema['enum']}")

    if "minimum" in schema and isinstance(value, int) and value < schema["minimum"]:
        _fail(where, f"значение {value} меньше минимума {schema['minimum']}")

    if "maximum" in schema and isinstance(value, int) and value > schema["maximum"]:
        _fail(where, f"значение {value} больше максимума {schema['maximum']}")

    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            _fail(where, f"элементов {len(value)}, минимум {schema['minItems']}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _check_value(item, item_schema, f"{where}[{index}]")

    if isinstance(value, dict) and "properties" in schema:
        check(value, schema, where=where, nested=True)


def check(
    value: Any, schema: dict[str, Any], *, where: str = "значение", nested: bool = False
) -> None:
    """Проверяет значение по схеме.

    Args:
        value (Any): Проверяемое значение, обычно нагрузка события.
        schema (dict[str, Any]): Схема.
        where (str): Путь до значения, для сообщения об отказе.
        nested (bool): True, если схема - описание вложенного свойства, а не
            схема целиком. Наборы допустимых ключевых слов у них разные:
            заголовок схемы несёт $id и title, описание свойства - нет.

    Returns:
        None

    Raises:
        SchemaError: Если значение не соответствует схеме.
        UnsupportedKeyword: Если схема пользуется незнакомым словом.
    """
    allowed = _PROPERTY_KEYWORDS if nested else _SCHEMA_KEYWORDS
    _check_keywords(schema, allowed, where)

    types = _types_of(schema)
    if types and "object" not in types:
        _fail(where, f"схема описывает {types}, а проверяется отображение")
    if not isinstance(value, dict):
        _fail(where, f"ожидалось отображение, получено {type(value).__name__}")

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    missing = sorted(required - set(value))
    if missing:
        _fail(where, f"нет обязательных полей {missing}")

    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            _fail(where, f"поля {extra} схемой не описаны, а схема закрыта")

    for name, item in value.items():
        item_schema = properties.get(name)
        if item_schema is None:
            continue
        _check_value(item, item_schema, f"{where}.{name}")
