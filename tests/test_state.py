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
from funora.errors import CursorIncompatibleError, StateSchemaIncompatibleError
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

    Класс ошибки именно CursorIncompatibleError, и это не придирка к номеру.
    Спецификация делит два случая по тому, что делать: чужая версия схемы файла
    лечится выходом новой версии SDK, чужое семейство - никогда. Курсор, снятый
    с другой площадки, совместимым не станет.

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

    with pytest.raises(CursorIncompatibleError):
        StateFile(path).load()


#: Момент по стенным часам, от которого считают проверки гашения.
#:
#: Фиксирован намеренно. Метки гашения уходят в файл моментами от эпохи, и
#: проверка, берущая настоящие часы, зависела бы от машины - то есть проверяла
#: бы и часы тоже.
WALL: int = 1_700_000_000_000

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
    state.save({"dedup": first.snapshot(0.0, wall_ms=WALL)})

    # Показание секундомера у второго запуска СВОЁ и с первым не связано.
    # Прежде связь предполагалась, и на ней всё и ломалось.
    second = Deduplicator()
    assert second.restore(state.load()["dedup"], 9_999.0, wall_ms=WALL + 1_000) == 1
    assert len(second.filter((_event("a"),), 9_999.0)) == 0


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
    state.save({"dedup": first.snapshot(0.0, wall_ms=WALL)})

    second = Deduplicator()
    second.restore(state.load().get("dedup", {}), 9_999.0, wall_ms=WALL + 1_000)
    assert len(second.filter((_event("a"),), 9_999.0)) == 1


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
    saved = source.snapshot(0.0, wall_ms=WALL)
    assert target.restore(saved, 10.0, wall_ms=WALL + 10_000) == 0
    assert len(target.filter((_event("a"),), 10.0)) == 1


def test_restore_respects_the_per_key_bound() -> None:
    """Проверяет, что восстановление не раздувает кэш сверх предела.

    Файл могла записать версия с другим пределом, и доверять ему нельзя.

    Returns:
        None
    """
    oversized = {"chat:1": {f"e{i}": WALL + i for i in range(100)}}

    target = Deduplicator(entries_per_key=4)
    assert target.restore(oversized, 100.0, wall_ms=WALL + 100) == 4


def test_dedup_ignores_the_uptime_of_the_machine() -> None:
    """Проверяет, что гашение переживает перезапуск при любом аптайме.

    Метки хранились монотонными секундами - часами, у которых начало отсчёта
    своё в каждом запуске. После перезапуска сохранённая метка означала не то,
    что значила при записи, и исходов было два, оба плохие.

    Машину перезагрузили: показание секундомера малое, разность с меткой
    отрицательная, запись не истекала НИКОГДА - срок в час не работал вовсе.

    Машина работает давно: показание огромное, и весь кэш выбрасывался разом
    на первом же чтении. Это хуже. Гашение сохраняется затем, чтобы пережить
    перезапуск, а оно ровно перезапуска и не переживало: всё доставленное
    приходило заново. Для обработчика выдачи - выданный дважды товар.

    Returns:
        None
    """
    source = Deduplicator(ttl_ms=3_600_000)
    source.commit(source.filter((_event("a"),), 72_000.0), 72_000.0)
    saved = source.snapshot(72_000.0, wall_ms=WALL)

    # Прошла минута по стенным часам. Показание секундомера - какое угодно.
    for uptime in (0.5, 5.0, 72_010.0, 864_000.0):
        target = Deduplicator(ttl_ms=3_600_000)
        assert target.restore(saved, uptime, wall_ms=WALL + 60_000) == 1, (
            f"при показании секундомера {uptime} запись пропала"
        )
        assert len(target.filter((_event("a"),), uptime)) == 0, (
            f"при показании секундомера {uptime} повтор не погашен"
        )


def test_dedup_expires_by_the_wall_clock() -> None:
    """Проверяет обратную половину: срок вправду выходит.

    Приёмник, который держит записи всегда, неотличим от неработающего срока -
    и это ровно то, чем прежняя редакция была на перезагруженной машине.

    Returns:
        None
    """
    source = Deduplicator(ttl_ms=3_600_000)
    source.commit(source.filter((_event("a"),), 72_000.0), 72_000.0)
    saved = source.snapshot(72_000.0, wall_ms=WALL)

    # Прошло два часа по стенным часам при том же показании секундомера.
    target = Deduplicator(ttl_ms=3_600_000)
    assert target.restore(saved, 72_000.0, wall_ms=WALL + 7_200_000) == 0


def test_dedup_stamps_are_whole_numbers() -> None:
    """Проверяет, что в файл уходят целые числа, а не дробные.

    Каноническая форма запрещает числа с плавающей точкой (правило 8), а файл
    состояния объявляет себя канонической формой. Прежде туда ложились
    монотонные секунды - дробные по определению.

    Returns:
        None
    """
    source = Deduplicator()
    source.commit(source.filter((_event("a"),), 0.25), 0.25)
    saved = source.snapshot(0.25, wall_ms=WALL)

    stamps = [stamp for bucket in saved.values() for stamp in bucket.values()]
    assert stamps, "снимок пуст"
    for stamp in stamps:
        assert isinstance(stamp, int) and not isinstance(stamp, bool), (
            f"метка {stamp!r} не целое число"
        )


def test_state_remembers_the_canonical_form(tmp_path: Path) -> None:
    """Проверяет, что версия канонической формы попадает в файл.

    Она меняется отдельно от версии спецификации: одна и та же модель может
    сериализоваться по-новому, и это ломает сохранённые отпечатки. Файл, не
    помнящий её, нельзя проверить на пригодность - можно только надеяться.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    import json

    from funora.contract import CANONICAL_FORM_VERSION

    path = tmp_path / "state.json"
    StateFile(path).save({"что-нибудь": 1})

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["canonical_form_version"] == CANONICAL_FORM_VERSION


def test_state_of_another_canonical_form_is_refused(tmp_path: Path) -> None:
    """Проверяет отказ на файле, записанном другой канонической формой.

    Сохранённые отпечатки собраны по другим правилам и не совпадут ни с чем.
    Молчаливое принятие такого файла означает, что гашение повторов не сработает
    ни разу, - и заметить это можно только по повторно выданному товару.

    Args:
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    import json

    from funora.contract import CANONICAL_FORM_VERSION

    path = tmp_path / "state.json"
    StateFile(path).save({"что-нибудь": 1})

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["canonical_form_version"] = CANONICAL_FORM_VERSION + 1
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CursorIncompatibleError, match="канонической формой"):
        StateFile(path).load()
