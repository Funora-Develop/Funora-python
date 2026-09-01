"""Сквозная проверка правила о заимствованном знании.

ПРАВИЛО ОДНО, И ДЕЙСТВУЕТ ОНО НА ВСЕ ОПЕРАЦИИ СРАЗУ: операция ЗАПИСИ, часть
которой стоит на сообщении независимой реализации того же протокола, не
отправляет ничего без явного согласия вызывающего.

Проверок «без согласия не уходит запрос» в проекте семь - по одной в наборе
каждой такой операции. Каждая проверяет СВОЮ, и ни одна не заметит восьмой,
написанной завтра без предохранителя.

Этот набор смотрит с другой стороны: он берёт перечень операций ИЗ КОНТРАКТА,
отбирает по признаку - запись на вторичном источнике - и требует отказа от
каждой. Появится новая, забудут предохранитель - упадёт здесь, а не у продавца.

ЧТЕНИЕ СОГЛАСИЯ НЕ СПРАШИВАЕТ, и это вторая половина правила, тоже проверяемая.
Ошибка чтения на чужом знании видна: вернётся не то либо не вернётся ничего.
Ошибка записи необратима.

Спрашивать согласие везде подряд значило бы обесценить механизм: вызывающий,
привыкший включать всё, перестанет читать, ЧТО ему предлагают включить.
"""

from __future__ import annotations

from typing import Final

import pytest

from funora.operations import OPERATIONS

#: Сколько заимствованных операций записи было на 31.08.2026.
#:
#: Число стоит здесь не ради числа. Опустей перечень - и все проверки ниже
#: прошли бы, ничего не проверив: цикл по нулю записей не падает никогда.
_WRITES_AT_LEAST: Final[int] = 7

#: Столько же для чтений.
_READS_AT_LEAST: Final[int] = 4


def _borrowed() -> list[str]:
    """Отбирает операции, стоящие на вторичном источнике.

    Возвращает:
        list[str]: Имена операций.
    """
    return sorted(
        name for name, one in OPERATIONS.items() if one.request_provenance == "third_party_report"
    )


def _borrowed_writes() -> list[str]:
    """Отбирает из них операции ЗАПИСИ.

    Возвращает:
        list[str]: Имена операций.
    """
    return [one for one in _borrowed() if OPERATIONS[one].safety.value != "safe"]


def _borrowed_reads() -> list[str]:
    """Отбирает из них операции чтения.

    Возвращает:
        list[str]: Имена операций.
    """
    return [one for one in _borrowed() if OPERATIONS[one].safety.value == "safe"]


def test_the_registry_of_borrowed_operations_is_not_empty() -> None:
    """Требует, чтобы перечень не опустел молча.

    Пустой перечень прошёл бы все проверки ниже: цикл по нулю записей не падает
    никогда. Проверка на непустоту стоит первой именно поэтому.

    Возвращает:
        None
    """
    writes, reads = _borrowed_writes(), _borrowed_reads()

    assert len(writes) >= _WRITES_AT_LEAST, (
        f"заимствованных записей {len(writes)}, а было {_WRITES_AT_LEAST}. "
        "Если операцию сняли - хорошо; если у неё пропало объявление "
        "происхождения, то вместе с ним пропал и предохранитель"
    )
    assert len(reads) >= _READS_AT_LEAST, f"заимствованных чтений {len(reads)}"


@pytest.mark.parametrize("name", _borrowed_writes())
def test_every_borrowed_write_names_its_source_and_its_price(name: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА: каждая заимствованная запись объясняется целиком.

    Три поля обязательны, и каждое отвечает на свой вопрос вызывающего:

      кто сказал - source, иначе сообщение неотличимо от выдумки;
      что именно непроверено - rests_on_it, иначе недоверие переносится либо на
      всё сразу, либо ни на что;
      чем грозит ошибка - why_opt_in, иначе согласие спрашивают без причины.

    Аргументы:
        name (str): Имя операции.

    Возвращает:
        None
    """
    one = OPERATIONS[name]

    assert one.provenance_source, f"{name}: не сказано, кто сообщил"
    assert one.provenance_rests_on, f"{name}: не сказано, что именно непроверено"
    assert len(one.provenance_rests_on) > 60, (
        f"{name}: сказано слишком коротко, чтобы что-то значить"
    )


@pytest.mark.parametrize("name", _borrowed_writes())
def test_every_borrowed_write_refuses_without_consent(name: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: каждая заимствованная запись отказывает.

    Проверок «без согласия не уходит запрос» в проекте семь - по одной в наборе
    каждой операции. Каждая знает про СВОЮ, и ни одна не заметит восьмой,
    написанной завтра без предохранителя.

    Эта смотрит с другой стороны: перечень берётся из КОНТРАКТА, и новая
    операция попадает в него сама.

    Аргументы:
        name (str): Имя операции.

    Возвращает:
        None
    """
    # Ввоз по короткому имени, а не через пакет tests: пакета такого нет, и
    # работает он у меня лишь потому, что корень оказался в пути. В CI не
    # оказался - и проверка падала ввозом, ничего не проверив.
    from _consent_probe import call_without_consent

    from funora.errors import UsageError

    with pytest.raises(UsageError) as raised:
        call_without_consent(name)

    text = str(raised.value)
    assert "согласия" in text, f"{name}: отказ не говорит о согласии"
    assert OPERATIONS[name].provenance_source.split(",")[0].strip() in text, (
        f"{name}: отказ не называет источника заимствования"
    )


@pytest.mark.parametrize("name", _borrowed_reads())
def test_a_borrowed_read_asks_no_consent(name: str) -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: заимствованное чтение согласия НЕ спрашивает.

    Обратная половина правила, и она важна не меньше. Спрашивать согласие везде
    подряд значило бы обесценить механизм: вызывающий, привыкший включать всё,
    перестанет читать, ЧТО ему предлагают включить.

    Ошибка чтения на чужом знании видна - вернётся не то либо не вернётся
    ничего. Ошибка записи необратима.

    Аргументы:
        name (str): Имя операции.

    Возвращает:
        None
    """
    one = OPERATIONS[name]
    assert one.safety.value == "safe", f"{name}: чтение объявлено небезопасным"
    # У чтения объяснять цену согласия незачем: согласия не спрашивают.
    assert one.provenance_source, f"{name}: источник не назван и у чтения"


def test_no_operation_borrows_without_declaring_it() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: заимствование объявляется, а не подразумевается.

    Проверить это до конца нельзя: заимствование, о котором не сказано нигде,
    ничем от собственного наблюдения не отличается - в том и беда.

    Что проверить МОЖНО: у каждого модуля, ссылающегося на сторонний источник в
    тексте, есть объявленная операция с этим происхождением. Модуль, поминающий
    источник и не объявивший его, - забытое объявление.

    Возвращает:
        None
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "funora"
    declared_sources = " ".join(
        one.provenance_source for one in OPERATIONS.values() if one.provenance_source
    )

    silent: list[str] = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not re.search(r"FunPayAPI|FunPayCardinal", source):
            continue
        # Модуль поминает источник. Значит где-то есть операция, объявившая его.
        if "FunPayAPI" not in declared_sources:
            silent.append(path.name)

    assert not silent, (
        f"модули ссылаются на сторонний источник, а операций с объявленным "
        f"происхождением нет: {silent}"
    )
