"""Проверки журнала правок цены.

ЗАЧЕМ ОН ВООБЩЕ. Контракт требует у lots.update_price аудита before_state, и
это единственная операция, которой аудит предписан. Довод простой: у площадки
нет ни истории цен, ни отката, и «как было» знать больше некому.

Отсюда три главные проверки набора, и каждая проверяет отказ, а не удобство:
без долговечного журнала правка ОТКАЗЫВАЕТ; запись ложится ВПЕРЕДИ отправки; и
первая запись о лоте не вытесняется пределом никогда.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_update_price import NODE, OFFER, _observation, _page, _revision

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit
from funora._price_audit import (
    DEFAULT_JOURNAL_LIMIT,
    UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT,
    PriceAudit,
    PriceChange,
)
from funora._state import StateFile
from funora._transport import TransportSettings
from funora.errors import ConfigurationError


def _change(offer_id: str, *, price_after: str = "100", at_ms: int = 1) -> PriceChange:
    """Собирает запись о правке.

    Аргументы:
        offer_id (str): предложение.
        price_after (str): новая цена.
        at_ms (int): момент записи.

    Возвращает:
        PriceChange: запись.
    """
    return PriceChange(
        offer_id=offer_id,
        node_id=NODE,
        price_before="50",
        price_after=price_after,
        revision_before="deadbeefdeadbeef",
        at_ms=at_ms,
    )


def test_a_price_change_is_refused_without_a_durable_journal() -> None:
    """Требует отказа правки цены без файла состояния.

    Журнал в памяти процесса - не аудит: перезапуск стирает единственную запись
    о том, какая цена стояла до бота.

    Возвращает:
        None
    """
    engine = Engine(TransportSettings(), Budget())
    core = engine.update_price(NODE, OFFER, "99", expected_revision=_revision())

    with pytest.raises(ConfigurationError) as raised:
        core.send(None)

    assert "unsafe_price_changes_without_audit" in str(raised.value), (
        "отказ обязан назвать послабление: иначе выход из него ищут наугад"
    )


def test_the_refusal_costs_no_request() -> None:
    """Требует, чтобы отказ наступал ДО чтения формы.

    Настройка клиента от страницы не зависит, и ходить за ней ради заведомого
    отказа значит тратить чужой запрос.

    Возвращает:
        None
    """
    engine = Engine(TransportSettings(), Budget())
    core = engine.update_price(NODE, OFFER, "99", expected_revision=_revision())

    with pytest.raises(ConfigurationError):
        core.send(None)

    # Ни одной просьбы ядро выдать не успело: исключение вылетело на первом же
    # send, до всякого yield.


def test_the_state_file_lifts_the_refusal(tmp_path: Path) -> None:
    """Требует, чтобы файл состояния разрешал правку без всяких послаблений.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    engine = Engine(TransportSettings(), Budget(), state_path=tmp_path / "state.json")
    assert engine.price_audit.durable
    assert UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT not in frozenset(engine._unsafe)


def test_the_relief_leaves_a_mark() -> None:
    """Требует отметки в состоянии здоровья при снятой защите.

    Снять защиту можно, снять её незаметно нельзя.

    Возвращает:
        None
    """
    engine = Engine(TransportSettings(), Budget(), unsafe_price_changes_without_audit=True)
    assert engine.price_audit.durable
    assert UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT in frozenset(engine._unsafe)


def test_the_relief_leaves_no_mark_when_the_file_is_there(tmp_path: Path) -> None:
    """Требует, чтобы отметка ставилась по ФАКТУ, а не по просьбе.

    Попросивший послабление и передавший файл защиту не снимал, и говорить о
    нём обратное значило бы врать в отчёте о здоровье.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    engine = Engine(
        TransportSettings(),
        Budget(),
        state_path=tmp_path / "state.json",
        unsafe_price_changes_without_audit=True,
    )
    assert UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT not in frozenset(engine._unsafe)


def test_the_record_is_written_before_the_request_leaves(tmp_path: Path) -> None:
    """Требует, чтобы запись лежала в ФАЙЛЕ уже к моменту отправки.

    Главная проверка набора. «Запишем, когда подтвердится» означает не записать
    ровно те правки, которые могли уйти: ответ теряется, процесс падает, а цена
    на площадке уже новая.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    path = tmp_path / "state.json"
    engine = Engine(TransportSettings(), Budget(), state_path=path)
    core = engine.update_price(NODE, OFFER, "777", expected_revision=_revision())

    reply: Any = None
    seen_submit = False
    while True:
        try:
            request = core.send(reply)
        except StopIteration:
            break
        if isinstance(request, Fetch):
            reply = _observation(_page(), url="https://funpay.com/lots/offerEdit")
            continue
        assert isinstance(request, Submit)
        seen_submit = True

        # Файл читается СТОРОННИМ читателем: так его увидел бы процесс,
        # поднятый после падения этого.
        stored = StateFile(path).load().get("price_audit") or {}
        journal = stored.get("journal") or []
        assert journal, "запрос уходит, а в файле про правку ничего нет"
        assert journal[-1]["price_after"] == "777"
        assert journal[-1]["price_before"] != "777", "прежняя цена не записана"

        reply = _observation("<html></html>", url=f"https://funpay.com/lots/{NODE}/trade")

    assert seen_submit, "сохранение так и не отправилось"


