"""Проверки слоя бота: очередь исходящих и разбор её в потоке наблюдения.

ЧТО ЗДЕСЬ ЗАЩИЩАЕТСЯ. Клиент не защищён ни одной блокировкой. У ограничителя
исходящих проверка и запись не атомарны, у бюджета - тоже. Второй поток,
зовущий отправку напрямую, разрывает обе пары, и проявится это не отказом и не
исключением, а недосчётом предела: ограничитель решит, что за час ушло меньше,
чем ушло.

Такую поломку набор поймать не может - она вероятностная. Поймать он может
другое: что порядок, при котором её не бывает, вправду соблюдается. Отправка
идёт из потока наблюдения, из чужого потока она отвергается вслух, а задания
берутся из очереди по нескольку за паузу.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Final

import pytest

import funora._client as client_module
import funora._engine as engine_module
from funora._client import Client
from funora._transport import Observation
from funora._watch import Router
from funora.bot import Bot, Outbox, SendCommand
from funora.errors import UsageError
from funora.send_outcome import SendOutcome

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

NODE_ID: Final[str] = "283028758"


def _thread_html() -> str:
    """Читает снимок переписки с числовой позицией последнего сообщения.

    Возвращает:
        str: разметка страницы диалога.
    """
    html = (FIXTURES / "chat-thread.v8.logged.ru.skeleton.txt").read_text(encoding="utf-8")
    at = html.index("contact-item active")
    end = html.index(">", at)
    row = html[at:end]
    fixed = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="2010613313"', row, count=1)
    assert fixed != row, "позиция не подставилась"
    return html[:at] + fixed + html[end:]


def _observation(html: str, *, url: str) -> Observation:
    """Собирает наблюдение, каким его отдаёт транспорт.

    Аргументы:
        html (str): тело ответа.
        url (str): конечный адрес.

    Возвращает:
        Observation: наблюдение.
    """
    body = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=url,
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(body),
        declared_length=len(body),
    )


class _Tape:
    """Подставной транспорт: заказы, диалоги, переписка и приём отправки."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.submitted: list[dict[str, str]] = []
        self.threads: list[int] = []

    def fetch(self, path: str) -> Observation:
        """Отдаёт страницу по пути.

        Аргументы:
            path (str): запрошенный путь.

        Возвращает:
            Observation: наблюдение.
        """
        self.paths.append(path)
        if path.startswith("/orders"):
            return _observation(
                (FIXTURES / "orders-trade.logged.ru.skeleton.txt").read_text(encoding="utf-8"),
                url="https://funpay.com/orders/trade",
            )
        if "node=" in path:
            self.threads.append(len(self.paths))
            return _observation(_thread_html(), url=f"https://funpay.com/chat/?node={NODE_ID}")
        return _observation(
            (FIXTURES / "chat.logged.ru.skeleton.txt").read_text(encoding="utf-8"),
            url="https://funpay.com/chat/",
        )

    def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]) -> Observation:
        """Принимает отправку и отвечает подтверждением.

        Аргументы:
            path (str): путь обращения.
            fields (dict[str, str]): поля формы.
            headers (dict[str, str]): заголовки.

        Возвращает:
            Observation: наблюдение с ответом канала.
        """
        self.submitted.append(dict(fields))
        answer = {
            "response": {},
            "objects": [
                {
                    "type": "chat_node",
                    "id": int(NODE_ID),
                    "data": {"node": {"name": "users-1-2"}, "messages": [{"id": 1, "html": ""}]},
                }
            ],
        }
        return _observation(json.dumps(answer), url="https://funpay.com/runner/")

    def close(self) -> None:
        """Закрывает подставной транспорт.

        Возвращает:
            None
        """


