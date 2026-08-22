"""Проверки сохранения состояния между запусками.

Спецификация требует, чтобы кэш гашения повторов переживал перезапуск. Кэш
только в памяти означает, что после любого перезапуска повторно приходит всё, что
успело прийти до него, - для обработчика, выдающего товар, это выданный дважды
товар при каждом перезапуске процесса.

Проверяется поэтому не столько запись и чтение, сколько три способа испортить всё
незаметно: обрезанный файл, чужой формат, истёкшие записи.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from funora._diff import Event
from funora._poll import Deduplicator
from funora._state import ADAPTER_FAMILY, STATE_FORMAT, StateFile
from funora.errors import StateSchemaIncompatibleError
from funora.events import EventType


def _event(event_id: str, key: str = "chat:1") -> Event:
    """Собирает событие для проверок.

    Args:
        event_id (str): Идентификатор события.
        key (str): Ключ упорядочивания.

    Returns:
        Event: Событие.
    """
    return Event(
        id=event_id,
        type=EventType.MESSAGE_CREATED,
        account_id="12345678",
        ordering_key=key,
        entity_id="1",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
        origin="structural",
        payload={},
    )


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Проверяет, что отсутствие файла не считается отказом.

    Первый запуск - штатное событие. Требовать файл значило бы требовать
    создать его вручную.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    assert StateFile(tmp_path / "нет-такого.json").load() == {}


def test_roundtrip(tmp_path: Path) -> None:
    """Проверяет запись и чтение состояния.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state = StateFile(tmp_path / "state.json")
    state.save({"dedup": {"chat:1": {"a": 1.0}}})
    assert state.load() == {"dedup": {"chat:1": {"a": 1.0}}}


def test_write_is_atomic(tmp_path: Path) -> None:
    """Проверяет, что после записи не остаётся временных файлов.

    Файл собирается рядом и переименовывается поверх. Дописывание на месте
    оставило бы обрезанный файл при убийстве процесса посреди записи, и
    следующий запуск не смог бы его прочитать - то есть перезапуск в самый
    неудачный момент отменил бы всю защиту.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state = StateFile(tmp_path / "state.json")
    state.save({"dedup": {}})
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_truncated_file_is_loud(tmp_path: Path) -> None:
    """Проверяет, что обрезанный файл не читается молча.

    Молчаливый старт с нуля здесь неотличим от штатной работы и приводит к
    повторной обработке всего, что уже обработано.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    path = tmp_path / "state.json"
    path.write_text('{"format": "funora-state-v1", "payl', encoding="utf-8")

    with pytest.raises(StateSchemaIncompatibleError):
        StateFile(path).load()


def test_foreign_format_is_loud(tmp_path: Path) -> None:
    """Проверяет отказ на файле чужой версии формата.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"format": "funora-state-v99", "adapter_family": ADAPTER_FAMILY, "payload": {}}),
        encoding="utf-8",
    )

    with pytest.raises(StateSchemaIncompatibleError) as exc:
        StateFile(path).load()
    assert STATE_FORMAT in str(exc.value)


def test_foreign_adapter_family_is_loud(tmp_path: Path) -> None:
    """Проверяет отказ на состоянии от другой площадки.

    Совпадение идентификаторов было бы случайным, а последствия - молчаливым
    гашением чужих событий.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"format": STATE_FORMAT, "adapter_family": "другая-площадка", "payload": {}}),
        encoding="utf-8",
    )

    with pytest.raises(StateSchemaIncompatibleError):
        StateFile(path).load()


def test_dedup_survives_a_restart(tmp_path: Path) -> None:
    """Проверяет главное свойство: повтор гасится после перезапуска.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state = StateFile(tmp_path / "state.json")

    first = Deduplicator()
    fresh = first.filter((_event("a"),), 0.0)
    first.commit(fresh, 0.0)
    state.save({"dedup": first.snapshot()})

    second = Deduplicator()
    assert second.restore(state.load()["dedup"], 1.0) == 1
    assert len(second.filter((_event("a"),), 1.0)) == 0


def test_uncommitted_event_survives_a_restart_as_fresh(tmp_path: Path) -> None:
    """Проверяет, что непринятое событие приходит снова и после перезапуска.

    Событие, на котором обработчик упал, не фиксируется. Перезапуск не должен
    это менять: иначе достаточно перезапустить процесс, чтобы потерять именно
    то событие, которое обработчику не далось.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state = StateFile(tmp_path / "state.json")

    first = Deduplicator()
    first.filter((_event("a"),), 0.0)
    state.save({"dedup": first.snapshot()})

    second = Deduplicator()
    second.restore(state.load().get("dedup", {}), 1.0)
    assert len(second.filter((_event("a"),), 1.0)) == 1


def test_expired_records_are_dropped_on_restore() -> None:
    """Проверяет отбрасывание истёкших записей при восстановлении.

    Иначе после длинной паузы восстановился бы кэш, который всё равно нельзя
    использовать, и память тратилась бы на записи, гасящие уже ничего.

    Returns:
        None
    """
    source = Deduplicator(ttl_ms=1000)
    source.commit(source.filter((_event("a"),), 0.0), 0.0)

    target = Deduplicator(ttl_ms=1000)
    assert target.restore(source.snapshot(), 10.0) == 0
    assert len(target.filter((_event("a"),), 10.0)) == 1


def test_restore_respects_the_per_key_bound() -> None:
    """Проверяет, что восстановление не раздувает кэш сверх предела.

    Файл могла записать версия с другим пределом, и доверять ему нельзя.

    Returns:
        None
    """
    oversized = {"chat:1": {f"e{i}": float(i) for i in range(100)}}

    target = Deduplicator(entries_per_key=4)
    assert target.restore(oversized, 100.0) == 4