def test_the_journal_survives_a_restart(tmp_path: Path) -> None:
    """Требует, чтобы журнал переживал перезапуск процесса.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    path = tmp_path / "state.json"
    first = Engine(TransportSettings(), Budget(), state_path=path)
    first.price_audit.record(_change(OFFER, price_after="123", at_ms=17))
    first._save_price_audit()

    second = Engine(TransportSettings(), Budget(), state_path=path)
    restored = second.price_audit.history(OFFER)
    assert len(restored) == 1
    assert restored[0].price_after == "123"
    assert restored[0].price_before == "50"
    assert restored[0].at_ms == 17
    assert second.price_audit.original(OFFER) is not None


def test_the_first_record_of_a_lot_is_never_evicted() -> None:
    """Требует, чтобы «как было до бота» пережило вытеснение.

    Промежуточные цены ставил тот же бот, и терять их не жалко. Первая - та,
    что стояла до него, и она отвечает на единственный вопрос, ради которого
    журнал заведён.

    Возвращает:
        None
    """
    audit = PriceAudit(limit=3)
    audit.record(_change(OFFER, price_after="1", at_ms=1))
    for step in range(2, 8):
        audit.record(_change("other", price_after=str(step), at_ms=step))

    assert len(audit) == 3, "предел не соблюдён"
    assert audit.dropped == 4, "вытесненные не сосчитаны"
    assert audit.history(OFFER) == (), "первая запись обязана быть вытеснена из журнала"

    original = audit.original(OFFER)
    assert original is not None, "«как было до бота» потеряно вытеснением"
    assert original.price_after == "1"


def test_eviction_is_counted_and_not_silent() -> None:
    """Требует, чтобы усечение было видно счётчиком.

    Молчаливое усечение читалось бы как «правок было столько», а их было
    больше.

    Возвращает:
        None
    """
    audit = PriceAudit(limit=2)
    assert audit.dropped == 0
    for step in range(1, 6):
        audit.record(_change("lot", price_after=str(step), at_ms=step))
    assert len(audit) == 2
    assert audit.dropped == 3
    assert [one.price_after for one in audit.history()] == ["4", "5"], "вытесняется не старое"


def test_a_zero_limit_means_no_eviction() -> None:
    """Требует, чтобы нулевой предел означал «не вытеснять».

    Возвращает:
        None
    """
    audit = PriceAudit(limit=0)
    for step in range(1, 40):
        audit.record(_change("lot", price_after=str(step), at_ms=step))
    assert len(audit) == 39
    assert audit.dropped == 0


def test_the_default_limit_is_declared() -> None:
    """Требует, чтобы предел по умолчанию был именно объявленным числом.

    Возвращает:
        None
    """
    audit = PriceAudit()
    for step in range(DEFAULT_JOURNAL_LIMIT + 5):
        audit.record(_change("lot", price_after=str(step), at_ms=step + 1))
    assert len(audit) == DEFAULT_JOURNAL_LIMIT
    assert audit.dropped == 5


def test_history_filters_by_offer() -> None:
    """Требует, чтобы отбор по предложению отбирал.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.record(_change("a", price_after="1", at_ms=1))
    audit.record(_change("b", price_after="2", at_ms=2))
    audit.record(_change("a", price_after="3", at_ms=3))

    assert [one.price_after for one in audit.history("a")] == ["1", "3"]
    assert [one.price_after for one in audit.history("b")] == ["2"]
    assert len(audit.history()) == 3


