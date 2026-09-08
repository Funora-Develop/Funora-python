"""Протокол проверяется в процессе с независимыми ожидаемыми векторами."""

import io
import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from funora import conformance as adapter
from funora.capabilities import CAPABILITY_INITIAL, Capability
from funora.errors import ValidationError


@pytest.fixture
def spec():
    root = Path(os.environ.get("FUNORA_SPEC_DIR", "")) / "spec/conformance"
    if not (root / "canonical-form.vectors.json").is_file():
        pytest.skip("FUNORA_SPEC_DIR недоступен")
    return root


@pytest.mark.parametrize("kind", ["serialize", "fingerprint"])
@pytest.mark.parametrize("bucket", ["accept", "reject"])
def test_canonical_protocol_returns_computed_values(spec, kind, bucket):
    vectors = json.loads((spec / "canonical-form.vectors.json").read_text(encoding="utf-8"))
    for index, vector in enumerate(vectors[kind][bucket]):
        message = {
            "id": vector["name"],
            "kind": kind if bucket == "accept" else kind + "_refuses",
            "vector": f"{kind}.{bucket}[{index}]",
        }
        result = adapter.answer(message)
        expected = vector.get("expected")
        if bucket == "accept" and expected is None:
            expected = next(
                one["expected"] for one in vectors[kind][bucket] if one["name"] == vector["same_as"]
            )
        assert result == {
            "id": message["id"],
            "outcome": "pass",
            "value": expected if bucket == "accept" else vector["refuses_with"],
        }


@pytest.mark.parametrize(
    "kind,file,result_key",
    [
        ("rate_budget", "rate-budget.vectors.json", "sent"),
        ("outbound_governor", "outbound-governor.vectors.json", "decisions"),
        ("resume", "resume.vectors.json", "steps"),
    ],
)
def test_scenario_protocol_preserves_all_results(spec, kind, file, result_key):
    vectors = json.loads((spec / file).read_text(encoding="utf-8"))
    for index, vector in enumerate(vectors["scenarios"]):
        result = adapter.answer(
            {"id": vector["name"], "kind": kind, "vector": f"scenarios[{index}]"}
        )
        if vector.get("concurrent"):
            assert result["outcome"] == "skip"
            assert result["not_implemented"] == vector["requires"]
        else:
            assert result["outcome"] == "pass", result
            expected = vector["expected"]
            if kind == "rate_budget":
                sent = result["sent"]
                if "refused" in expected:
                    assert [index for index, at in enumerate(sent) if at is None] == expected[
                        "refused"
                    ]
                for index, at in expected.get("at", {}).items():
                    assert sent[int(index)] == at, vector["name"]
                if "total_sent_at_most" in expected:
                    assert sum(at is not None for at in sent) <= expected["total_sent_at_most"]
            else:
                assert result[result_key] == expected, vector["name"]


def test_capability_protocol_covers_every_initial_state_and_decision(spec):
    vectors = json.loads((spec / "capabilities.vectors.json").read_text(encoding="utf-8"))
    for capability in Capability:
        initial = adapter.answer({"kind": "capability_initial", "capability": capability.value})
        assert initial["outcome"] == "pass", initial
        assert initial["value"] == CAPABILITY_INITIAL[capability].value
        for decision in vectors["decision"]["rows"]:
            result = adapter.answer(
                {
                    "kind": "capability_decision",
                    "capability": capability.value,
                    "state": decision["state"],
                    "opted_in": decision["opted_in"],
                }
            )
            assert result["outcome"] == "pass", result
            assert result["value"] == ("разрешено" if decision["allowed"] else decision["error"])


