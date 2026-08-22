"""Общая подготовка наборов.

Здесь только то, что обязано случиться до любого набора: словарь доменных типов
для сверки со схемами. Держать его копией внутри сверки нельзя - копия разошлась
бы со spec/types.yaml молча, и сверка начала бы отвергать тип, который
спецификация объявила.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _domain_types() -> None:
    """Отдаёт сверке словарь доменных типов из спецификации.

    Returns:
        None
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return
    source = Path(raw) / "spec" / "types.yaml"
    if not source.is_file():
        return

    import yaml
    from _schema_check import use_types

    doc = yaml.safe_load(source.read_text(encoding="utf-8"))
    use_types(doc["types"])
