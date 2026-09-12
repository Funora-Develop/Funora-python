"""Основание возможности не освежается чтением профиля и не переживает сброс."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest
from test_aclient import _AsyncFakeFetcher
from test_client import _FakeFetcher, _observation, _page
from test_public_transport import market_page

from funora import AsyncClient, CapabilityEvaluation, Client
from funora._budget import Budget
from funora._whoami import _CapabilityStates
from funora.capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from funora.errors import NetworkError, ProtocolChangedError, SessionExpiredError


def failed_core(error):
    if False:
        yield
    raise error


def test_profiles_do_not_probe_or_refresh_evidence():
    transport = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    with Client(transport=transport, budget=Budget(names=())) as client:
        initial = client.account.capabilities()
        assert initial.states() == CAPABILITY_INITIAL
        assert all(e.source == "static" for e in initial.evaluations().values())
        with ThreadPoolExecutor(max_workers=8) as pool:
            profiles = list(pool.map(lambda _: client.account.capabilities(), range(80)))
        assert transport.calls == 0
        assert all(p.evaluations() == initial.evaluations() for p in profiles)
        client.orders.list()
        seen = client.account.capabilities()
        evaluation = seen.evaluation_of(Capability.ORDERS_LIST)
        assert isinstance(evaluation, CapabilityEvaluation)
        assert evaluation.state is CapabilityState.SUPPORTED
        assert evaluation.source == "probe"
        assert initial.observed_at <= evaluation.evaluated_at <= seen.observed_at
        assert client.account.capabilities().evaluation_of(Capability.ORDERS_LIST) == evaluation
        assert initial.evaluation_of(Capability.ORDERS_LIST).source == "static"
        copy = seen.evaluations()
        copy.clear()
        assert seen.evaluations()
        with pytest.raises(FrozenInstanceError):
            evaluation.source = "static"
        assert transport.calls == 1


@pytest.mark.parametrize("error", [ProtocolChangedError("changed"), SessionExpiredError("expired")])
def test_failure_invalidates_only_its_lane_and_resume_restores_initials(error):
    private = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    public = _FakeFetcher([_observation(market_page())])
    with Client(transport=private, public_transport=public, budget=Budget(names=())) as client:
        client.orders.list()
        client.market.offers("123")
        before = client.account.capabilities()
        with pytest.raises(type(error)):
            client.run(failed_core(error))
        failed = client.account.capabilities()
        for cap in [Capability.ORDERS_LIST, Capability.ACCOUNT_BALANCE]:
            evaluation = failed.evaluation_of(cap)
            assert evaluation.state is CapabilityState.UNKNOWN
            assert evaluation.source == "observed_failure"
        assert failed.evaluation_of(Capability.MARKET_OFFERS) == before.evaluation_of(
            Capability.MARKET_OFFERS
        )
        assert before.state_of(Capability.ORDERS_LIST) is CapabilityState.SUPPORTED
        client.resume()
        restored = client.account.capabilities()
        assert restored.states() == CAPABILITY_INITIAL
        assert all(e.source == "static" for e in restored.evaluations().values())


def test_real_public_parser_failure_invalidates_evidence_without_affecting_private():
    private = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    public = _FakeFetcher([_observation(market_page()), _observation("<html></html>")])
    with Client(transport=private, public_transport=public, budget=Budget(names=())) as client:
        client.orders.list()
        client.market.offers("123")
        before = client.account.capabilities()
        with pytest.raises(ProtocolChangedError):
            client.market.offers("123")
        after = client.account.capabilities()
        assert after.evaluation_of(Capability.MARKET_OFFERS).source == "observed_failure"
        assert after.state_of(Capability.MARKET_SNAPSHOT) is CapabilityState.UNKNOWN
        assert after.evaluation_of(Capability.ORDERS_LIST) == before.evaluation_of(
            Capability.ORDERS_LIST
        )


def test_network_failure_does_not_relabel_prior_observation_as_fresh():
    with Client(transport=_FakeFetcher([]), budget=Budget(names=())) as client:
        before = client.account.capabilities()
        with pytest.raises(NetworkError):
            client.run(failed_core(NetworkError("offline")))
        assert client.account.capabilities().evaluations() == before.evaluations()


async def test_async_profiles_share_observations_without_probes():
    transport = _AsyncFakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    async with AsyncClient(transport=transport, budget=Budget(names=())) as client:
        profiles = await asyncio.gather(*(client.account.capabilities() for _ in range(80)))
        assert transport.calls == 0
        assert all(p.evaluations() == profiles[0].evaluations() for p in profiles)
        await client.orders.list()
        seen = await client.account.capabilities()
        assert seen.evaluation_of(Capability.ORDERS_LIST).source == "probe"
        with pytest.raises(ProtocolChangedError):
            await client.run(failed_core(ProtocolChangedError("changed")))
        failed = await client.account.capabilities()
        assert failed.evaluation_of(Capability.ORDERS_LIST).source == "observed_failure"
        assert transport.calls == 1


def test_reset_and_snapshot_do_not_mix_generations():
    states = _CapabilityStates()

    def reset_many():
        for _ in range(300):
            states.invalidate()
            states.reset()

    def read_many():
        for _ in range(300):
            values = list(states.snapshot().evaluations().values())
            assert len({v.source for v in values}) == 1
            assert len({v.evaluated_at for v in values}) == 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reset_many), *(pool.submit(read_many) for _ in range(3))]
        for future in futures:
            future.result()


def test_invalidation_preserves_contract_restrictions(monkeypatch):
    monkeypatch.setitem(
        CAPABILITY_INITIAL, Capability.CHATS_SEND_IMAGE, CapabilityState.EXPERIMENTAL
    )
    monkeypatch.setitem(CAPABILITY_INITIAL, Capability.ORDERS_REFUND, CapabilityState.UNSUPPORTED)
    states = _CapabilityStates()
    states.invalidate()
    assert states[Capability.CHATS_SEND_IMAGE] is CapabilityState.EXPERIMENTAL
    assert states[Capability.ORDERS_REFUND] is CapabilityState.UNSUPPORTED


@pytest.mark.parametrize(
    "key", ["network", "evaluation_sources", "invalidate_on", "invalidation_scope", "reset_on"]
)
def test_codegen_rejects_unimplemented_profile_rules(tmp_path, key):
    import os
    import shutil
    import sys
    from pathlib import Path

    import yaml

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import codegen

    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(spec) / "spec", tmp_path / "spec")
    path = tmp_path / "spec/capabilities.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["probe"]["profile"][key] = "unsupported"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit, match="профиля возможностей"):
        codegen.render_capabilities(tmp_path)


def test_evaluation_and_profile_match_the_contract():
    import json
    import os
    from dataclasses import fields
    from pathlib import Path
    from typing import get_args, get_type_hints

    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    models = Path(spec) / "spec/models"
    schema = json.loads((models / "capability-evaluation.schema.json").read_text())
    assert (
        set(schema["properties"])
        == set(schema["required"])
        == {f.name for f in fields(CapabilityEvaluation)}
    )
    assert set(schema["properties"]["state"]["enum"]) == {s.value for s in CapabilityState}
    assert set(schema["properties"]["source"]["enum"]) == set(
        get_args(get_type_hints(CapabilityEvaluation)["source"])
    )
    profile = json.loads((models / "capability-profile.schema.json").read_text())
    assert set(profile["required"]) == {"states", "evaluations", "observed_at"}
    assert profile["properties"]["evaluations"]["additionalProperties"]["$ref"] == (
        "capability-evaluation.schema.json"
    )
    assert set(profile["properties"]["states"]["additionalProperties"]["enum"]) == {
        s.value for s in CapabilityState
    }


def test_profile_invalidation_discards_cached_health():
    transport = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))] * 2)
    with Client(transport=transport, budget=Budget(names=())) as client:
        assert client.account.health().is_usable
        assert client.account.health().from_cache
        with pytest.raises(SessionExpiredError):
            client.run(failed_core(SessionExpiredError("expired")))
        assert not client.account.health().from_cache
        assert transport.calls == 2


async def test_async_public_evidence_is_independent():
    private = _AsyncFakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    public = _AsyncFakeFetcher([_observation(market_page()), _observation("<html></html>")])
    async with AsyncClient(
        transport=private, public_transport=public, budget=Budget(names=())
    ) as client:
        await client.orders.list()
        await client.market.offers("123")
        before = await client.account.capabilities()
        assert before.evaluation_of(Capability.MARKET_OFFERS).source == "probe"
        with pytest.raises(ProtocolChangedError):
            await client.market.offers("123")
        after = await client.account.capabilities()
        assert after.evaluation_of(Capability.ORDERS_LIST) == before.evaluation_of(
            Capability.ORDERS_LIST
        )
        assert after.evaluation_of(Capability.MARKET_OFFERS).source == "observed_failure"
        client.resume()
        restored = await client.account.capabilities()
        assert restored.states() == CAPABILITY_INITIAL


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
def test_combined_profile_is_not_dated_before_its_public_observation(monkeypatch, client_type):
    client = client_type(public_only=True, budget=Budget(names=()))
    engine_type = type(client._public_engine)
    original = engine_type.capability_profile

    def observe_between_snapshots(engine):
        if engine is client._public_engine:
            engine._state.capabilities[Capability.CHIPS_OFFERS] = CapabilityState.SUPPORTED
        return original(engine)

    monkeypatch.setattr(engine_type, "capability_profile", observe_between_snapshots)
    profile = client._capability_profile()
    assert profile.evaluation_of(Capability.CHIPS_OFFERS).evaluated_at <= profile.observed_at
    if isinstance(client, AsyncClient):
        asyncio.run(client.close())
    else:
        client.close()
