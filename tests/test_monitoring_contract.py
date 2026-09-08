"""Локальный runtime объявлен отдельно от HTTP-операций и следует политике."""

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import codegen


@pytest.fixture
def spec(tmp_path):
    source = os.environ.get("FUNORA_SPEC_DIR")
    if not source:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(source) / "spec", tmp_path / "spec")
    return tmp_path


@pytest.mark.parametrize(
    "path,value",
    [
        ("admission_control.enabled", False),
        ("admission_control.enabled", 1),
        ("admission_control.cost_source", "constant"),
        ("admission_control.dry_run", "absent"),
        ("admission_control.run", "absent"),
        ("admission_control.forecast_share", "all"),
        ("admission_control.lifetime", "forever"),
        ("market_watch.revision", "time"),
        ("market_watch.incomplete", "forget"),
        ("market_watch.cold_start", "emit_all"),
        ("market_watch.default_interval_ms", 0),
        ("market_watch.consecutive_absences", True),
    ],
)
def test_monitoring_policy_cannot_change_silently(spec, path, value):
    file = spec / "spec/runtime/budget.yaml"
    doc = yaml.safe_load(file.read_text(encoding="utf-8"))
    group, field = path.split(".")
    doc[group][field] = value
    file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(SystemExit):
        codegen.render_budget(spec)


@pytest.mark.parametrize("cost", [None, 0, True, 1.0, 2])
def test_snapshot_cost_matches_single_page_adapter(spec, cost):
    file = spec / "spec/services/market.yaml"
    doc = yaml.safe_load(file.read_text(encoding="utf-8"))
    entry = doc["operations"]["market.snapshot"]
    if cost is None:
        del entry["cost_hint"]
    else:
        entry["cost_hint"] = cost
    file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(SystemExit):
        codegen.render_operations(spec)


def test_runtime_api_is_declared_and_available_in_both_facades(spec):
    from funora._aclient import AsyncMonitoring
    from funora._client import Monitoring

    doc = yaml.safe_load((spec / "spec/runtime/budget.yaml").read_text(encoding="utf-8"))[
        "admission_control"
    ]
    declared = {doc["dry_run"], doc["run"]}
    for cls in (Monitoring, AsyncMonitoring):
        assert {"monitoring." + name for name in vars(cls) if not name.startswith("_")} == declared
