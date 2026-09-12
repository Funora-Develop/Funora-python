"""Все записи проходят квоту перед первым изменением, безопасный POST - нет."""

from datetime import UTC, datetime

import pytest
from test_currency_switch import BALANCE_HTML
from test_refund import _order_page
from test_reviews_write import ORDER_HTML
from test_send_image import NODE, PNG, THREAD_HTML, _observation
from test_update_price import NODE as LOT_NODE
from test_update_price import OFFER, WHEN, _page

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit, Upload
from funora._lot_form import parse_lot_form
from funora._transport import TransportSettings
from funora.capabilities import Capability
from funora.errors import BudgetExhaustedError
from funora.operations import OPERATIONS


def _lot(engine, *, price=False, activate=False):
    form = parse_lot_form(_page(active=not activate), observed_at=WHEN)
    if price:
        return engine.update_price(LOT_NODE, OFFER, "3.50", expected_revision=form.revision)
    return engine.set_lot_visible(
        LOT_NODE, OFFER, visible=activate, expected_revision=form.revision
    )


WRITES = {
    "account.switch_currency": (lambda e: e.switch_currency("USD"), lambda: BALANCE_HTML),
    "chats.mark_read": (lambda e: e.mark_chat_read(NODE), lambda: THREAD_HTML),
    "chats.send_image": (
        lambda e: e.send_image(NODE, PNG, filename="test.png"),
        lambda: THREAD_HTML,
    ),
    "chats.send_text": (lambda e: e.send_text(NODE, "test"), lambda: THREAD_HTML),
    "lots.activate": (lambda e: _lot(e, activate=True), lambda: _page(active=False)),
    "lots.deactivate": (lambda e: _lot(e), _page),
    "lots.promote": (lambda e: e.promote_lots("1", "922"), lambda: ""),
    "lots.update_price": (lambda e: _lot(e, price=True), _page),
    "orders.refund": (lambda e: e.refund_order("ZVVQ8FKP"), lambda: _order_page(refundable=True)),
    "reviews.leave": (
        lambda e: e.leave_review("ZVVQ8FKP", rating=5, text="test"),
        lambda: ORDER_HTML,
    ),
    "reviews.remove": (lambda e: e.remove_review("ZVVQ8FKP"), lambda: ORDER_HTML),
}


def _engine(monkeypatch, *, exhausted):
    monkeypatch.setattr("funora._engine.monotonic", lambda: 0.0)
    budget = Budget(names=("write",))
    if exhausted:
        budget._buckets[0].tokens = 0
    engine = Engine(
        TransportSettings(),
        budget,
        experimental=frozenset(Capability),
        unsafe_sends_without_ledger=True,
        unsafe_price_changes_without_audit=True,
    )
    engine._state.outbound.note_incoming(NODE, at_ms=int(datetime.now(UTC).timestamp() * 1000))
    return engine


def _first_post(core, html):
    reply = None
    for _ in range(5):
        request = core.send(reply)
        if isinstance(request, (Submit, Upload)):
            return request
        assert isinstance(request, Fetch), request
        reply = _observation(html, "https://funpay.com" + request.path)
    pytest.fail("запись не дошла до отправки")


def test_every_declared_write_has_an_admission_scenario():
    assert set(WRITES) == {name for name, op in OPERATIONS.items() if str(op.safety) != "safe"}


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize("exhausted", [False, True])
def test_write_quota_is_checked_before_first_mutation(monkeypatch, operation, exhausted):
    engine = _engine(monkeypatch, exhausted=exhausted)
    build, page = WRITES[operation]
    core = build(engine)
    try:
        if exhausted:
            with pytest.raises(BudgetExhaustedError, match="write"):
                _first_post(core, page())
            assert engine._budget._buckets[0].tokens == 0
        else:
            assert isinstance(_first_post(core, page()), (Submit, Upload))
            assert engine._budget._buckets[0].tokens == 59
    finally:
        core.close()


@pytest.mark.parametrize("operation", ["buyer", "lots", "chips"])
def test_safe_post_does_not_require_action_quota(monkeypatch, operation):
    engine = _engine(monkeypatch, exhausted=True)
    if operation == "buyer":
        core = engine.read_buyer_viewing(NODE, ("9310582",))
    elif operation == "lots":
        core = engine.calculate_prices(node_id="922", price="3.50")
    else:
        core = engine.calculate_prices(game_id="1", price="3.50")
    try:
        assert isinstance(_first_post(core, THREAD_HTML), Submit)
        assert engine._budget._buckets[0].tokens == 0
    finally:
        core.close()