def test_missing_spec_and_incompatible_protocol_fail_explicitly(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNORA_SPEC_DIR", raising=False)
    case = {"kind": "serialize", "vector": "serialize.accept[0]"}
    assert "ConfigurationError" in adapter.answer(case)["detail"]
    root = tmp_path / "spec/conformance"
    root.mkdir(parents=True)
    (root / "canonical-form.vectors.json").write_text(json.dumps({"runner_protocol": 999}))
    monkeypatch.setenv("FUNORA_SPEC_DIR", str(tmp_path))
    result = adapter.answer(case)
    assert result["outcome"] == "fail" and "ValidationError" in result["detail"]


@pytest.mark.parametrize(
    "case",
    [
        {"kind": "serialize", "vector": "serialize.accept[999999]"},
        {"kind": "serialize"},
        {"kind": "capability_initial", "capability": "future.capability"},
        {"kind": "capability_decision", "capability": "orders.list", "state": "future-state"},
        {"kind": "serialize_refuses", "vector": "serialize.accept[0]"},
        {"kind": "fingerprint_refuses", "vector": "fingerprint.accept[0]"},
    ],
)
def test_invalid_or_mislabelled_case_never_passes(spec, case):
    result = adapter.answer(case)
    assert result["outcome"] == "fail"
    assert result["detail"]


def test_cli_uses_utf8_and_one_result_per_nonempty_line(monkeypatch):
    cases = [{"id": "первый", "kind": "unknown"}, {"id": "второй", "kind": "unknown"}]
    incoming = io.BytesIO(
        (
            "\n" + "\n \n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n"
        ).encode()
    )
    outgoing = io.BytesIO()
    stdin = io.TextIOWrapper(incoming, encoding="ascii")
    stdout = io.TextIOWrapper(outgoing, encoding="ascii")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdin", stdin)
        patch.setattr(sys, "stdout", stdout)
        with (
            pytest.warns(RuntimeWarning, match="found in sys.modules"),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_module("funora.conformance", run_name="__main__")
        assert raised.value.code == 0
        stdout.flush()
        result = [json.loads(line) for line in outgoing.getvalue().decode("utf-8").splitlines()]
    assert [one["id"] for one in result] == [one["id"] for one in cases]
    assert all(one["outcome"] == "fail" for one in result)


def test_retry_protocol_computes_attempts_and_waits_from_vectors(spec):
    vectors = json.loads((spec / "retries.vectors.json").read_text(encoding="utf-8"))
    for index, vector in enumerate(vectors["scenarios"]):
        result = adapter.answer({"kind": "retries", "vector": f"scenarios[{index}]"})
        assert result["outcome"] == "pass", result
        assert result["requests"] == len(vector["responses"])
        assert len(result["retry_trace"]) == len(vector["expected"])
        for got, expected in zip(result["retry_trace"], vector["expected"], strict=True):
            lower, upper = expected["delay_ms"]
            assert lower <= got["delay_ms"] <= upper
            assert {k: v for k, v in got.items() if k != "delay_ms"} == {
                k: v for k, v in expected.items() if k != "delay_ms"
            }
        assert result["waited_ms"] >= sum(one["delay_ms"] for one in result["retry_trace"])


@pytest.mark.parametrize("reference", ["bad", "serialize.accept[-1]", "scenarios[0]"])
def test_malformed_vector_reference_is_refused(reference):
    result = adapter.answer({"kind": "serialize", "vector": reference})
    assert result["outcome"] == "fail" and "ValidationError" in result["detail"]


@pytest.mark.parametrize(
    "worker,scenario,error_type,error",
    [
        (
            adapter._run_scenario,
            {"ttl_ms": 1, "uptime_s": 0, "steps": [{"restart": {}}]},
            ValidationError,
            "перезапуска",
        ),
        (adapter._run_trace, {"requests": [{"action": "false"}]}, ValueError, "boolean"),
        (adapter._run_outbound, {"events": [{"kind": "future"}]}, ValueError, "неизвестное"),
        (adapter._run_retries, {"responses": []}, ValidationError, "пределами"),
    ],
)
def test_broken_scenario_cannot_become_success(worker, scenario, error_type, error):
    with pytest.raises(error_type, match=error):
        worker(scenario)


@pytest.mark.parametrize("failure", ["request", "unrecorded-error", "foreign-error"])
def test_retry_harness_reports_driver_faults(monkeypatch, failure):
    from funora import _engine, errors

    closed = []

    def wrong_core(self, *args, **kwargs):
        try:
            if failure == "unrecorded-error":
                raise errors.ValidationError("unrecorded failure")
            yield _engine.Submit("/", {}, {})
        finally:
            closed.append(True)

    if failure == "foreign-error":
        monkeypatch.setitem(errors.ERROR_BY_STABLE_ID, "bad.error", ValueError)
        expected = errors.ValidationError
        scenario = {"responses": [{"error": "bad.error"}]}
    else:
        monkeypatch.setattr(_engine.Engine, "fetch_ok", wrong_core)
        expected = TypeError if failure == "request" else errors.ValidationError
        scenario = {"responses": []}
    with pytest.raises(expected):
        adapter._run_retries(scenario)
    if failure != "foreign-error":
        assert closed == [True]


@pytest.mark.parametrize("state", ["unsupported", "experimental"])
def test_initial_capability_reports_declared_refusal_state(monkeypatch, state):
    from funora import _gate
    from funora.capabilities import CapabilityState

    capability = Capability.ORDERS_LIST
    monkeypatch.setitem(_gate.CAPABILITY_INITIAL, capability, CapabilityState(state))
    result = adapter.answer({"kind": "capability_initial", "capability": capability.value})
    assert result["outcome"] == "pass" and result["value"] == state
