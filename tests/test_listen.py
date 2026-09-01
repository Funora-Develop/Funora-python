"""Проверки быстрого пути: канал обновлений как сигнал.

Главное, что здесь проверяется, - не скорость, а ЧЕСТНОСТЬ быстрого пути. Он
имеет право экономить чтения только тогда, когда канал ответил понятно и сказал
«ничего не менялось». Во всех прочих случаях - непонятный ответ, объявленная
ошибка, чужая форма, слишком долгая тишина - наблюдение обязано вернуться к
чтению страниц, потому что страницы читаются кодом, чьё поведение установлено.

Настоящего ожидания в наборе нет: паузы подменены.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import monotonic

import pytest

import funora._client as client_module
import funora._engine as engine_module
from funora._client import Client
from funora._listen import (
    CHANNEL_COOLDOWN_MS,
    CHANNEL_WATCHDOG_MS,
    CHAT_BOOKMARKS,
    ORDERS_COUNTERS,
    ChannelSignal,
    ChannelState,
    classify_signal,
    seed_tags,
    unexpected_shape,
    wanted_objects,
)
from funora._runner import parse_runner_context
from funora._transport import Observation
from funora._updates import UNSEEN_TAG, parse_updates_answer
from funora._watch import Router

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"


def _page(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _observation(html: str) -> Observation:
    """Собирает наблюдение из готовой разметки.

    Args:
        html (str): Тело ответа.

    Returns:
        Observation: Наблюдение с объявленной длиной тела.
    """
    return replace(
        Observation(
            status=200,
            final_url="https://funpay.com/orders/trade",
            html=html,
            elapsed_ms=10,
            redirects=0,
            content_length=len(html.encode("utf-8")),
            declared_length=len(html.encode("utf-8")),
        )
    )


def _answer(objects: list[dict[str, object]], *, response: object = True) -> str:
    """Собирает тело ответа канала.

    Args:
        objects (list[dict[str, object]]): Изменившиеся объекты.
        response (object): Поле response. Логическое - ответ на опрос без
            действия, объект - ответ на действие.

    Returns:
        str: Тело ответа.
    """
    return json.dumps({"objects": objects, "response": response}, ensure_ascii=False)


class _Channel:
    """Транспорт, отвечающий и на страницы, и на канал.

    Args:
        answers (list[str]): Тела ответов канала, по одному на обращение.
            Последнее повторяется, если обращений больше.
    """

    def __init__(self, answers: list[str]) -> None:
        self._answers = answers
        self._asked = 0
        self.paths: list[str] = []
        self.submitted: list[dict[str, str]] = []

    def fetch(self, path: str) -> Observation:
        """Отдаёт страницу по адресу.

        Args:
            path (str): Запрошенный путь.

        Returns:
            Observation: Наблюдение.
        """
        self.paths.append(path)
        if path.startswith("/orders"):
            return _observation(_page("orders-trade.logged.ru"))
        return _observation(_page("chat.logged.ru"))

    def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]) -> Observation:
        """Отвечает на обращение к каналу.

        Args:
            path (str): Путь.
            fields (dict[str, str]): Поля запроса.
            headers (dict[str, str]): Заголовки.

        Returns:
            Observation: Наблюдение с телом ответа канала.
        """
        self.submitted.append(dict(fields))
        body = self._answers[min(self._asked, len(self._answers) - 1)]
        self._asked += 1
        return _observation(body)

    def close(self) -> None:
        """Закрывает подставной транспорт.

        Returns:
            None
        """

    def pages_read(self) -> int:
        """Считает прочитанные страницы.

        Returns:
            int: Сколько раз читались страницы.
        """
        return len(self.paths)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет сон счётчиком пауз и двигает часы вместе с ним.

    Часы обязаны двигаться: и бюджет, и остывание канала, и сторожевой срок
    считаются по монотонным секундам. Подмена, глотающая сон и оставляющая часы
    на месте, показала бы канал, который не остывает никогда.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        list[float]: Длительности, которые цикл собирался проспать.
    """
    slept: list[float] = []
    started = monotonic()
    offset = [0.0]

    def fake_sleep(seconds: float) -> None:
        """Считает паузу и продвигает часы на неё же.

        Returns:
            None
        """
        slept.append(seconds)
        offset[0] += seconds

    def fake_monotonic() -> float:
        """Возвращает время с учётом проспанного.

        Returns:
            float: Монотонные секунды.
        """
        return started + offset[0]

    monkeypatch.setattr(client_module, "sleep", fake_sleep)
    monkeypatch.setattr(engine_module, "monotonic", fake_monotonic)
    return slept


# --- разбор ответа --------------------------------------------------------