def test_a_command_from_another_thread_is_sent_by_the_watching_thread(
    no_clock: list[float],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверяет то, ради чего слой и написан.

    Посторонний поток кладёт задание, наблюдение его отправляет. Проверяется не
    только факт отправки, но и ПОТОК, в котором она произошла: если отправить
    из чужого - счёт ограничителя портится молча.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.
        monkeypatch (pytest.MonkeyPatch): механизм подмены.

    Возвращает:
        None
    """
    from funora._client import ChatsService

    tape = _Tape()
    where: list[int] = []
    original = ChatsService.send_text

    def watched(self: ChatsService, *args: object, **kwargs: object) -> object:
        """Запоминает поток отправки.

        Возвращает:
            object: то же, что и настоящая отправка.
        """
        where.append(threading.get_ident())
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ChatsService, "send_text", watched)

    # Файл состояния обязателен: без долговечного реестра отправка отказывает,
    # и это требование контракта, а не строгость проверки.
    with Client(transport=tape, state_path=tmp_path / "state.json") as client:  # type: ignore[arg-type]
        client.engine._state.outbound.note_incoming(
            NODE_ID, at_ms=int(datetime.now(UTC).timestamp() * 1000)
        )
        bot = Bot(client, Router())

        stranger = threading.Thread(
            target=bot.send,
            args=(NODE_ID, "здравствуйте"),
            kwargs={"idempotency_key": "k1"},
        )
        stranger.start()
        stranger.join()
        posted_from = stranger.ident

        assert bot.outbox.pending == 1, "задание не легло в очередь"

        bot.run(max_iterations=2)
        runner = threading.get_ident()

    assert tape.submitted, "задание из чужого потока не ушло"
    assert where == [runner], (
        f"отправка случилась в потоке {where}, а наблюдение шло в {runner}. "
        "Отправка из чужого потока портит счёт ограничителя молча"
    )
    assert posted_from != runner, "задание клали из того же потока - проверка ничего не различает"
    assert bot.sent == 1


def test_sending_directly_from_another_thread_is_refused(no_clock: list[float]) -> None:
    """Требует громкого отказа на прямую отправку из чужого потока.

    Молчаливая гонка хуже громкого отказа: она не роняет ничего и не бросает
    исключений, она портит счёт предела - а объясняет это площадка.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape) as client:  # type: ignore[arg-type]
        # Переписка СОГРЕТА нарочно. Не согрей её - и отправку отверг бы
        # ограничитель исходящих, тоже с UsageError, и проверка проходила бы
        # даже со снятой защитой потока. Ровно это и показала мутация.
        client.engine._state.outbound.note_incoming(
            NODE_ID, at_ms=int(datetime.now(UTC).timestamp() * 1000)
        )
        bot = Bot(client, Router())
        bot.outbox.claim()

        failed: list[BaseException] = []

        def stranger() -> None:
            """Пробует отправить из чужого потока.

            Возвращает:
                None
            """
            try:
                bot.send_now(NODE_ID, "мимо очереди")
            except BaseException as exc:  # noqa: BLE001 - отказ и есть предмет проверки
                failed.append(exc)

        thread = threading.Thread(target=stranger)
        thread.start()
        thread.join()

    assert failed, "прямая отправка из чужого потока не отвергнута вовсе"
    assert isinstance(failed[0], UsageError), f"отвергнута не тем отказом: {failed[0]!r}"
    assert "не из потока наблюдения" in str(failed[0]), (
        f"отвергнуто по другой причине: {failed[0]}. Проверка требует именно "
        "защиты потока, а не отказа ограничителя - иначе она проходит и без неё"
    )
    assert not tape.submitted, "запрос ушёл, хотя вызов был отвергнут"


def test_the_same_key_is_not_sent_twice() -> None:
    """Требует, чтобы повтор ключа не давал второго сообщения.

    Отправка необратима, а перезапуск процесса, повтор события и нажатие кнопки
    дважды - обычные вещи.

    Возвращает:
        None
    """
    outbox = Outbox()
    first = outbox.put(SendCommand(chat_id=NODE_ID, text="раз", idempotency_key="k"))
    second = outbox.put(SendCommand(chat_id=NODE_ID, text="раз", idempotency_key="k"))

    assert first.duplicate is False
    assert second.duplicate is True, "повтор ключа принят как новое задание"
    assert outbox.pending == 1, f"в очереди {outbox.pending} заданий вместо одного"


