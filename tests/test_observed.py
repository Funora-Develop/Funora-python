"""Проверки типа наблюдаемого значения.

Набор защищает одно различие: «поле пустое» и «поля не было» - разные события с
противоположными решениями. Пустое поле перечитывать незачем, отсутствующее -
повод заподозрить изменение вёрстки.

Половина проверок здесь о том, что тип неудобен нарочно. Удобный тип этот
различие проглотил бы: `if entry.status` и `entry.status or ""` пишутся сами
собой и стирают его молча.
"""

from __future__ import annotations

import pytest

from funora._observed import Confidence, Observed, Presence
from funora.errors import FunoraError, UnobservedFieldError


def test_three_states_are_distinguishable() -> None:
    """Проверяет, что три состояния различимы снаружи.

    Returns:
        None
    """
    assert Observed.present("x").presence is Presence.PRESENT
    assert Observed.empty("").presence is Presence.EMPTY
    assert Observed.missing("no_node").presence is Presence.NOT_OBSERVED


def test_empty_is_observed_and_missing_is_not() -> None:
    """Проверяет главное различие набора.

    Returns:
        None
    """
    assert Observed.empty("").is_observed
    assert not Observed.missing("no_node").is_observed


def test_reading_missing_value_raises() -> None:
    """Проверяет, что значение, которого никто не видел, нельзя прочитать молча.

    Вернуть здесь None значило бы стереть различие, ради которого тип заведён.
    Подставить умолчание может только вызывающий: лишь он знает, чем грозит его
    задаче отсутствие именно этого поля.

    Returns:
        None
    """
    with pytest.raises(UnobservedFieldError) as exc:
        _ = Observed.missing("status_mapping_not_observed").value
    assert "status_mapping_not_observed" in str(exc.value)
    assert "get(" in str(exc.value), "сообщение обязано подсказывать выход"


def test_usage_error_is_a_funora_error() -> None:
    """Проверяет, что ошибка ловится обработчиком базового типа.

    Returns:
        None
    """
    assert issubclass(UnobservedFieldError, FunoraError)


def test_empty_value_is_readable() -> None:
    """Проверяет, что пустое значение читается без возражений.

    Оно наблюдалось, и это полноценный результат.

    Returns:
        None
    """
    assert Observed.empty("").value == ""


def test_bool_is_forbidden() -> None:
    """Проверяет запрет на приведение к булеву значению.

    Три состояния не сводятся к двум без потери различия, а запись
    `if entry.description` выглядит настолько естественно, что проглотила бы
    его молча.

    Returns:
        None
    """
    for value in (Observed.present("x"), Observed.empty(""), Observed.missing("no_node")):
        with pytest.raises(TypeError) as exc:
            bool(value)
        assert "is_observed" in str(exc.value), "сообщение обязано подсказывать замену"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Observed.present("Аккаунт"), "Аккаунт"),
        (Observed.empty(""), ""),
        (Observed.missing("no_node"), "<не наблюдалось: no_node>"),
    ],
)
def test_str_shows_absence_in_words(value: Observed[str], expected: str) -> None:
    """Проверяет строковое представление во всех трёх состояниях.

    Значение уходит в журналы и сообщения, где пустая строка неотличима от
    честно пустого поля.

    Args:
        value (Observed[str]): Проверяемое значение.
        expected (str): Ожидаемое представление.

    Returns:
        None
    """
    assert str(value) == expected
    assert f"{value}" == expected


def test_get_and_or_none_are_the_explicit_way_out() -> None:
    """Проверяет способы признать отсутствие явно.

    Returns:
        None
    """
    missing: Observed[str] = Observed.missing("no_node")
    assert missing.get("умолчание") == "умолчание"
    assert missing.or_none() is None
    assert Observed.present("x").get("умолчание") == "x"


