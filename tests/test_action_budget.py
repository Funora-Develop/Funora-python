"""Запись учитывается действием, HTTP-запросы продолжают учитываться отдельно."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_send_image import NODE, PNG, _engine
from test_send_image import _Scripted as ImageScript
from test_update_price import NODE as LOT_NODE
from test_update_price import OFFER, WHEN, _page

from funora._budget import Budget
from funora._engine import Engine, Submit
from funora._identity import Identity
from funora._lot_form import parse_lot_form
from funora._transport import TransportSettings
from funora.capabilities import Capability
from funora.errors import BudgetExhaustedError, TransportError, ValidationError


def _write(budget):
    return next(bucket for bucket in budget._buckets if bucket.limits.name == "write")


def test_reading_does_not_touch_exhausted_action_quota():
    budget = Budget()
    _write(budget).tokens = 0
    assert budget.reserve(0).granted
    assert not budget.reserve(0, action=True).granted
    assert _write(budget).tokens == 0


@pytest.mark.parametrize("empty", ["host", "account", "write"])
def test_refusal_does_not_spend_other_quotas(empty):
    budget = Budget()
    next(b for b in budget._buckets if b.limits.name == empty).tokens = 0
    before = [b.tokens for b in budget._buckets]
    assert not budget.reserve(0, action=True).granted
    assert [b.tokens for b in budget._buckets] == before


def test_action_cost_is_one_even_when_request_cost_differs():
    budget = Budget()
    assert budget.reserve(0, cost=3, action=True).granted
    assert [b.tokens for b in budget._buckets] == [57, 27, 59]
    assert budget.reserve(0, cost=0, action=True).granted
    assert [b.tokens for b in budget._buckets] == [57, 27, 58]


def test_parallel_accounts_share_one_action_burst():
    budget = Budget(names=("write",))
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(
                lambda n: budget.for_account(str(n)).reserve(0, action=True).granted, range(40)
            )
        )
    assert sum(results) == 5
    assert _write(budget).tokens == 55


def test_image_upload_and_message_count_as_one_action(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("funora._engine.monotonic", lambda: 1.0)
    script = ImageScript()
    script.run(engine.send_image(NODE, PNG, filename="test.png"))
    assert len(script.uploads) == len(script.submits) == 1
    assert _write(engine._budget).tokens == 59
    # GET страницы, Upload файла, Submit сообщения.
    assert engine._budget._buckets[0].tokens == 57


def test_exhausted_action_quota_blocks_upload(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("funora._engine.monotonic", lambda: 0.0)
    _write(engine._budget).tokens = 0
    script = ImageScript()
    with pytest.raises(BudgetExhaustedError, match="write"):
        script.run(engine.send_image(NODE, PNG, filename="test.png"))
    assert len(script.fetches) == 1
    assert script.uploads == script.submits == []


def test_invalid_image_does_not_consume_an_action():
    engine = _engine()
    with pytest.raises(ValidationError):
        next(engine.send_image(NODE, b"", filename="test.png"))
    assert _write(engine._budget).tokens == 60


@pytest.mark.parametrize("visible", [True, False])
def test_noop_visibility_does_not_consume_an_action(monkeypatch, visible):
    engine = Engine(TransportSettings(), Budget(), experimental=frozenset(Capability))
    form = parse_lot_form(_page(active=visible), observed_at=WHEN)

    def read(*args):
        yield from ()
        return form

    monkeypatch.setattr(Engine, "read_lot_form", read)
    core = engine.set_lot_visible(LOT_NODE, OFFER, visible=visible, expected_revision=form.revision)
    with pytest.raises(StopIteration):
        next(core)
    assert _write(engine._budget).tokens == 60


@pytest.mark.parametrize("operation", ["price", "activate", "deactivate"])
def test_lot_save_reserves_request_and_action_before_submit(monkeypatch, operation):
    budget = Budget()
    engine = Engine(
        TransportSettings(),
        budget,
        experimental=frozenset(Capability),
        unsafe_price_changes_without_audit=True,
    )
    form = parse_lot_form(_page(active=operation != "activate"), observed_at=WHEN)

    def read(*args):
        yield from ()
        return form

    monkeypatch.setattr(Engine, "read_lot_form", read)
    monkeypatch.setattr("funora._engine.monotonic", lambda: 0.0)
    core = (
        engine.update_price(LOT_NODE, OFFER, "3.50", expected_revision=form.revision)
        if operation == "price"
        else engine.set_lot_visible(
            LOT_NODE, OFFER, visible=operation == "activate", expected_revision=form.revision
        )
    )
    try:
        assert isinstance(next(core), Submit)
        assert [b.tokens for b in budget._buckets] == [59, 29, 59]
    finally:
        core.close()


def test_rate_limit_reduces_action_quota_for_all_account_views():
    budget = Budget()
    first, second = budget.for_account("a"), budget.for_account("b")
    Identity("action-test", budget=budget).note_limit(0)
    assert _write(first) is _write(second)
    assert _write(first).tokens == 30
    assert _write(first).factor == 0.5


def test_uncertain_transport_failure_does_not_return_the_action(monkeypatch):
    monkeypatch.setattr("funora._engine.monotonic", lambda: 0.0)
    engine = Engine(TransportSettings(), Budget())
    core = engine.promote_lots("1", "922")
    assert isinstance(next(core), Submit)
    with pytest.raises(TransportError):
        core.throw(TransportError("ответ потерян"))
    assert _write(engine._budget).tokens == 59


def test_unchanged_price_does_not_save_or_consume_an_action(monkeypatch):
    engine = Engine(TransportSettings(), Budget(), unsafe_price_changes_without_audit=True)
    form = parse_lot_form(_page(), observed_at=WHEN)

    def read(*args):
        yield from ()
        return form

    monkeypatch.setattr(Engine, "read_lot_form", read)
    core = engine.update_price(LOT_NODE, OFFER, form.price_text, expected_revision=form.revision)
    with pytest.raises(StopIteration) as done:
        next(core)
    assert done.value.value is form
    assert _write(engine._budget).tokens == 60
