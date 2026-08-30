"""Проверки разбора ответа канала обновлений.

Наблюдение снято 30.08.2026 тремя опросами подряд, и всё, что здесь проверяется,
взято из него, а не из чужой реализации.

ГЛАВНОЕ, ЧТО ЗАЩИЩАЕТ НАБОР, - предел подписки. Площадка обрезает её молча:
послано одиннадцать объектов, вернулось десять, и признака отброшенного в ответе
нет никакого. Реализация, подписавшаяся на пятнадцать диалогов, получит десять и
не узнает об этом ниоткуда - сообщения пяти покупателей не придут, и объяснить
это будет нечем.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from funora._updates import (
    MAX_SUBSCRIPTION,
    UNSEEN_TAG,
    build_subscription,
    parse_updates_answer,
)
from funora.errors import ProtocolChangedError


def _answer(objects: list[dict[str, Any]], response: Any = False) -> str:
    """Собирает тело ответа канала.

    Аргументы:
        objects (list[dict[str, Any]]): Объекты ответа.
        response (Any): Поле response.

    Возвращает:
        str: Тело ответа.
    """
    return json.dumps({"objects": objects, "response": response}, ensure_ascii=False)


def test_a_quiet_channel_is_a_normal_state() -> None:
    """Требует читать пустой перечень объектов как молчание, а не как поломку.

    Канал молчит, когда молчать нечего, и это его обычное состояние: ради него
    он и нужен.

    Возвращает:
        None
    """
    answer = parse_updates_answer(_answer([]))

    assert answer.is_quiet is True
    assert answer.objects == ()
    assert answer.error == ""


def test_the_numbers_come_as_numbers() -> None:
    """Проверяет то, ради чего канал ценен помимо скорости.

    Счётчики приходят числами. Признак непрочитанного сегодня ВЫВОДИТСЯ из
    расхождения двух позиций в разметке и помечен выведенным; здесь он число.

    Возвращает:
        None
    """
    answer = parse_updates_answer(
        _answer(
            [
                {
                    "type": "orders_counters",
                    "id": "77",
                    "tag": "abcd1234",
                    "data": {"buyer": 0, "seller": 4},
                }
            ]
        )
    )

    counters = answer.objects[0]
    assert counters.number("seller") == 4
    assert counters.number("buyer") == 0, "ноль прочитан как отсутствие"
    assert counters.number("которого_нет") is None


def test_a_boolean_is_not_a_number() -> None:
    """Требует не читать логическое значение как счётчик.

    В Python истина - это единица, и счётчик, оказавшийся логическим,
    прочитался бы числом молча.

    Возвращает:
        None
    """
    answer = parse_updates_answer(
        _answer([{"type": "chat_node", "id": "1", "tag": "t", "data": {"silent": True}}])
    )
    assert answer.objects[0].number("silent") is None


def test_the_missing_field_is_not_a_zero() -> None:
    """Требует различать «поля нет» и «значение ноль».

    Ноль означает «счётчик равен нулю», отсутствие - «мы не знаем». Слить их
    значило бы объявить пустым то, что не прочитано.

    Возвращает:
        None
    """
    answer = parse_updates_answer(_answer([{"type": "x", "id": "1", "tag": "t", "data": {}}]))
    assert answer.objects[0].number("counter") is None


def test_the_tags_are_collected_for_the_next_poll() -> None:
    """Требует собрать метки ответа: они и есть квитанция «я видел до сюда».

    Возвращает:
        None
    """
    answer = parse_updates_answer(
        _answer(
            [
                {"type": "chat_node", "id": "1", "tag": "aaa", "data": {}},
                {"type": "chat_node", "id": "2", "tag": "bbb", "data": {}},
                {"type": "chat_node", "id": "3", "tag": "", "data": {}},
            ]
        )
    )

    assert answer.tags() == {("chat_node", "1"): "aaa", ("chat_node", "2"): "bbb"}, (
        "метка без значения попала в перечень: подставленная пустой, она "
        "означала бы не то, что означает выдуманная"
    )


def test_the_tag_is_keyed_by_object_not_by_kind() -> None:
    """Требует ключевать метку парой из вида и идентификатора.

    Подписка держит по объекту на каждый диалог, и вид у них общий: ключ из
    одного вида затёр бы все метки, кроме последней.

    Возвращает:
        None
    """
    answer = parse_updates_answer(
        _answer(
            [
                {"type": "chat_node", "id": "1", "tag": "aaa", "data": {}},
                {"type": "chat_node", "id": "2", "tag": "bbb", "data": {}},
            ]
        )
    )
    assert len(answer.tags()) == 2


def test_an_error_is_read_only_from_an_action_answer() -> None:
    """Требует читать ошибку там, где ей есть где лежать.

    Площадка отвечает объектом при опросе С ДЕЙСТВИЕМ и логическим - без него.
    У логического ошибке взяться неоткуда.

    Возвращает:
        None
    """
    plain = parse_updates_answer(_answer([], response=False))
    assert plain.answered_action is False
    assert plain.error == ""

    acted = parse_updates_answer(_answer([], response={"error": "нельзя"}))
    assert acted.answered_action is True
    assert acted.error == "нельзя"

    fine = parse_updates_answer(_answer([], response={"error": None}))
    assert fine.error == "", "пустая ошибка прочитана как ошибка"


@pytest.mark.parametrize(
    "body",
    ["не json вовсе", "[]", '"строка"', "42", '{"response": false}'],
)
def test_a_body_of_another_shape_is_a_protocol_change(body: str) -> None:
    """Требует громкого отказа на теле не той формы.

    Молчаливое «объектов нет» здесь неотличимо от штатного молчания канала, а
    означает противоположное: мы приняли за канал не канал.

    Аргументы:
        body (str): Тело ответа.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_updates_answer(body)


