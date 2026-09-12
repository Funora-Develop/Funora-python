"""Проверки наблюдаемой трассы настоящего цикла чтения."""

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

from funora._budget import Budget
from funora._identity import Identity
from funora.conformance import _run_retries


def test_retry_header_cannot_bypass_total_cooldown_cap():
    result = _run_retries({"responses": [{"status": 429, "retry_after_ms": 900000}, {}]})
    assert result["requests"] == 2
    assert 300000 <= result["waited_ms"] <= 300001


def test_zero_is_a_valid_window_start():
    identity = Identity("clock-zero", budget=Budget(names=()))
    identity.note_limit(0)
    identity.note_limit(1)
    assert identity.limits_seen == 2


def test_runner_rejects_invalid_retry_delay_and_missing_wait(tmp_path):
    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    fake = tmp_path / "fake.py"
    for mutation in ["result['retry_trace'][0]['delay_ms'] = -1", "result['waited_ms'] = -1"]:
        fake.write_text(
            "import json, sys\nfrom funora.conformance import answer\n"
            "for line in sys.stdin:\n"
            "    case=json.loads(line)\n    result=answer(case)\n"
            "    if case.get('kind') == 'retries':\n        " + mutation + "\n"
            "    print(json.dumps(result))\n"
        )
        run = subprocess.run(
            ["node", str(Path(spec) / "scripts/conformance.js"), f"{sys.executable} {fake}"],
            capture_output=True,
            text=True,
        )
        assert run.returncode != 0
        assert "retries/" in run.stdout + run.stderr


def test_expected_answers_are_ignored_by_the_adapter():
    scenario = {"responses": [{"error": "funora.transport.timeout"}, {}], "random": 0.5}
    before = _run_retries(scenario)
    mutated = copy.deepcopy(scenario)
    mutated["expected"] = [{"delay_ms": [99999, 99999]}]
    assert _run_retries(mutated) == before


@pytest.mark.parametrize("mutation", ["missing_vectors", "renamed_suite"])
def test_runner_requires_the_declared_retry_suite(tmp_path, mutation):
    import shutil

    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    root = Path(spec)
    for name in ("scripts", "spec"):
        shutil.copytree(root / name, tmp_path / name)
    (tmp_path / "node_modules").symlink_to(root / "node_modules", target_is_directory=True)
    if mutation == "missing_vectors":
        (tmp_path / "spec/conformance/retries.vectors.json").unlink()
    else:
        path = tmp_path / "spec/protocol/retry-policy.yaml"
        path.write_text(path.read_text().replace("suite: retries", "suite: missing"))
    run = subprocess.run(
        [
            "node",
            str(tmp_path / "scripts/conformance.js"),
            f"{sys.executable} -m funora.conformance",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 2
    assert "retries" in run.stderr
