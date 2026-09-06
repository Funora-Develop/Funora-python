"""Контракт не может незаметно отключить защиту операций в существующем ядре."""

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import codegen


@pytest.mark.parametrize(
    "service, operation, field, value",
    [
        ("market", "market.offers", "transport_lane", None),
        ("chips", "chips.offers", "transport_lane", None),
        ("market", "market.snapshot", "transport_lane", "authenticated"),
        ("market", "market.offers", "transport_lane", "unknown"),
        ("market", "market.snapshot", "safety", "unsafe"),
        ("market", "market.snapshot", "completeness_required", False),
        ("market", "market.snapshot", "completeness_required", None),
        ("chats", "chats.send_text", "transport_lane", "public_read"),
        ("chats", "chats.send_text", "governor", None),
        ("chats", "chats.send_text", "governor", "unknown"),
        ("chats", "chats.send_image", "governor", None),
        ("chats", "chats.send_image", "governor", "unknown"),
        ("market", "market.offers", "governor", "outbound_message"),
    ],
)
def test_incompatible_operation_policy_is_rejected(tmp_path, service, operation, field, value):
    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(spec) / "spec", tmp_path / "spec")
    path = tmp_path / "spec/services" / f"{service}.yaml"
    doc = yaml.safe_load(path.read_text())
    body = doc["operations"][operation]
    if value is None:
        body.pop(field)
    else:
        body[field] = value
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit):
        codegen.render_operations(tmp_path)


@pytest.mark.parametrize(
    "bucket, unit", [("write", None), ("write", "requests"), ("host", "actions_per_hour")]
)
def test_wrong_budget_unit_is_rejected(tmp_path, bucket, unit):
    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(spec) / "spec", tmp_path / "spec")
    path = tmp_path / "spec/runtime/budget.yaml"
    doc = yaml.safe_load(path.read_text())
    if unit is None:
        doc["buckets"][bucket].pop("unit")
    else:
        doc["buckets"][bucket]["unit"] = unit
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit):
        codegen.render_budget(tmp_path)
