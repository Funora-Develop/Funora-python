"""Проверки одновременной раздачи событий.

Спецификация говорит про порядок так: он сохраняется внутри одного ключа
упорядочивания, а события с разными ключами порядка между собой не имеют.
Асинхронный клиент этим пользуется - но только когда его об этом попросили.

Набор проверяет три вещи, и все три про то, что легко сломать незаметно.

Порядок внутри ключа. Два сообщения одного диалога, пришедшие в обратном
порядке, - это выданный не тот товар, а не косметика.

Одинаковость итога. Итог партии обязан быть один и тот же независимо от того,
кто из групп успел раньше. Разъедься он - и решение о сдвиге курсора начало бы
зависеть от планировщика, то есть событие терялось бы через раз и
невоспроизводимо.

Одновременность как таковая. Проверка, что группы действительно идут вместе, а
не последовательно с параметром для вида.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from funora._diff import Event
from funora._watch import Router, StepResult, adispatch
from funora.errors import HandlerTimeoutError, SessionExpiredError
from funora.events import EventType

#: Момент наблюдения. Задан явно, чтобы события оставались повторяемыми.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _event(key: str, number: int) -> Event:
    """Собирает событие с заданным ключом упорядочивания.

    Args:
        key (str): Ключ упорядочивания.
        number (int): Порядковый номер внутри ключа.

    Returns:
        Event: Событие.
    """
    return Event(
        id=f"{key}:{number}",
        type=EventType.MESSAGE_CREATED,
        account_id="12345678",
        ordering_key=key,
        entity_id=key,
        observed_at=WHEN,
        origin="structural",
        payload={"n": number},
    )


#: Партия из трёх ключей по три события в каждом.
BATCH: tuple[Event, ...] = tuple(
    _event(key, number) for key in ("chat:a", "chat:b", "chat:c") for number in range(3)
)


async def test_order_inside_a_key_survives() -> None:
    """Проверяет, что внутри ключа порядок сохраняется при любой одновременности.

    Два сообщения одного диалога, пришедшие в обратном порядке, - это выданный
    не тот товар. Ради этого ключ и существует.

    Returns:
        None
    """
    seen: dict[str, list[int]] = {}

    async def handler(event: Event) -> None:
        """Записывает номер события в список своего ключа.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        # Пауза нулевой длины отдаёт управление циклу событий: без неё
        # сопрограммы отработали бы подряд и проверка ничего не проверила бы.
        await asyncio.sleep(0)
        seen.setdefault(event.ordering_key, []).append(event.payload["n"])

    router = Router()
    router.on()(handler)
    await adispatch(router, BATCH, concurrency=4)

    assert seen == {"chat:a": [0, 1, 2], "chat:b": [0, 1, 2], "chat:c": [0, 1, 2]}


