"""Проверки разбора страницы одного заказа.

Каждая проверка двусторонняя: положительная половина требует прочесть то, что в
снимке есть, отрицательная - НЕ прочесть того, чего там нет. У этой страницы
вторая половина дороже: состояние заказа - то, по чему бот решает выдавать
товар, и выдуманное значение здесь стоит денег.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from funora._observed import Confidence
from funora._order import parse_order_page
from funora._result import Severity
from funora.errors import ProtocolChangedError
from funora.extraction import OrderStatus

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок страницы заказа. Аккаунт наблюдений - продавец.
ORDER: Final[str] = "order.logged.ru"

#: Момент наблюдения. Постоянен нарочно: разбор обязан быть повторяемым.
WHEN: Final[datetime] = datetime(2026, 8, 24, tzinfo=UTC)


def _page(name: str) -> str:
    """Читает снимок страницы.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_every_anchored_field_is_read() -> None:
    """Требует, чтобы прочиталось каждое поле, у которого есть свой якорь.

    Перебор идёт по именам, а не по одному полю: поле, добавленное в запись и
    забытое в разборе, иначе осталось бы ненаблюдённым молча.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)

    assert not view.defects, [one.code for one in view.defects]
    for name in (
        "order_number",
        "status",
        "status_class",
        "amount_text",
        "currency_symbol_text",
        "category_href",
        "counterparty_name",
        "counterparty_href",
        "counterparty_online",
        "counterparty_banned",
        "chat_node_id",
        "refund_available",
        "review_author_id",
    ):
        value = getattr(view, name)
        assert value.is_observed, (
            f"поле {name} не прочитано, причина {value.reason!r}. Снимок его несёт"
        )


def test_the_status_is_marked_inferred_and_not_observed() -> None:
    """Требует, чтобы выведенное состояние было помечено выведенным.

    Словарь цветовых классов снят со СПИСКА ПРОДАЖ. Что он верен и для страницы
    заказа - рассуждение, пусть и правдоподобное: класс тот же, оформление у
    площадки одно.

    Пометка уверенности существует ровно для таких случаев, и разбор, объявивший
    вывод наблюдением, отбирает у вызывающего единственный способ их различить.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)

    assert view.status.value is OrderStatus.CLOSED
    assert view.status.confidence is Confidence.INFERRED, (
        f"состояние помечено {view.status.confidence}: словарь снят с другой "
        "страницы, и выдавать вывод за наблюдение нельзя"
    )
    # Само имя класса отдаётся как есть и наблюдено, а не выведено.
    assert view.status_class.value == "text-success"
    assert view.status_class.confidence is Confidence.OBSERVED


def test_an_unknown_status_class_is_not_observed_rather_than_guessed() -> None:
    """Требует отказаться от незнакомого класса состояния.

    Наблюдено ОДНО состояние на одном заказе. Прочие цветовые классы того же
    семейства на странице дают ноль совпадений, и это не значит, что их не
    бывает: значит, что их не видели.

    Возвращает:
        None
    """
    broken = _page(ORDER).replace('<span class="text-success">', '<span class="text-unknown">', 1)
    assert broken != _page(ORDER), "подмена класса состояния не сработала"

    view = parse_order_page(broken, WHEN)

    assert not view.status.is_observed, (
        f"незнакомый класс прочитан как состояние {view.status.or_none()}"
    )
    assert view.status.reason == "status_class_not_in_dictionary"
    assert "status_class_not_in_dictionary" in {one.code for one in view.defects}
    # Имя класса при этом отдаётся: вызывающий увидит, что именно не опознано.
    assert view.status_class.value == "text-unknown"


def test_two_status_classes_at_once_give_no_status() -> None:
    """Требует отказаться от состояния, когда носитель несёт сразу два.

    Состояний два, а заказ один. Взять любое значило бы выбрать наугад то, по
    чему бот решает выдавать товар.

    Возвращает:
        None
    """
    broken = _page(ORDER).replace(
        '<span class="text-success">', '<span class="text-success text-primary">', 1
    )
    view = parse_order_page(broken, WHEN)

    assert not view.status.is_observed
    assert view.status.reason == "status_carriers_disagree"
    assert "status_carriers_disagree" in {one.code for one in view.defects}


def test_a_missing_status_carrier_is_a_page_defect() -> None:
    """Требует громко заметить исчезновение носителя состояния.

    Возвращает:
        None
    """
    broken = _page(ORDER).replace('<span class="text-success">', '<em class="text-success">', 1)
    view = parse_order_page(broken, WHEN)

    assert not view.status.is_observed
    assert "status_carrier_missing" in {one.code for one in view.defects}
    assert any(one.severity is Severity.PAGE for one in view.defects)