def test_a_full_outbox_refuses_out_loud() -> None:
    """Требует отказа на переполненной очереди, а не молчаливой потери.

    Возвращает:
        None
    """
    outbox = Outbox(max_pending=2)
    for index in range(2):
        outbox.put(SendCommand(chat_id=NODE_ID, text="раз", idempotency_key=f"k{index}"))

    with pytest.raises(UsageError, match="переполнена"):
        outbox.put(SendCommand(chat_id=NODE_ID, text="раз", idempotency_key="k9"))

    # Ключ отвергнутого задания не запомнен: иначе повторная попытка положить
    # его, когда очередь разгрузится, была бы отброшена как повтор.
    outbox.take(2)
    again = outbox.put(SendCommand(chat_id=NODE_ID, text="раз", idempotency_key="k9"))
    assert again.duplicate is False, "отвергнутый ключ запомнен и больше не принимается"


def test_the_refusal_reaches_the_thread_that_asked(no_clock: list[float], tmp_path: Path) -> None:
    """Требует, чтобы отказ отправки дошёл до положившего задание.

    Отказ случается в потоке наблюдения, где его некому ловить. Не передав его
    в квитанцию, мы потеряли бы его совсем: телеграм-бот показал бы человеку,
    что сообщение ушло.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape, state_path=tmp_path / "state.json") as client:  # type: ignore[arg-type]
        bot = Bot(client, Router())
        # Переписка НЕ согрета, признак холода не объявлен - ограничитель обязан
        # отвергнуть задание.
        ticket = bot.send(NODE_ID, "здравствуйте", idempotency_key="k1")
        bot.run(max_iterations=2)

    assert ticket.ready, "квитанция не закрыта: положивший задание ждал бы вечно"
    with pytest.raises(UsageError, match="ограничитель исходящих"):
        ticket.wait(timeout=0)
    assert bot.refused == 1
    assert not tape.submitted, "отвергнутое задание всё же ушло"


def test_one_refusal_does_not_stop_the_watch(no_clock: list[float]) -> None:
    """Требует, чтобы отказ одного задания не ронял наблюдение.

    Иначе цикл встаёт из-за одной чужой кнопки.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape) as client:  # type: ignore[arg-type]
        bot = Bot(client, Router())
        bot.send(NODE_ID, "первое", idempotency_key="k1")
        bot.run(max_iterations=3)

    assert bot.refused == 1
    assert len([one for one in tape.paths if one.startswith("/orders")]) == 3, (
        "наблюдение не сделало трёх шагов - отказ задания его остановил"
    )


