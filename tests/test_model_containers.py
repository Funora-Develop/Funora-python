"""Сверка формы проверяет словарь значений и уникальные строковые флажки."""

import pytest
from _schema_check import SchemaError, UnsupportedKeyword, check


def container_schema(property_schema):
    return {"type": "object", "properties": {"data": property_schema}}


@pytest.mark.parametrize("values", [[], ["active"], ["active", "deactivate_after_sale"]])
def test_unique_string_items_are_accepted(values):
    schema = container_schema({"type": "array", "items": {"type": "string"}, "uniqueItems": True})
    check({"data": values}, schema)


@pytest.mark.parametrize("values", [["active", "active"], ["a", "b", "a"]])
def test_repeated_string_items_are_rejected(values):
    schema = container_schema({"type": "array", "items": {"type": "string"}, "uniqueItems": True})
    with pytest.raises(SchemaError, match="повторяются"):
        check({"data": values}, schema)


def test_uniqueness_is_not_imposed_when_disabled():
    schema = container_schema({"type": "array", "items": {"type": "string"}, "uniqueItems": False})
    check({"data": ["active", "active"]}, schema)


@pytest.mark.parametrize(
    "prop",
    [
        {"type": "array", "items": {"type": "string"}, "uniqueItems": "true"},
        {"type": "array", "items": {"type": "object"}, "uniqueItems": True},
        {"type": "array", "uniqueItems": True},
    ],
)
def test_unsupported_uniqueness_rules_do_not_silently_pass(prop):
    with pytest.raises(UnsupportedKeyword, match="uniqueItems"):
        check({"data": []}, container_schema(prop))


@pytest.mark.parametrize("value", [False, 7, None, [], {}])
def test_mapping_values_must_follow_additional_properties_schema(value):
    schema = container_schema({"type": "object", "additionalProperties": {"type": "string"}})
    check({"data": {"amount": "7"}}, schema)
    with pytest.raises(SchemaError):
        check({"data": {"amount": value}}, schema)


def test_declared_properties_are_checked_separately_from_additional_ones():
    schema = container_schema(
        {
            "type": "object",
            "properties": {"known": {"type": "integer"}},
            "additionalProperties": {"type": "string"},
        }
    )
    check({"data": {"known": 1, "amount": "7"}}, schema)
    with pytest.raises(SchemaError):
        check({"data": {"known": "1", "amount": "7"}}, schema)


def test_closed_mapping_without_named_properties_rejects_every_key():
    schema = container_schema({"type": "object", "additionalProperties": False})
    check({"data": {}}, schema)
    with pytest.raises(SchemaError):
        check({"data": {"amount": "7"}}, schema)