def test_the_missing_review_is_read_as_empty_not_as_unobserved() -> None:
    """Требует читать отсутствие отзыва ПОЛОЖИТЕЛЬНО.

    Атрибут оценки присутствует и пуст. Это свидетельство о странице, а не о
    нашем незнании, и различие вызывающему нужно: «отзыва нет» и «оценку не
    прочитали» ведут к разным решениям.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)

    # «Пусто» - это ПРОЧИТАНО и пусто. is_observed здесь истинно, и это не
    # мелочь: обёртка различает три состояния, а не два, и «пусто» стоит на
    # стороне знания, а не незнания.
    assert view.review_rating.is_observed, "пустой атрибут - это наблюдение, а не пробел"
    assert view.review_rating.presence.value == "empty", view.review_rating.presence
    assert view.review_rating.reason is None, (
        "у наблюдения «пусто» причины отсутствия не бывает: это факт о странице"
    )

    # А вот исчезновение самого атрибута - уже незнание, и оно отличается.
    broken = _page(ORDER).replace(" data-rating ", " ", 1)
    assert broken != _page(ORDER), "снятие атрибута оценки не сработало"
    without = parse_order_page(broken, WHEN)
    assert without.review_rating.reason == "attribute_absent:review_rating", (
        f"причина {without.review_rating.reason!r}: отсутствие атрибута обязано "
        "отличаться от пустого атрибута"
    )


def test_a_banned_counterparty_is_a_field_of_its_own() -> None:
    """Требует отдельного поля для блокировки, а не третьего значения присутствия.

    На снимке узел несёт offline И banned разом. Сведи их в одно поле - и одно
    из двух потерялось бы: присутствие отвечает на «в сети ли он сейчас»,
    блокировка - на «жив ли аккаунт вообще».

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)

    assert view.counterparty_online.value is False
    assert view.counterparty_banned.value is True

    # Снятие одного признака не трогает другой.
    unbanned = _page(ORDER).replace(
        'class="media media-user offline banned"', 'class="media media-user offline"', 1
    )
    other = parse_order_page(unbanned, WHEN)
    assert other.counterparty_banned.value is False
    assert other.counterparty_online.value is False


def test_an_unknown_presence_class_is_not_read_by_negation() -> None:
    """Требует не выводить присутствие из отсутствия знакомого класса.

    Правило «нет offline, значит online» запрещено: оно выглядело бы работающим
    ровно до переименования класса.

    Возвращает:
        None
    """
    broken = _page(ORDER).replace(
        'class="media media-user offline banned"', 'class="media media-user away banned"', 1
    )
    view = parse_order_page(broken, WHEN)

    assert not view.counterparty_online.is_observed
    assert view.counterparty_online.reason == "class_not_in_dictionary"
    # Блокировка при этом читается: она признак наличия токена, а не словаря.
    assert view.counterparty_banned.value is True


def test_the_chat_id_is_read_and_its_two_carriers_are_compared() -> None:
    """Требует прочесть идентификатор диалога и сверить два его носителя.

    Идентификатор - самое ценное на странице помимо самого заказа: по нему
    переписку читают целиком. Виджет и ссылка «открыть переписку» - два носителя
    одного и того же, и расхождение означает смену разметки.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)
    assert view.chat_node_id.is_observed

    broken = _page(ORDER).replace("chat-control", "chat-control-renamed", 1)
    other = parse_order_page(broken, WHEN)
    assert "chat_carriers_disagree" in {one.code for one in other.defects}


def test_the_amount_is_split_by_nodes_and_notices_a_changed_shape() -> None:
    """Требует делить сумму по узлам и замечать смену устройства блока.

    Первая редакция резала текст блока образцом «цифры, потом остальное». На
    снимке это не работало вовсе: скелет заменяет значение подписью, и текст
    начинается с буквы. Разбор, который нельзя проверить на фикстуре, не
    проверен ничем.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)
    assert view.amount_text.is_observed
    assert view.currency_symbol_text.is_observed
    assert view.amount_text.value != view.currency_symbol_text.value, (
        "число и знак валюты пришли одинаковыми: блок не разделён"
    )

    # Блок остаётся на месте, но узлов в нём становится больше: разбор обязан
    # отказаться, а не взять первый попавшийся. Подмена самого тега сюда не
    # годится - от неё перестаёт находиться сам блок, и это другой отказ.
    broken = _page(ORDER).replace(
        '<span class="h1 mr4 text-bold">',
        '<span class="extra">x</span><span class="h1 mr4 text-bold">',
        1,
    )
    assert broken != _page(ORDER), "добавление узла не сработало"

    other = parse_order_page(broken, WHEN)
    assert not other.amount_text.is_observed
    assert other.amount_text.reason == "amount_block_shape_changed", other.amount_text.reason