def test_missing_has_no_confidence() -> None:
    """Проверяет, что у отсутствия нет уверенности правила.

    Уверенность описывает, как получено значение. У ненаблюдённого поля
    получать было нечего, и любое значение здесь вводило бы в заблуждение.

    Returns:
        None
    """
    assert Observed.missing("no_node").confidence is None
    assert Observed.present("x").confidence is Confidence.OBSERVED
    assert Observed.present("x", Confidence.INFERRED).confidence is Confidence.INFERRED


def test_reason_only_on_missing() -> None:
    """Проверяет, что причина заполнена только у отсутствия.

    Returns:
        None
    """
    assert Observed.missing("no_node").reason == "no_node"
    assert Observed.present("x").reason is None
    assert Observed.empty("").reason is None


def test_present_refuses_an_empty_string() -> None:
    """Проверяет, что пустая строка не собирается как наблюдённая.

    Непустоту обещает сам тип, и обещание держалось на честном слове:
    конструктор принимал что угодно. Собранное здесь пустое значение отбирает у
    вызывающего единственный способ отличить «поле есть, и оно пусто» от «поле
    есть»: различать он должен по состоянию, а не заглядывая внутрь.

    Returns:
        None
    """
    with pytest.raises(ValueError, match="empty"):
        Observed.present("")


def test_present_refuses_an_empty_sequence() -> None:
    """Проверяет то же для последовательности.

    Пустой кортеж ссылок как наблюдённое значение означал бы «посмотрели, ссылок
    нет»; такое наблюдение существует, но собирается через empty().

    Returns:
        None
    """
    with pytest.raises(ValueError, match="empty"):
        Observed.present(())


def test_present_accepts_false_and_zero() -> None:
    """Проверяет, что строгость не задела логическое и числовое поля.

    У логического поля False - полноценное значение, а не пустота: признак
    непрочитанного, равный False, означает «прочитано», а не «не наблюдалось».
    Проверка стоит здесь затем, что запретить «всё ложное» было бы проще, и
    именно это сломало бы chats.unread.

    Returns:
        None
    """
    assert Observed.present(False).value is False
    assert Observed.present(0).value == 0


def test_no_parsed_field_breaks_the_promise() -> None:
    """Проверяет обещание на всех полях всех снимков разом.

    Проверка конструктора держит только те поля, что собраны через present().
    Эта смотрит с другой стороны - на результат разбора целиком, - и потому
    переживёт появление поля, собранного как-нибудь иначе.

    Returns:
        None
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from funora._chats import parse_chats_page
    from funora._orders import parse_orders_page
    from funora._thread import parse_thread

    fixtures = Path(__file__).parent / "fixtures" / "pages"
    when = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

    def read(name: str) -> str:
        """Читает снимок страницы.

        Args:
            name (str): Имя снимка без расширения.

        Returns:
            str: Разметка снимка.
        """
        return (fixtures / f"{name}.skeleton.txt").read_text(encoding="utf-8")

    entities = [
        *parse_orders_page(read("orders-trade.logged.ru"), observed_at=when).rows(
            accept_incomplete=True
        ),
        *parse_chats_page(read("chat.logged.ru"), observed_at=when).rows(accept_incomplete=True),
        *parse_thread(read("chat-thread.logged.ru"), observed_at=when).messages(
            accept_incomplete=True
        ),
    ]
    assert len(entities) > 20, "сущностей не набралось - проверять нечего"

    checked = 0
    for entity in entities:
        for name in entity.__slots__:
            field = getattr(entity, name)
            if not isinstance(field, Observed):
                continue
            checked += 1
            if field.presence is not Presence.PRESENT:
                continue
            value = field.value
            if isinstance(value, str | tuple | list | frozenset | set | dict):
                assert value, (
                    f"поле {type(entity).__name__}.{name} объявлено наблюдённым, "
                    "а значение пусто - отличить «пусто» от «есть» вызывающему нечем"
                )

    assert checked > 100, "наблюдаемых полей не набралось - проверка почти ничего не смотрит"