def test_original_is_the_first_and_stays_the_first() -> None:
    """Требует, чтобы первая запись не перезаписывалась второй.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.record(_change(OFFER, price_after="1", at_ms=1))
    audit.record(_change(OFFER, price_after="2", at_ms=2))

    original = audit.original(OFFER)
    assert original is not None
    assert original.price_after == "1"
    assert original.at_ms == 1
    assert audit.original("никого") is None


@pytest.mark.parametrize(
    "record",
    [
        {"offer_id": "", "at_ms": 1},
        {"offer_id": "   ", "at_ms": 1},
        {"offer_id": 42, "at_ms": 1},
        {"offer_id": None, "at_ms": 1},
        {"at_ms": 1},
        {"offer_id": "a"},
        {"offer_id": "a", "at_ms": True},
        {"offer_id": "a", "at_ms": "1"},
        {"offer_id": "a", "at_ms": 1.5},
        "не словарь",
    ],
)
def test_a_bad_record_is_skipped_and_the_rest_survives(record: Any) -> None:
    """Требует, чтобы битая запись не рушила остальные.

    Разбор по месту обнулял бы журнал и падал на середине: восстановленным
    остаётся начало, а всё, что дальше, пропадает насовсем. Пропадает вместе с
    прежними ценами.

    Аргументы:
        record (Any): непригодная запись.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.restore(
        {
            "journal": [
                {**_flat("good-1"), **{}},
                record,
                {**_flat("good-2"), **{}},
            ]
        }
    )
    assert [one.offer_id for one in audit.history()] == ["good-1", "good-2"]


def _flat(offer_id: str) -> dict[str, Any]:
    """Собирает пригодную запись обычными значениями.

    Аргументы:
        offer_id (str): предложение.

    Возвращает:
        dict[str, Any]: запись.
    """
    return {
        "offer_id": offer_id,
        "node_id": NODE,
        "price_before": "50",
        "price_after": "60",
        "revision_before": "aaaabbbbccccdddd",
        "at_ms": 1,
    }


@pytest.mark.parametrize("value", [None, "нет", 12, [], {"journal": 1}])
def test_a_broken_payload_leaves_an_empty_journal(value: Any) -> None:
    """Требует пустого журнала, а не исключения, на непригодном разделе.

    Файл мог быть записан прежней редакцией, у которой раздела не было вовсе.

    Аргументы:
        value (Any): непригодное значение раздела journal.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.record(_change(OFFER))
    audit.restore({"journal": value})
    assert len(audit) == 0


@pytest.mark.parametrize("value", [True, -1, "5", 1.5, None])
def test_a_bad_dropped_counter_reads_as_zero(value: Any) -> None:
    """Требует, чтобы непригодный счётчик читался нулём, а не чем попало.

    Истина в Python - это единица, и счётчик True прочитался бы как одна
    вытесненная запись.

    Аргументы:
        value (Any): непригодное значение счётчика.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.restore({"journal": [_flat("a")], "dropped": value})
    assert audit.dropped == 0


def test_a_good_dropped_counter_is_kept() -> None:
    """Требует, чтобы пригодный счётчик переживал восстановление.

    Иначе перезапуск делает усечение молчаливым - ровно то, от чего счётчик и
    заведён.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.restore({"journal": [_flat("a")], "dropped": 7})
    assert audit.dropped == 7


def test_an_old_file_without_the_first_section_still_answers_original() -> None:
    """Требует, чтобы записи журнала годились в первые.

    Файл мог быть записан редакцией, у которой раздела first не было, и терять
    из-за этого «как было до бота» нельзя.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.restore({"journal": [_flat("a"), {**_flat("a"), "price_after": "70"}]})
    original = audit.original("a")
    assert original is not None
    assert original.price_after == "60", "первой сочли не первую"