async def test_keys_really_go_together() -> None:
    """Проверяет, что группы идут одновременно, а не последовательно.

    Иначе параметр был бы для вида: набор прошёл бы, ничего не ускорив.

    Returns:
        None
    """
    started: list[str] = []
    release = asyncio.Event()

    async def handler(event: Event) -> None:
        """Отмечается и ждёт общего разрешения на первом событии ключа.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        if event.payload["n"] == 0:
            started.append(event.ordering_key)
            if len(started) == 3:
                release.set()
            await release.wait()

    router = Router()
    router.on()(handler)

    # Последовательная раздача здесь никогда бы не завершилась: первый ключ ждал
    # бы разрешения, которое даст только третий. Предел ожидания и есть проверка.
    await asyncio.wait_for(adispatch(router, BATCH, concurrency=3), timeout=5)
    assert sorted(started) == ["chat:a", "chat:b", "chat:c"]


async def test_result_does_not_depend_on_who_finished_first() -> None:
    """Проверяет, что итог партии одинаков при любой одновременности.

    Итог решает, сдвигать ли курсор. Зависел бы он от планировщика - и событие
    терялось бы через раз, невоспроизводимо.

    Returns:
        None
    """

    async def handler(event: Event) -> None:
        """Падает на одном заранее известном событии.

        Args:
            event (Event): Событие.

        Returns:
            None

        Raises:
            RuntimeError: На втором событии второго ключа.
        """
        # Разная задержка нужна, чтобы группы гарантированно финишировали не в
        # том порядке, в каком начались.
        await asyncio.sleep({"chat:a": 0.03, "chat:b": 0.01, "chat:c": 0.02}[event.ordering_key])
        if event.ordering_key == "chat:b" and event.payload["n"] == 1:
            raise RuntimeError("обработчик не смог")

    router = Router()
    router.on()(handler)

    def shape(result: StepResult) -> tuple[object, ...]:
        """Сводит итог к сравнимому виду.

        Args:
            result (StepResult): Итог раздачи.

        Returns:
            tuple[object, ...]: Сравнимая часть итога.
        """
        return (
            tuple(e.id for e in result.delivered),
            tuple(e.id for e in result.failed),
            result.advance,
            len(result.errors),
            type(result.fatal),
        )

    serial = await adispatch(router, BATCH, concurrency=1)
    parallel = await adispatch(router, BATCH, concurrency=3)
    again = await adispatch(router, BATCH, concurrency=8)

    assert shape(serial) == shape(parallel) == shape(again)
    assert not serial.advance, "курсор не должен сдвигаться при упавшем обработчике"
    # Порядок явный, а не только «совпадает с последовательным». Иначе проверка
    # прошла бы и на реализации, которая одинаково перемешивает оба прогона.
    assert [e.id for e in parallel.failed] == ["chat:b:1"]
    assert [e.id for e in parallel.delivered] == [
        "chat:a:0",
        "chat:a:1",
        "chat:a:2",
        "chat:b:0",
        # chat:b:1 упал, но соседи по ключу от этого не пропадают: отказ одного
        # события отменяет сдвиг курсора, а не остаток партии.
        "chat:b:2",
        "chat:c:0",
        "chat:c:1",
        "chat:c:2",
    ]


async def test_platform_error_is_the_same_one() -> None:
    """Проверяет, что ошибка площадки выбирается одна и та же.

    Первая ошибка площадки уходит вызывающему, и выбирать её планировщиком
    значило бы отдавать при каждом запуске другую.

    Returns:
        None
    """

    async def handler(event: Event) -> None:
        """Поднимает ошибку площадки на первых событиях двух разных ключей.

        Args:
            event (Event): Событие.

        Returns:
            None

        Raises:
            SessionExpiredError: На первом событии ключей a и c.
        """
        await asyncio.sleep(0.02 if event.ordering_key == "chat:a" else 0.0)
        if event.payload["n"] == 0 and event.ordering_key in ("chat:a", "chat:c"):
            raise SessionExpiredError(f"сессия истекла на {event.ordering_key}")

    router = Router()
    router.on()(handler)

    serial = await adispatch(router, BATCH, concurrency=1)
    parallel = await adispatch(router, BATCH, concurrency=3)

    assert serial.fatal is not None
    assert parallel.fatal is not None
    assert str(serial.fatal) == str(parallel.fatal), (
        "выбранная ошибка площадки зависит от того, кто успел раньше"
    )
    # Ключ a стоит в партии первым и ждёт дольше всех. Реализация, берущая ту
    # ошибку, что пришла раньше, выбрала бы c - и эта строка её поймает.
    assert "chat:a" in str(parallel.fatal)


async def test_a_single_key_is_not_reordered() -> None:
    """Проверяет, что партия из одного ключа остаётся последовательной.

    Одновременность здесь не только бесполезна, но и запрещена: весь смысл ключа
    в том, что внутри него порядок есть.

    Returns:
        None
    """
    seen: list[int] = []

    async def handler(event: Event) -> None:
        """Записывает номер события.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        await asyncio.sleep(0.01 if event.payload["n"] == 0 else 0)
        seen.append(event.payload["n"])

    router = Router()
    router.on()(handler)
    one_key = tuple(_event("chat:a", n) for n in range(4))
    await adispatch(router, one_key, concurrency=8)

    assert seen == [0, 1, 2, 3]


