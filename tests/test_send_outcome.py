"""Проверки правила, устанавливающего исход отправки.

Разница между «отправлено» и «не отправлено» у операции без отмены дороже любой
другой в этом контракте. Отменить сообщение покупателю нельзя, и повтор при
неоднозначном исходе означает второе сообщение.

Поэтому исходов ТРИ. Двузначный ответ вынудил бы выбрать одно из двух зол:
объявить успехом всё, что вернулось с кодом 200, либо однажды отправить дважды.

ОТВЕТЫ ЗДЕСЬ СОБРАНЫ ПО НАБЛЮДЁННОЙ ФОРМЕ, а не выдуманы. Сетевые записи хранят
форму, а не значения, и подставить настоящий ответ неоткуда - но форма и есть
то, что наблюдалось. Первая проверка набора сверяет собранное с записью: разойдись
они, и правило описывало бы ответ, которого площадка не присылает.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from funora._runner import classify_send_response
from funora.send_outcome import SEND_PIPELINE, SEND_REASONS, SendOutcome

#: Записи сетевых наблюдений.
OBSERVATIONS: Final[Path] = Path(__file__).resolve().parent.parent / "observations"

#: Наблюдение отправки: в нём есть и опрос, и действие.
SEND: Final[str] = "network.send-origins.json"

#: Имя диалога, в который «отправляли». Значение своё: настоящих в записях нет.
NODE: Final[str] = "users-12345678-87654321"


def _answer(**overrides: Any) -> str:
    """Собирает ответ канала по наблюдённой форме.

    Аргументы:
        **overrides (Any): что заменить на верхнем уровне.

    Возвращает:
        str: тело ответа.
    """
    body: dict[str, Any] = {
        "objects": [
            {
                "type": "chat_node",
                "id": NODE,
                "tag": "7f3a9b21",
                "data": {
                    "node": {"id": 283028758, "name": NODE, "silent": False},
                    "messages": [{"id": 2010613313, "author": 12345678, "html": "<div/>"}],
                    "hasHistory": True,
                },
            },
            {
                "type": "chat_bookmarks",
                "id": 12345678,
                "tag": "a1b2c3d4",
                "data": {"counter": 1, "message": 2010613313, "order": [1, 2], "html": "<div/>"},
            },
        ],
        "response": {"error": None},
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def test_the_assembled_answer_matches_the_recorded_shape() -> None:
    """Требует, чтобы собранный ответ совпадал по форме с наблюдённым.

    Проверка держит связь правила с наблюдением. Без неё правило описывало бы
    ответ, которого площадка не присылает, а весь набор проходил бы на выдуманной
    форме - и был бы проверкой самого себя.

    Возвращает:
        None
    """
    path = OBSERVATIONS / SEND
    if not path.is_file():
        pytest.skip("сетевого наблюдения отправки нет на диске")

    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    actions = [
        one
        for one in records
        if isinstance(((one.get("request") or {}).get("fields") or {}).get("request"), dict)
    ]
    assert actions, "в наблюдении нет ни одной записи с действием"

    shape = actions[0]["response"]["fields"]
    assert sorted(shape) == ["objects", "response"], sorted(shape)
    assert list(shape["response"]) == ["error"], shape["response"]

    node = next(one for one in shape["objects"] if one.get("type") == "chat_node")
    assert sorted(node) == ["data", "id", "tag", "type"], sorted(node)
    assert sorted(node["data"]) == ["hasHistory", "messages", "node"], sorted(node["data"])
    assert "name" in node["data"]["node"], node["data"]["node"]
    assert "id" in node["data"]["messages"][0], node["data"]["messages"][0]

    # И то же самое - у собранного здесь ответа.
    mine = json.loads(_answer())
    assert sorted(mine) == sorted(shape)
    assert sorted(mine["response"]) == sorted(shape["response"])
    mine_node = next(one for one in mine["objects"] if one["type"] == "chat_node")
    assert sorted(mine_node) == sorted(node)
    assert sorted(mine_node["data"]) == sorted(node["data"])


def test_a_confirmed_send_is_named_by_a_positive_sign() -> None:
    """Требует объявлять успех по возвращённому сообщению, а не по молчанию.

    Отсутствие отказа означало бы «отправлено» о всяком ответе с кодом 200.

    Возвращает:
        None
    """
    result = classify_send_response(_answer(), sent_to=NODE)

    assert result.outcome is SendOutcome.CONFIRMED
    assert result.reason == "confirmed_by_channel"
    assert result.is_confirmed is True
    assert result.channel_message_id.is_observed
    assert result.channel_message_id.value == 2010613313
    assert result.node.value == NODE
    assert result.messages_in_answer == 1


@pytest.mark.parametrize(
    ("body", "outcome", "reason"),
    [
        pytest.param("не json вовсе", SendOutcome.UNCONFIRMED, "body_not_json", id="шаг1"),
        pytest.param("[1, 2, 3]", SendOutcome.UNCONFIRMED, "body_not_an_object", id="шаг2"),
        pytest.param(
            json.dumps({"objects": [], "response": False}),
            SendOutcome.UNCONFIRMED,
            "response_not_an_object",
            id="шаг3",
        ),
    ],
)
def test_a_malformed_answer_is_unconfirmed_not_failed(
    body: str, outcome: SendOutcome, reason: str
) -> None:
    """Требует называть непонятный ответ неподтверждённым, а не неудачным.

    Запрос УШЁЛ. Ответ, который не разобрался, говорит о том, что пришло не то, -
    и ничего не говорит о том, выполнено ли действие.

    Аргументы:
        body (str): тело ответа.
        outcome (SendOutcome): ожидаемый исход.
        reason (str): ожидаемая причина.

    Возвращает:
        None
    """
    result = classify_send_response(body, sent_to=NODE)

    assert result.outcome is outcome
    assert result.reason == reason
    assert result.is_confirmed is False


def test_a_non_empty_error_is_a_refusal_whatever_its_shape() -> None:
    """Требует опознавать отказ, не зная его формы.

    Формы отказа не видел никто: поле error наблюдалось только пустым. Для
    решения форма и не нужна - довольно предиката «error не пусто».

    Отсюда цена той неизвестности: она мешает НАЗВАТЬ причину отказа словами, а
    не отличить отказ от успеха.

    Возвращает:
        None
    """
    for shape in ("что-то пошло не так", {"code": 42}, ["первое", "второе"], 0, False):
        result = classify_send_response(_answer(response={"error": shape}), sent_to=NODE)
        assert result.outcome is SendOutcome.REFUSED, f"на отказе вида {shape!r}"
        assert result.reason == "channel_reported_error"


def test_an_answer_about_someone_elses_dialogue_confirms_nothing() -> None:
    """Требует сверять диалог ответа с тем, в который отправляли.

    Канал отвечает и о чужих диалогах: подписка едет в каждом запросе, и объект
    другого диалога в ответе отправку не подтверждает.

    Возвращает:
        None
    """
    result = classify_send_response(_answer(), sent_to="users-99999999-11111111")

    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == "node_mismatch"


def test_an_answer_without_the_dialogue_object_confirms_nothing() -> None:
    """Требует не считать подтверждением ответ без узла диалога.

    Пустое поле objects приводит сюда же, и это верно.

    Возвращает:
        None
    """
    for objects in ([], [{"type": "chat_bookmarks", "id": 1, "tag": "x", "data": {}}]):
        result = classify_send_response(_answer(objects=objects), sent_to=NODE)
        assert result.outcome is SendOutcome.UNCONFIRMED
        assert result.reason == "no_chat_node_in_answer"


def test_a_dialogue_object_without_messages_confirms_nothing() -> None:
    """Требует не считать подтверждением узел диалога без сообщений.

    Узел приходит и при простом обновлении. Пустой список подтверждением не
    является.

    Возвращает:
        None
    """
    quiet = json.loads(_answer())
    quiet["objects"][0]["data"]["messages"] = []

    result = classify_send_response(json.dumps(quiet), sent_to=NODE)

    assert result.outcome is SendOutcome.UNCONFIRMED
    assert result.reason == "empty_message_list"


def test_every_step_of_the_normative_order_has_its_own_case() -> None:
    """Требует, чтобы у каждого объявленного шага был свой случай.

    Шаг, чей случай не отличается от соседнего, - не шаг, а украшение. Требование
    записано в самой спецификации, в разделе conformance.

    Возвращает:
        None
    """
    covered = {
        "body_not_json",
        "body_not_an_object",
        "response_not_an_object",
        "channel_reported_error",
        "no_chat_node_in_answer",
        "node_mismatch",
        "empty_message_list",
        "confirmed_by_channel",
    }
    declared = {reason for _, _, reason in SEND_PIPELINE}

    assert declared == covered, (
        f"порядок объявляет причины {sorted(declared - covered)}, у которых нет случая; "
        f"набор проверяет {sorted(covered - declared)}, которых порядок не называет"
    )


def test_the_reasons_of_the_order_are_all_declared() -> None:
    """Требует, чтобы всякая причина шага была в закрытом перечне.

    Причина - не пояснение для человека, а то, по чему вызывающий принимает
    решение. Названная шагом и отсутствующая в перечне, она читалась бы как
    опечатка.

    Возвращает:
        None
    """
    for _, _, reason in SEND_PIPELINE:
        assert reason in SEND_REASONS, f"причина {reason!r} не объявлена в перечне"


def test_the_receipt_is_never_read_as_a_plain_truth_value() -> None:
    """Требует именованного признака вместо приведения к булеву.

    У квитанции три исхода, и молчаливое `if result` читало бы unconfirmed как
    успех - ровно ту ошибку, ради которой третий исход и заведён.

    Возвращает:
        None
    """
    unconfirmed = classify_send_response("не json", sent_to=NODE)

    assert bool(unconfirmed) is True, (
        "квитанция приводится к булеву, и приведение даёт истину: "
        "`if result` прочтёт неподтверждённое как успех"
    )
    assert unconfirmed.is_confirmed is False