def test_two_global_objects_are_subscribed_not_one_per_dialogue() -> None:
    """Проверяет состав подписки.

    Подписаться можно и на каждый диалог порознь, но объектов принимается
    десять, а обрезка молчалива: продавец с пятнадцатью диалогами не узнал бы,
    что пять из них не слушаются вовсе. Глобальных счётчиков ровно два при
    любом числе диалогов.

    Возвращает:
        None
    """
    assert wanted_objects("12345678") == [
        (ORDERS_COUNTERS, "12345678"),
        (CHAT_BOOKMARKS, "12345678"),
    ]


def test_an_empty_answer_means_quiet_not_broken() -> None:
    """Проверяет, что пустой перечень объектов - штатное состояние.

    Возвращает:
        None
    """
    signal, reason = classify_signal(parse_updates_answer(_answer([])))

    assert signal is ChannelSignal.QUIET
    assert reason == ""


def test_a_changed_object_asks_for_a_page_read() -> None:
    """Проверяет, что изменение объекта читается как «читай страницы».

    Возвращает:
        None
    """
    body = _answer([{"type": ORDERS_COUNTERS, "id": "1", "tag": "t1", "data": {"seller": 1}}])

    signal, _ = classify_signal(parse_updates_answer(body))

    assert signal is ChannelSignal.CHANGED


def test_a_declared_error_is_not_read_as_quiet() -> None:
    """Проверяет порядок разбора: ошибка раньше содержимого.

    Ответ с непустой ошибкой и пустыми объектами иначе прочитался бы тишиной -
    то есть отказ выглядел бы подтверждением, что менять нечего.

    Возвращает:
        None
    """
    body = _answer([], response={"error": "что-то пошло не так"})

    signal, reason = classify_signal(parse_updates_answer(body))

    assert signal is ChannelSignal.DEGRADED
    assert reason == "channel_reported_error"


def test_an_answer_to_an_action_we_did_not_send_is_refused() -> None:
    """Проверяет, что ответ чужой формы признаётся непригодным.

    Опрос идёт без действия, и площадка отвечает на такой запрос логическим.
    Объект означает, что мы получили ответ на чужой запрос либо канал устроен
    не так, как наблюдался.

    Возвращает:
        None
    """
    body = _answer([], response={"error": None})

    assert unexpected_shape(parse_updates_answer(body))


def test_tags_accumulate_across_answers() -> None:
    """Проверяет накопление меток.

    Ответ несёт только изменившиеся объекты. Собирая метки лишь из последнего
    ответа, опрос подставлял бы всем молчавшим метку «я ничего не видел», и
    площадка отдавала бы их целиком заново на каждом шаге.

    Возвращает:
        None
    """
    first = parse_updates_answer(
        _answer(
            [
                {"type": ORDERS_COUNTERS, "id": "7", "tag": "o1", "data": {}},
                {"type": CHAT_BOOKMARKS, "id": "7", "tag": "b1", "data": {}},
            ]
        )
    )
    tags = first.tags()

    second = parse_updates_answer(
        _answer([{"type": CHAT_BOOKMARKS, "id": "7", "tag": "b2", "data": {}}])
    )
    tags = second.tags(tags)

    assert tags == {
        (ORDERS_COUNTERS, "7"): "o1",
        (CHAT_BOOKMARKS, "7"): "b2",
    }, "метка молчавшего объекта обязана пережить ответ, в котором его нет"


def test_seed_tags_reads_what_the_page_actually_shows() -> None:
    """Проверяет, что начальные метки берутся со страницы.

    Метка счётчиков продаж наблюдена равной значению атрибута страницы. Метка
    закладок не наблюдена, и берётся кандидат: неугаданная метка не ломает
    ничего - площадка отвечает на любую всем, что изменилось.

    Возвращает:
        None
    """
    context = parse_runner_context(_page("chat.logged.ru"))

    tags = seed_tags(context)

    assert context.own_user_id.is_observed, "снимок обязан нести собственный номер"
    own = context.own_user_id.value
    assert (ORDERS_COUNTERS, own) in tags
    assert tags[(ORDERS_COUNTERS, own)] == context.orders_tag.value


def test_an_unknown_tag_is_still_a_valid_subscription() -> None:
    """Проверяет, что объект без известной метки получает «я ничего не видел».

    Возвращает:
        None
    """
    from funora._updates import build_subscription

    batches = build_subscription([(ORDERS_COUNTERS, "7")], {})

    assert batches[0][0]["tag"] == UNSEEN_TAG


# --- состояние слушателя --------------------------------------------------


def test_one_failure_does_not_leave_the_fast_path() -> None:
    """Проверяет, что разовый отказ не уводит с быстрого пути.

    Сеть моргает, и уходить на минуту из-за одного моргания значило бы терять
    скорость чаще, чем нужно.

    Возвращает:
        None
    """
    state = ChannelState()

    assert state.note_failure("что-то", now=100.0) is False
    assert state.available(100.0), "после одного отказа канал ещё доступен"