def test_an_object_without_a_kind_is_skipped() -> None:
    """Требует пропускать объект без вида.

    Что лежит в его data - неизвестно, и читать это нечем.

    Возвращает:
        None
    """
    answer = parse_updates_answer(
        _answer([{"id": "1", "tag": "t", "data": {"buyer": 1}}, {"type": "ok", "id": "2"}])
    )
    assert [one.type for one in answer.objects] == ["ok"]


def test_the_subscription_is_cut_into_portions_of_ten() -> None:
    """ГЛАВНАЯ ПРОВЕРКА НАБОРА: подписка режется на порции.

    Площадка обрезает длинную подписку МОЛЧА - послано одиннадцать, вернулось
    десять, признака отброшенного нет. Считать порции обязан вызывающий.

    Возвращает:
        None
    """
    wanted = [("chat_node", str(one)) for one in range(23)]
    portions = build_subscription(wanted, {})

    assert len(portions) == 3, f"порций {len(portions)}, а 23 объекта дают три"
    assert [len(one) for one in portions] == [10, 10, 3]
    assert all(len(one) <= MAX_SUBSCRIPTION for one in portions)

    # Ни один объект не потерян и ни один не задвоен.
    sent = [one["id"] for portion in portions for one in portion]
    assert sent == [str(one) for one in range(23)]


def test_an_object_without_a_known_tag_gets_the_unseen_one() -> None:
    """Требует подставлять метку «я ничего не видел», когда своей нет.

    Площадка отвечает на неё всем, что изменилось, - ровно так же, как отвечает
    странице при первом обращении.

    Возвращает:
        None
    """
    portions = build_subscription(
        [("chat_node", "1"), ("chat_node", "2")], {("chat_node", "1"): "своя"}
    )

    tags = {one["id"]: one["tag"] for one in portions[0]}
    assert tags == {"1": "своя", "2": UNSEEN_TAG}


def test_an_empty_subscription_still_gives_one_portion() -> None:
    """Требует отдать одну пустую порцию, а не ноль порций.

    Ноль порций означал бы «опрашивать нечего», и цикл не сделал бы ни одного
    запроса - в том числе на первом шаге, когда подписываться ещё не на что.

    Возвращает:
        None
    """
    assert build_subscription([], {}) == [[]]


def test_the_observed_answer_parses_as_it_was_seen() -> None:
    """Разбирает ответ той формы, какая наблюдалась 30.08.2026.

    Форма взята из записи наблюдения: имена полей и виды объектов те самые.
    Значения здесь свои - записи наблюдения хранят подписи, а не значения.

    Возвращает:
        None
    """
    answer = parse_updates_answer(
        _answer(
            [
                {
                    "type": "chat_bookmarks",
                    "id": "77",
                    "tag": "bm123456",
                    "data": {"counter": 1, "html": "<div>...</div>", "message": 9, "order": [1, 2]},
                },
                {
                    "type": "orders_counters",
                    "id": "77",
                    "tag": "or123456",
                    "data": {"buyer": 0, "seller": 4},
                },
            ]
        )
    )

    assert [one.type for one in answer.objects] == ["chat_bookmarks", "orders_counters"]
    assert answer.objects[0].number("counter") == 1
    assert answer.objects[1].number("seller") == 4
    assert answer.tags() == {
        ("chat_bookmarks", "77"): "bm123456",
        ("orders_counters", "77"): "or123456",
    }

    # Разметка проходит НАСКВОЗЬ и не разбирается: разбирать её тем же кодом,
    # что разбирает страницу, никто не проверял.
    assert answer.objects[0].data["html"] == "<div>...</div>"
    assert answer.objects[0].number("html") is None
