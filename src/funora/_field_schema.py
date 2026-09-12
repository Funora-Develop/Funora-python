"""Схема фильтров раздела по наблюдённым элементам .lot-fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from selectolax.parser import HTMLParser

from ._extract import attribute, text
from ._observed import Observed
from ._result import Completeness, Defect, Severity
from .errors import IncompleteResultError, UnsupportedCapabilityError
from .extraction import SELECTORS


@dataclass(frozen=True, slots=True)
class FieldOption:
    """Вариант выбора. Пустое value тоже является допустимым значением."""

    value: Observed[str]
    label_text: Observed[str]


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """Поле фильтра; kind открыт для новых видов, неизвестный вид - unknown."""

    field_id: Observed[str]
    input_name: Observed[str]
    label_text: Observed[str]
    kind: str
    options: tuple[FieldOption, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldSchema:
    """Схема конкретного раздела с явным признаком полноты."""

    section_id: str
    observed_at: datetime
    completeness: Completeness
    reason: str
    defects: tuple[Defect, ...] = ()
    _fields: tuple[FieldDefinition, ...] = field(default=(), repr=False)

    def fields(self, *, accept_incomplete: bool = False) -> tuple[FieldDefinition, ...]:
        if self.completeness is not Completeness.COMPLETE and not accept_incomplete:
            raise IncompleteResultError(f"схема полей неполна: {self.reason}")
        return self._fields


def parse_field_schema(html: str, *, section_id: str, observed_at: datetime) -> FieldSchema:
    """Читает choice и range; новый вид сохраняется как неполный результат."""
    tree = HTMLParser(html)
    container = tree.css_first(SELECTORS["catalog.field_schema.container"])
    if container is None:
        raise UnsupportedCapabilityError("на странице раздела нет наблюдённого блока полей")
    fields: list[FieldDefinition] = []
    defects: list[Defect] = []
    seen: set[str] = set()
    for index, node in enumerate(container.css(SELECTORS["catalog.field_schema.fields"])):
        field_id = attribute(node, "data-id", "field_id")
        input_name = attribute(
            node.css_first(SELECTORS["catalog.field_schema.input"]), "name", "input_name"
        )
        choice = node.css_first(SELECTORS["catalog.field_schema.kinds.choice"])
        range_box = node.css_first(SELECTORS["catalog.field_schema.kinds.range"])
        kind = "choice" if choice is not None else "range" if range_box is not None else "unknown"
        options = (
            tuple(
                FieldOption(attribute(button, "value", "value"), text(button, "option_label"))
                for button in choice.css(SELECTORS["catalog.field_schema.kinds.choice.options"])
            )
            if choice is not None
            else ()
        )
        problems: list[str] = []
        if not field_id.or_none() or not input_name.or_none():
            problems.append("field_identifier_missing")
        name = input_name.or_none()
        if name:
            if name in seen:
                problems.append("duplicate_input_name")
            seen.add(name)
        if kind == "unknown" or (choice is not None and range_box is not None):
            problems.append("field_kind_unrecognized")
        if choice is not None and (
            not options
            or any(not option.value.is_observed for option in options)
            or len({option.value.or_none() for option in options}) != len(options)
        ):
            problems.append("choice_options_unusable")
        for code in problems:
            defects.append(Defect(severity=Severity.ROW, code=code, detail=code, row_index=index))
        fields.append(
            FieldDefinition(
                field_id,
                input_name,
                text(node.css_first(SELECTORS["catalog.field_schema.label"]), "label"),
                kind,
                options,
            )
        )
    return FieldSchema(
        section_id,
        observed_at,
        Completeness.PARTIAL if defects else Completeness.COMPLETE,
        "field_defects" if defects else "all_fields_parsed",
        tuple(defects),
        tuple(fields),
    )
