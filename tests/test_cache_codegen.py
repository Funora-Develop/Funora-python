"""Новые правила кэша и LRU не могут молча разойтись со спецификацией."""

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import codegen


@pytest.mark.parametrize("mutation", ["eviction", "ttl", "invalidation", "unknown", "retries"])
def test_unsupported_policy_is_rejected(tmp_path, mutation):
    spec = os.environ.get("FUNORA_SPEC_DIR")
    if not spec:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    shutil.copytree(Path(spec) / "spec", tmp_path / "spec")
    if mutation == "retries":
        path = tmp_path / "spec/protocol/retry-policy.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["conformance"]["suite"] = "missing"
        renderer = codegen.render_retry
    elif mutation == "eviction":
        path = tmp_path / "spec/events/delivery.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["deduplication"]["eviction"] = "newest_first"
        renderer = codegen.render_events
    else:
        path = tmp_path / "spec/services/catalog.yaml"
        doc = yaml.safe_load(path.read_text())
        cache = doc["operations"]["catalog.categories"]["cacheable"]
        if mutation == "ttl":
            cache["ttl_ms"] = 0
        elif mutation == "invalidation":
            cache["invalidate_on"] = ["session_change"]
        else:
            cache["unknown"] = True
        renderer = codegen.render_operations
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(SystemExit):
        renderer(tmp_path)