def test_no_more_than_the_limit_is_sent_per_pause(no_clock: list[float]) -> None:
    """Требует предела на число отправок за паузу.

    Одна отправка при неоднозначном ответе тянет за собой сверку - три чтения
    переписки с паузами до восьми секунд. Без предела один разбор очереди
    растянул бы паузу на минуту, и наблюдение всё это время стояло бы.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape) as client:  # type: ignore[arg-type]
        client.engine._state.outbound.note_incoming(
            NODE_ID, at_ms=int(datetime.now(UTC).timestamp() * 1000)
        )
        bot = Bot(client, Router(), max_sends_per_idle=2)
        for index in range(5):
            bot.send(NODE_ID, f"строка {index}", idempotency_key=f"k{index}")

        assert bot.outbox.pending == 5
        bot.run(max_iterations=1)
        left = bot.outbox.pending

    assert left == 3, f"за паузу разобрано {5 - left} заданий при пределе 2"


def test_draining_does_not_stretch_the_polling_interval(no_clock: list[float]) -> None:
    """Требует вычитать разбор очереди из паузы, а не добавлять к ней.

    Иначе чем больше работы, тем реже наблюдение - ровно наоборот тому, что
    нужно: очередь наполняется как раз тогда, когда покупатели пишут.

    Проверяется сравнением с прогоном БЕЗ разбора. Сравнивать с числом из
    спецификации нельзя: расписание само замедляется в тишине, и очередной
    множитель сделал бы проверку ложной, не тронув поведения.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    slow = 0.4

    def watch(hook: object) -> list[float]:
        """Прокручивает наблюдение и возвращает проспанное.

        Аргументы:
            hook (object): крючок паузы либо None.

        Возвращает:
            list[float]: длительности сна этого прогона.
        """
        before = len(no_clock)
        with Client(transport=_Tape()) as client:  # type: ignore[arg-type]
            client.run(
                client.engine.watch(Router(), max_iterations=2),
                router=Router(),
                on_idle=hook,  # type: ignore[arg-type]
            )
        return no_clock[before:]

    def linger(pause_ms: int) -> None:
        """Изображает долгий разбор очереди.

        Аргументы:
            pause_ms (int): длительность паузы.

        Возвращает:
            None
        """
        client_module.sleep(slow)

    plain = watch(None)
    with_drain = watch(linger)

    assert plain, "прогон без разбора не проспал ни разу"
    # Без разбора: одна запись на паузу. С разбором: сон самого разбора, потом
    # остаток паузы. Пауз столько же, а записей вдвое больше.
    assert len(with_drain) == 2 * len(plain), (with_drain, plain)

    rests = with_drain[1::2]
    for index, (rest, whole) in enumerate(zip(rests, plain, strict=True)):
        assert rest == pytest.approx(whole - slow), (
            f"пауза {index}: без разбора {whole} с, с разбором остаток {rest} с. "
            f"Потраченное не вычлось - темп опроса едет на {slow} с за шаг"
        )


def test_a_confirmed_send_reaches_the_ticket(no_clock: list[float], tmp_path: Path) -> None:
    """Проверяет, что исход отправки доходит до положившего задание.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape, state_path=tmp_path / "state.json") as client:  # type: ignore[arg-type]
        client.engine._state.outbound.note_incoming(
            NODE_ID, at_ms=int(datetime.now(UTC).timestamp() * 1000)
        )
        bot = Bot(client, Router())
        ticket = bot.send(NODE_ID, "здравствуйте", idempotency_key="k1")
        bot.run(max_iterations=2)

    result = ticket.wait(timeout=0)
    assert result is not None, "исход не дошёл до квитанции"
    assert result.outcome in set(SendOutcome)
    assert tape.submitted, "отправка не дошла до канала"
    request = json.loads(tape.submitted[0]["request"])
    assert request["data"]["content"] == "здравствуйте", request


@pytest.fixture
def no_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет сон счётчиком и двигает монотонные часы вместе с ним.

    Аргументы:
        monkeypatch (pytest.MonkeyPatch): механизм подмены.

    Возвращает:
        list[float]: длительности, которые цикл собирался проспать.
    """
    slept: list[float] = []
    started = monotonic()
    offset = [0.0]

    def fake_sleep(seconds: float) -> None:
        """Считает паузу и продвигает часы.

        Аргументы:
            seconds (float): сколько цикл собирался проспать.

        Возвращает:
            None
        """
        slept.append(seconds)
        offset[0] += seconds

    def fake_monotonic() -> float:
        """Возвращает время с учётом проспанного.

        Возвращает:
            float: монотонные секунды.
        """
        return started + offset[0]

    monkeypatch.setattr(client_module, "sleep", fake_sleep)
    monkeypatch.setattr(client_module, "monotonic", fake_monotonic)
    monkeypatch.setattr(engine_module, "monotonic", fake_monotonic)
    return slept
