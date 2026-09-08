"""JSON без неоднозначных ключей и неконечных чисел."""

import json
from math import isfinite
from typing import Any


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("повтор поля JSON")
        result[name] = value
    return result


def _finite_number(raw: str) -> float:
    value = float(raw)
    if not isfinite(value):
        raise ValueError("неконечное число JSON")
    return value


def load_json(body: str) -> Any:
    """Читает JSON; вызывающий переводит отказ в ошибку своей границы данных."""
    return json.loads(
        body,
        object_pairs_hook=_unique_fields,
        parse_float=_finite_number,
        parse_constant=_finite_number,
    )
