"""Гашение повтора обновляет LRU, в том числе после записи JSON на диск."""

import json

from test_poll import _event

from funora._poll import Deduplicator


def test_a_repeated_event_stays_protected_when_the_bucket_is_full() -> None:
    dedup = Deduplicator(entries_per_key=2)
    a, b, c = (_event(name) for name in ["a", "b", "c"])
    dedup.commit((a, b), 0.0)
    assert dedup.filter((a,), 0.1) == ()
    dedup.commit((c,), 0.2)
    assert dedup.filter((a,), 0.3) == ()
    assert dedup.filter((b,), 0.3) == (b,)


def test_lru_order_survives_sorted_json_and_equal_timestamps() -> None:
    dedup = Deduplicator(entries_per_key=2)
    a, b, c = (_event(name) for name in ["a", "b", "c"])
    dedup.commit((a, b), 0.0)
    dedup.filter((a,), 0.0)
    saved = json.loads(
        json.dumps(
            {"dedup": dedup.snapshot(0, wall_ms=1000), "order": dedup.snapshot_order()},
            sort_keys=True,
        )
    )
    restored = Deduplicator(entries_per_key=2)
    restored.restore(saved["dedup"], 0, wall_ms=1000, ordering=saved["order"])
    restored.commit((c,), 0.1)
    assert restored.filter((a,), 0.2) == ()
    assert restored.filter((b,), 0.2) == (b,)


def test_old_snapshots_restore_newest_timestamps_before_applying_the_limit() -> None:
    event = _event("a")
    dedup = Deduplicator(entries_per_key=1)
    dedup.restore({event.ordering_key: {"a": 9000, "z": 1000}}, 0, wall_ms=10000)
    assert dedup.filter((event,), 0) == ()


def test_malformed_saved_order_fails_without_discarding_dedup():
    import pytest

    from funora.errors import StateSchemaIncompatibleError

    dedup = Deduplicator()
    for ordering in ([], {"a": "b"}, {"a": [None]}, {"a": ["b", "b"]}):
        with pytest.raises(StateSchemaIncompatibleError):
            dedup.restore({}, 0, ordering=ordering)
    for state in ([], {"a": []}, {"a": {"event": "yesterday"}}):
        with pytest.raises(StateSchemaIncompatibleError):
            dedup.restore(state, 0)