def test_two_failures_in_a_row_start_the_cooldown() -> None:
    """Проверяет, что два отказа подряд считаются состоянием.

    Возвращает:
        None
    """
    state = ChannelState()
    state.note_failure("раз", now=100.0)

    assert state.note_failure("два", now=100.0) is True
    assert not state.available(100.0)
    assert state.available(100.0 + CHANNEL_COOLDOWN_MS / 1000)


def test_a_good_answer_forgets_the_failures() -> None:
    """Проверяет, что счёт неудач обнуляется понятным ответом.

    Возвращает:
        None
    """
    state = ChannelState()
    state.note_failure("раз", now=1.0)
    state.note_success(2.0, changed=True)

    assert state.note_failure("снова", now=3.0) is False, "счёт обязан начаться заново"


def test_silence_longer_than_the_watchdog_asks_for_a_page_read() -> None:
    """Проверяет сторожевой срок.

    Канал, замолчавший не потому, что менять нечего, а потому, что перестал
    отвечать по существу, от тишины неотличим: в обоих случаях объектов в
    ответе нет. Срок и есть то, что делает всю затею безопасной.

    Возвращает:
        None
    """
    state = ChannelState()
    state.note_success(0.0, changed=False)

    assert not state.watchdog_expired(1.0)
    assert state.watchdog_expired(CHANNEL_WATCHDOG_MS / 1000)


def test_a_change_resets_the_watchdog() -> None:
    """Проверяет, что отсчёт тишины начинается заново после изменения.

    Возвращает:
        None
    """
    state = ChannelState()
    state.note_success(0.0, changed=False)
    state.note_success(10.0, changed=True)

    assert not state.watchdog_expired(CHANNEL_WATCHDOG_MS / 1000)


# --- цикл наблюдения ------------------------------------------------------


def test_a_quiet_channel_saves_the_page_reads(no_sleep: list[float]) -> None:
    """Проверяет главное: тишина канала стоит одного маленького запроса.

    Ради этого всё и затевалось. Первый шаг читает страницы - иначе опорную
    точку брать неоткуда, - а дальше молчащий аккаунт страниц не читает вовсе.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    transport = _Channel([_answer([])])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3)

    assert len(transport.submitted) == 2, "канал спрошен на втором и третьем шаге"
    assert transport.pages_read() == 2, "страницы прочитаны только на первом шаге: заказы и диалоги"


def test_a_talking_channel_makes_the_loop_read_the_pages(no_sleep: list[float]) -> None:
    """Проверяет, что изменение в канале приводит к чтению страниц.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    changed = _answer([{"type": CHAT_BOOKMARKS, "id": "1", "tag": "b1", "data": {"counter": 1}}])
    transport = _Channel([changed])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=2)

    assert len(transport.submitted) == 1
    assert transport.pages_read() == 4, "оба шага прочитали по две страницы"


def test_an_unusable_channel_falls_back_to_reading_pages(no_sleep: list[float]) -> None:
    """Проверяет, что непонятный ответ не роняет наблюдение, а замедляет его.

    Уронить здесь значило бы сделать быстрый путь опаснее медленного, а он
    заводился ради скорости, а не вместо надёжности.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    transport = _Channel(["это не JSON"])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3)

    assert transport.pages_read() == 6, "все три шага прочитали страницы"
    assert len(transport.submitted) == 2, (
        "после двух непонятных ответов канал уходит в остывание и больше не спрашивается"
    )


def test_a_declared_error_also_falls_back(no_sleep: list[float]) -> None:
    """Проверяет откат при объявленной ошибке канала.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    transport = _Channel([_answer([], response={"error": "отказ"})])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3)

    assert transport.pages_read() == 6


def test_the_channel_is_not_asked_before_the_first_read(no_sleep: list[float]) -> None:
    """Проверяет, что первый шаг всегда читает страницы.

    Опорную точку брать неоткуда, и спрашивать канал до неё бессмысленно.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    transport = _Channel([_answer([])])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=1)

    assert transport.submitted == [], "на первом шаге канал не спрашивается"
    assert transport.pages_read() == 2


def test_turning_the_channel_off_restores_the_old_behaviour(no_sleep: list[float]) -> None:
    """Проверяет, что выключение возвращает прежний цикл целиком.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    transport = _Channel([_answer([])])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3, use_channel=False)

    assert transport.submitted == []
    assert transport.pages_read() == 6


def test_the_subscription_carries_the_token_and_both_objects(no_sleep: list[float]) -> None:
    """Проверяет состав запроса к каналу.

    Аргументы:
        no_sleep (list[float]): Перечень пауз.

    Возвращает:
        None
    """
    transport = _Channel([_answer([])])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=2)

    assert len(transport.submitted) == 1
    sent = transport.submitted[0]
    assert "csrf_token" in sent, "без защитного токена к каналу не обратиться"
    objects = json.loads(sent["objects"])
    assert [one["type"] for one in objects] == [ORDERS_COUNTERS, CHAT_BOOKMARKS]
    assert len({one["id"] for one in objects}) == 1, "оба объекта берут id из data-user"