def test_the_first_section_wins_over_the_journal() -> None:
    """Требует, чтобы вытесненная первая запись побеждала уцелевшую позднюю.

    Ради этого раздел first и хранится отдельно: в журнале первой записи уже
    может не быть.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.restore(
        {
            "first": [{**_flat("a"), "price_before": "5", "at_ms": 1}],
            "journal": [{**_flat("a"), "price_before": "500", "at_ms": 900}],
        }
    )
    original = audit.original("a")
    assert original is not None
    assert original.price_before == "5"


def test_a_snapshot_round_trips() -> None:
    """Требует, чтобы записанное читалось обратно тем же.

    Возвращает:
        None
    """
    audit = PriceAudit(limit=2)
    for step in range(1, 5):
        audit.record(_change("lot", price_after=str(step), at_ms=step))

    other = PriceAudit(limit=2)
    other.restore(audit.snapshot())

    assert [_flat_of(one) for one in other.history()] == [_flat_of(one) for one in audit.history()]
    assert other.dropped == audit.dropped
    first = other.original("lot")
    assert first is not None and first.price_after == "1"


def _flat_of(change: PriceChange) -> tuple[str, ...]:
    """Раскладывает запись в кортеж для сравнения.

    Аргументы:
        change (PriceChange): запись.

    Возвращает:
        tuple[str, ...]: поля записи строками.
    """
    return (
        change.offer_id,
        change.node_id,
        change.price_before,
        change.price_after,
        change.revision_before,
        str(change.at_ms),
    )


def test_a_field_of_the_wrong_type_reads_as_empty_not_as_text() -> None:
    """Требует, чтобы число не выдавало себя за прочитанную цену.

    Приведения к строке нет нарочно: 50, обращённое в «50», выглядело бы
    прочитанной ценой, а прочитано оно не было.

    Возвращает:
        None
    """
    audit = PriceAudit()
    audit.restore({"journal": [{**_flat("a"), "price_before": 50, "node_id": None}]})
    one = audit.history()[0]
    assert one.price_before == ""
    assert one.node_id == ""


def test_the_declared_audit_is_the_one_that_refuses() -> None:
    """Требует, чтобы объявление аудита совпадало с поведением операции.

    Пока ключ audit принимался генератором и выбрасывался, спецификация
    требовала сохранить состояние до правки, пакет о требовании не знал, и
    связать одно с другим было нечем. Объявление стояло и молчало - ровно та
    болезнь, которую репозиторий запрещает.

    Возвращает:
        None
    """
    from funora.operations import OPERATIONS

    declared = {name: one for name, one in OPERATIONS.items() if one.audit_fail_closed}
    assert set(declared) == {"lots.update_price"}, (
        f"аудит с отказом объявлен у {sorted(declared)}, а проверен только у "
        "правки цены: неподтверждённое объявление - это обещание без исполнения"
    )
    assert OPERATIONS["lots.update_price"].audit == "before_state"

    # И само поведение: объявлено fail_closed - значит отказывает.
    engine = Engine(TransportSettings(), Budget())
    core = engine.update_price(NODE, OFFER, "99", expected_revision=_revision())
    with pytest.raises(ConfigurationError):
        core.send(None)


def test_no_other_operation_claims_an_audit_it_does_not_do() -> None:
    """Требует, чтобы аудит был объявлен ровно там, где он исполняется.

    Возвращает:
        None
    """
    from funora.operations import OPERATIONS

    with_audit = {name for name, one in OPERATIONS.items() if one.audit}
    assert with_audit == {"lots.update_price"}, (
        f"аудит объявлен у {sorted(with_audit)}. Появившееся объявление обязано "
        "получить исполнение либо запись в spec/conformance/not-implemented.yaml"
    )


def test_an_audit_of_another_kind_is_refused_not_silently_substituted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Требует отказа, если контракт потребует аудита другого вида.

    Реализован ровно before_state - сохранить состояние ДО правки. Появись в
    спецификации другой вид, исполнять по-прежнему прежний значило бы молча
    выдавать одно требование за другое: снаружи выглядит исполненным, а
    исполнено не то.

    Аргументы:
        monkeypatch (pytest.MonkeyPatch): подменяет объявление операции.

    Возвращает:
        None
    """
    from dataclasses import replace

    from funora import operations

    monkeypatch.setitem(
        operations.OPERATIONS,
        "lots.update_price",
        replace(operations.OPERATIONS["lots.update_price"], audit="after_state"),
    )

    engine = Engine(TransportSettings(), Budget(), unsafe_price_changes_without_audit=True)
    core = engine.update_price(NODE, OFFER, "99", expected_revision=_revision())

    reply: Any = None
    with pytest.raises(ConfigurationError) as raised:
        while True:
            request = core.send(reply)
            assert isinstance(request, Fetch), "до отправки дело дойти не должно"
            reply = _observation(_page(), url="https://funpay.com/lots/offerEdit")

    assert "after_state" in str(raised.value), "отказ не называет требуемого вида аудита"