def test_the_params_are_returned_as_shown_without_invented_names() -> None:
    """Требует отдавать параметры как есть и все восемь.

    Имён у них нет нарочно: различить их можно было бы только по локализованной
    метке, а два из восьми к тому же совпадают побайтово.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)
    params = view.params()

    assert len(params) == 8, f"параметров {len(params)}, а на странице восемь"
    for one in params:
        assert one.label_text, "параметр без метки"
        assert one.value_text, "параметр без значения"
        assert not one.value_text.startswith(one.label_text), (
            f"значение {one.value_text!r} начинается с метки: они не разделены"
        )

    # Два параметра неразличимы - и это видно в самих данных, а не только в
    # рассуждении. Проверка стоит здесь затем, что попытка дать им имена
    # начнётся именно с сомнения «а вдруг они всё-таки разные».
    twins = [one for one in params if one.label_text == "T6:c"]
    assert len(twins) == 2, f"близнецов {len(twins)}"
    assert twins[0] == twins[1], "близнецы разошлись: разметка изменилась"


def test_a_page_without_the_param_list_is_a_protocol_change() -> None:
    """Требует громкого отказа там, где перечня параметров нет вовсе.

    Пустую запись возвращать нельзя: она неотличима от заказа без параметров.

    Возвращает:
        None
    """
    broken = _page(ORDER).replace('class="param-list"', 'class="param-list-renamed"', 1)
    with pytest.raises(ProtocolChangedError, match="перечня параметров"):
        parse_order_page(broken, WHEN)


def test_two_category_links_give_no_category() -> None:
    """Требует отказаться от раздела, когда признак перестал различать.

    Параметр со ссылкой опознаётся по НАЛИЧИЮ ссылки нужной формы. Признак
    слабее класса, и второе совпадение означает, что он больше не различает.

    Возвращает:
        None
    """
    broken = _page(ORDER).replace(
        '<div class="param-item">\n                              <h5>',
        '<div class="param-item">\n                              '
        '<a href="https://funpay.com/lots/{n99}/">x</a>\n                              <h5>',
        1,
    )
    assert broken != _page(ORDER), "подмена не сработала"

    view = parse_order_page(broken, WHEN)
    assert not view.category_href.is_observed
    assert view.category_href.reason == "category_link_ambiguous"
    assert "category_link_ambiguous" in {one.code for one in view.defects}


def test_the_chat_on_the_order_page_is_never_called_complete() -> None:
    """Требует не объявлять переписку со страницы заказа полной.

    Признака усечения на странице не нашлось ни одного, и из этого НЕ следует,
    что она полна: ровно на такой неудаче поиска уже ошиблись с догрузкой
    отзывов.

    Полноты у записи поэтому нет вовсе - есть число показанных сообщений и
    идентификатор диалога, которым переписку читают целиком.

    Возвращает:
        None
    """
    view = parse_order_page(_page(ORDER), WHEN)

    assert view.messages_shown == 5
    assert not hasattr(view, "completeness"), (
        "у записи появилась полнота: страница заказа обещать её не может"
    )
    assert view.chat_node_id.is_observed, (
        "без идентификатора диалога усечённая переписка становится тупиком"
    )


def test_a_damaged_read_does_not_declare_the_capability_working() -> None:
    """Требует понижать состояние возможности по повреждениям страницы.

    Прежде чтение заказа объявляло полноту безусловно, и возможность
    объявлялась работающей даже тогда, когда состояние заказа не прочиталось.
    Молчаливо: набор зелёный, профиль возможностей зелёный, а разбор вернул
    ненаблюдённое значение там, где решается выдача товара.

    Возвращает:
        None
    """
    from funora._engine import IMPLEMENTED
    from funora.capabilities import Capability, CapabilityState

    assert Capability.ORDERS_GET in IMPLEMENTED

    broken = _page(ORDER).replace('<span class="text-success">', '<span class="text-unknown">', 1)
    view = parse_order_page(broken, WHEN)
    assert any(one.severity is Severity.PAGE for one in view.defects), (
        "повреждение уровня страницы не замечено - понижать будет нечего"
    )
    assert CapabilityState.DEGRADED is not CapabilityState.SUPPORTED
