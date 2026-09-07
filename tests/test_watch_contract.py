"""Контракт повторной доставки нельзя поменять без поддержки в ядре."""

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import codegen


@pytest.mark.parametrize(
    "field",
    [
        "persist_before_handlers",
        "replay_source",
        "new_reads",
        "acknowledgement",
        "failed_ordering_key",
        "state_owner",
        "unknown",
        "missing",
    ],
)
def test_pending_delivery_contract_is_enforced(tmp_path: Path, field: str) -> None:
    source = os.environ.get("FUNORA_SPEC_DIR")
    if not source:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(source) / "spec", tmp_path / "spec")
    path = tmp_path / "spec/events/delivery.yaml"
    doc = yaml.safe_load(path.read_text())
    if field == "missing":
        del doc["pending_delivery"]
    else:
        doc["pending_delivery"][field] = False
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit, match="непринятой партии"):
        codegen.render_events(tmp_path)
