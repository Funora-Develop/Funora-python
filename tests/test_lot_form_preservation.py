"""Правка цены не обнуляет соседние поля формы."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from funora._lot_form import parse_lot_form
from funora.errors import ProtocolChangedError

HTML = (Path(__file__).parent / "fixtures/pages/lot-edit.logged.ru.skeleton.txt").read_text()
NOW = datetime(2026, 9, 6, tzinfo=UTC)


def form_with(fields: str):
    # Вставка в форму редактора, а не в первую форму шапки страницы.
    at = HTML.index("form-offer-editor")
    end = HTML.index("</form>", at)
    return parse_lot_form(HTML[:end] + fields + HTML[end:], observed_at=NOW)


def test_price_update_preserves_selected_values_and_checkbox_values() -> None:
    form = form_with("""
      <select name="server"><option value="1">one</option>
      <option selected value="2">two</option></select>
      <input type="checkbox" name="custom_flag" value="yes" checked>
      <input type="hidden" name="flags" value="0">
      <input type="checkbox" name="flags" value="1" checked>
      <input type="radio" name="choice" value="a">
      <input type="radio" name="choice" value="b" checked>
      <input disabled name="ignored" value="private">
    """)
    request = form.to_request(price="123")
    assert request["server"] == "2"
    assert request["choice"] == "b"
    assert request["custom_flag"] == "yes"
    assert request["flags"] == "1"
    assert "ignored" not in request
    assert request["price"] == "123"
    assert form.price_text != "123"


def test_select_without_selected_uses_the_browser_default() -> None:
    form = form_with('<select name="server"><option>first</option><option>second</option></select>')
    assert form.to_request()["server"] == "first"


@pytest.mark.parametrize(
    "extra",
    [
        '<select name="server" multiple><option selected value="a">a</option></select>',
        '<select name="server"></select>',
        '<input name="duplicate" value="a"><input name="duplicate" value="b">',
        '<input type="checkbox" name="flags" value="a" checked>'
        '<input type="checkbox" name="flags" value="b" checked>',
    ],
)
def test_unrepresentable_fields_refuse_to_write(extra: str) -> None:
    with pytest.raises(ProtocolChangedError):
        form_with(extra)


def test_activating_an_unchecked_checkbox_keeps_its_declared_value() -> None:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(HTML)
    active = tree.css_first('input[name="active"]')
    if "checked" in active.attributes:
        del active.attrs["checked"]
    active.attrs["value"] = "yes"
    form = parse_lot_form(tree.html, observed_at=NOW)
    assert not form.is_active
    assert form.to_request(active=True)["active"] == "yes"
    assert "active" not in form.to_request()
