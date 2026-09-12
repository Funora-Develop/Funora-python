"""Поле фильтра читается по снимку; неизвестное не выдаётся за полную схему."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_client import _FakeFetcher, _observation

from funora import AsyncClient, Client, FieldSchema
from funora._field_schema import parse_field_schema
from funora._result import Completeness
from funora.capabilities import Capability, CapabilityState
from funora.errors import IncompleteResultError, UnsupportedCapabilityError, ValidationError

NOW = datetime(2026, 9, 6, tzinfo=UTC)
HTML = (Path(__file__).parent / "fixtures/pages/catalog-fields.logged.ru.skeleton.txt").read_text()


def test_observed_schema_keeps_choices_ranges_and_empty_option() -> None:
    result = parse_field_schema(HTML, section_id="922", observed_at=NOW)
    assert result.completeness is Completeness.COMPLETE
    choice, interval = result.fields()
    assert choice.kind == "choice"
    assert choice.input_name.value == "f-type"
    assert not choice.label_text.is_observed
    assert [one.value.value for one in choice.options] == ["", "T14:cs#1", "T14:cs#2"]
    assert interval.kind == "range"
    assert interval.input_name.value == "f-members"
    assert interval.label_text.is_observed


@pytest.mark.parametrize("replacement", ["unknown-box", "lot-field-range-box"])
def test_unrecognized_or_ambiguous_fields_are_partial(replacement: str) -> None:
    html = HTML.replace('class="lot-field-radio-box"', f'class="{replacement}"')
    result = parse_field_schema(html, section_id="922", observed_at=NOW)
    if replacement == "unknown-box":
        assert result.completeness is Completeness.PARTIAL
        with pytest.raises(IncompleteResultError):
            result.fields()
        assert result.fields(accept_incomplete=True)[0].kind == "unknown"
    else:
        assert result.fields()[0].kind == "range"


@pytest.mark.parametrize(
    "before,after",
    [
        ('name="f-members"', 'name="f-type"'),
        ('name="f-members"', ""),
        ('value="T14:cs#2"', 'value="T14:cs#1"'),
        ('value="T14:cs#2"', ""),
        ('class="lot-field-radio-box"', 'class="lot-field-radio-box lot-field-range-box"'),
    ],
)
def test_damaged_field_schema_is_not_silently_complete(before: str, after: str) -> None:
    result = parse_field_schema(HTML.replace(before, after), section_id="922", observed_at=NOW)
    assert result.completeness is Completeness.PARTIAL
    assert result.defects


def test_missing_fields_are_unsupported_for_only_that_section() -> None:
    missing = HTML.replace('class="lot-fields"', 'class="no-fields"')
    with Client(transport=_FakeFetcher([_observation(missing), _observation(HTML)])) as client:
        with pytest.raises(UnsupportedCapabilityError):
            client.catalog.field_schema("1")
        assert client.capability(Capability.CATALOG_FIELD_SCHEMA) is CapabilityState.UNKNOWN
        result = client.catalog.field_schema("922")
        assert isinstance(result, FieldSchema)
        assert len(result.fields()) == 2
        assert client.capability(Capability.CATALOG_FIELD_SCHEMA) is CapabilityState.SUPPORTED


def test_invalid_section_never_reaches_transport() -> None:
    with Client(transport=_FakeFetcher([])) as client, pytest.raises(ValidationError):
        client.catalog.field_schema("../chat")


async def test_async_schema_uses_the_same_core() -> None:
    from test_aclient import _AsyncFakeFetcher

    async with AsyncClient(transport=_AsyncFakeFetcher([_observation(HTML)])) as client:
        result = await client.catalog.field_schema("922")
        assert len(result.fields()) == 2


def test_field_schema_matches_the_contract() -> None:
    from _schema_check import check
    from test_model_schemas import _as_json, _page_as_json, _schema

    page = parse_field_schema(HTML, section_id="922", observed_at=NOW)
    check(_page_as_json(page, "fields", page.fields()), _schema("field-schema"))
    for entry in page.fields():
        check(_as_json(entry), _schema("field-definition"))
        for option in entry.options:
            check(_as_json(option), _schema("field-option"))