@pytest.mark.parametrize("concurrency", [0, 1, -3])
async def test_non_positive_concurrency_means_serial(concurrency: int) -> None:
    """Проверяет, что бессмысленное значение не включает одновременность.

    Args:
        concurrency (int): Проверяемое значение.

    Returns:
        None
    """
    seen: list[str] = []

    async def handler(event: Event) -> None:
        """Записывает ключ события.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        await asyncio.sleep(0)
        seen.append(event.id)

    router = Router()
    router.on()(handler)
    await adispatch(router, BATCH, concurrency=concurrency)

    assert seen == [event.id for event in BATCH]


@pytest.mark.asyncio
async def test_hung_handler_does_not_hang_the_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что задумавшийся обработчик отпускается по времени.

    Предел был объявлен спецификацией и не применялся нигде. Обработчик, ушедший
    в вечное ожидание - забыл таймаут на своём запросе, залип на блокировке в
    базе, - останавливал цикл наблюдения целиком. Ни исключения, ни строки в
    журнале: клиент просто переставал ходить на площадку, и внешне это
    неотличимо от «ничего не происходит».

    Предел подменяется на короткий: ждать настоящие тридцать секунд в наборе
    нельзя.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    import funora._watch as watch_module

    monkeypatch.setattr(watch_module, "HANDLER_TIMEOUT_MS", 50)

    router = Router()
    started = asyncio.Event()

    @router.on(EventType.MESSAGE_CREATED)
    async def hang(event: Event) -> None:
        """Не возвращается никогда.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        started.set()
        await asyncio.sleep(3600)

    result = await asyncio.wait_for(adispatch(router, (_event("chat:1", 1),)), timeout=5)

    assert started.is_set(), "обработчик даже не начался - проверка не о том"
    assert result.failed, "зависший обработчик не отмечен как непринявший"
    assert not result.advance, "курсор сдвинулся при зависшем обработчике"
    assert result.fatal is None, (
        "отказ по времени объявлен условием площадки - тогда один задумавшийся "
        "обработчик останавливает наблюдение целиком"
    )
    assert result.errors and isinstance(result.errors[0], HandlerTimeoutError)


@pytest.mark.asyncio
async def test_handler_within_the_limit_is_not_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что уложившийся обработчик не отменяется.

    Обратная сторона: предел, поставленный слишком рьяно, рубит нормальную
    работу. Обработчик, отвечающий покупателю, ходит в сеть, и доли секунды для
    него - обычное дело.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    import funora._watch as watch_module

    monkeypatch.setattr(watch_module, "HANDLER_TIMEOUT_MS", 5000)

    router = Router()
    done: list[int] = []

    @router.on(EventType.MESSAGE_CREATED)
    async def slow(event: Event) -> None:
        """Работает заметное время, но укладывается.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        await asyncio.sleep(0.05)
        done.append(1)

    result = await adispatch(router, (_event("chat:1", 1),))

    assert done == [1]
    assert result.advance and not result.failed


@pytest.mark.asyncio
async def test_concurrency_above_the_declared_limit_is_refused() -> None:
    """Проверяет отказ на одновременности выше объявленного предела.

    Предел объявлен спецификацией и до сих пор не применялся: вызывающий мог
    попросить хоть тысячу. Тысяча одновременных обработчиков - это тысяча
    одновременных соединений с чужой базой у него и, что важнее, тысяча
    параллельных реакций на площадке.

    Отказ, а не тихое понижение: понизить молча значило бы дать вызывающему
    неверное представление о том, как работает его код.

    Returns:
        None
    """
    from funora.budget import MAX_CONCURRENT_HANDLERS
    from funora.errors import ConfigurationError

    router = Router()
    router.on()(lambda event: None)
    events = (_event("chat:1", 1), _event("chat:2", 2))

    with pytest.raises(ConfigurationError, match=str(MAX_CONCURRENT_HANDLERS)):
        await adispatch(router, events, concurrency=MAX_CONCURRENT_HANDLERS + 1)


@pytest.mark.asyncio
async def test_concurrency_at_the_limit_is_allowed() -> None:
    """Проверяет, что предел не отсекает сам себя.

    Ошибка на единицу здесь стоила бы дороже обычного: вызывающий, взявший
    число прямо из спецификации, получил бы отказ.

    Returns:
        None
    """
    from funora.budget import MAX_CONCURRENT_HANDLERS

    router = Router()
    seen: list[int] = []
    router.on()(lambda event: seen.append(event.payload["n"]))
    events = (_event("chat:1", 1), _event("chat:2", 2))

    result = await adispatch(router, events, concurrency=MAX_CONCURRENT_HANDLERS)
    assert sorted(seen) == [1, 2]
    assert result.advance
