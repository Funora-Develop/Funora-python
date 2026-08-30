"""Проверки направления сообщения и согревания переписки.

ЗАЧЕМ ЭТО ВООБЩЕ ЕСТЬ. Ограничитель исходящих считает переписку холодной, пока
не увидит входящего сообщения. Согревать его было некому: методы note_incoming и
note_event были написаны, проверены поодиночке и не вызывались из рабочего кода
ни разу.

Следствие было не мелким. Автоответ покупателю, который сам только что написал,
отвергался с cold_outreach_not_declared, а с явным признаком холодного обращения
упирался в три обращения в сутки. Автоответчика не существовало физически, и
узнать об этом можно было только попробовав.

Здесь проверяется вся цепочка: направление читается структурно, входящее греет
переписку, исходящее не греет, и после согревания отправка проходит БЕЗ признака
холодного обращения.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Final

import pytest

import funora._client as client_module
import funora._engine as engine_module
from funora._client import Client
from funora._diff import diff_thread, direction_of
from funora._observed import Observed
from funora._runner import take_anchor
from funora._thread import Message, parse_thread
from funora._transport import Observation
from funora._watch import Router
from funora.events import EventType

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

#: Снимок переписки форматом v8: в нём есть и свои сообщения, и чужие.
THREAD: Final[str] = "chat-thread.v8.logged.ru"

#: Диалог, в который цикл пойдёт дочитывать.
NODE_ID: Final[str] = "283028758"

WHEN: Final[datetime] = datetime(2026, 8, 30, tzinfo=UTC)


def _thread_html() -> str:
    """Читает снимок переписки с ЧИСЛОВОЙ позицией последнего сообщения.

    Скелет маскирует числа подписями, а отправка требует позицию числом. Без
    подстановки положительный случай отправки на фикстуре недостижим вовсе.

    Возвращает:
        str: разметка страницы диалога.
    """
    html = (FIXTURES / f"{THREAD}.skeleton.txt").read_text(encoding="utf-8")
    at = html.index("contact-item active")
    end = html.index(">", at)
    row = html[at:end]
    fixed = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="2010613313"', row, count=1)
    assert fixed != row, "позиция не подставилась"
    return html[:at] + fixed + html[end:]


def _observation(html: str, *, url: str = "https://funpay.com/chat/") -> Observation:
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


def _message(author: str | None) -> Message:
    """Собирает сообщение с заданным автором.

    Аргументы:
        author (str | None): адрес профиля автора либо None.

    Возвращает:
        Message: сообщение.
    """
    return Message(
        message_id=Observed.present("m1"),
        row_index=0,
        origin=Observed.present("human"),  # type: ignore[arg-type]
        author_name=Observed.missing("not_checked"),
        author_href=(
            Observed.present(author) if author is not None else Observed.missing("no_author")
        ),
        text=Observed.missing("not_checked"),
        time_text=Observed.missing("not_checked"),
        time_full_text=Observed.missing("not_checked"),
        external_links=Observed.missing("not_checked"),
    )


def test_the_trailing_slash_does_not_make_our_message_theirs() -> None:
    """Требует нормализовать адрес перед сравнением.

    Это не придирка к оформлению. На снимке адрес собственного профиля стоит
    БЕЗ завершающей косой черты, а у сообщений - С НЕЙ. Сравнение как есть
    объявило бы все собственные сообщения чужими, переписка грелась бы от
    нашего же ответа, а бот отвечал бы сам себе.

    Возвращает:
        None
    """
    html = _thread_html()
    own = take_anchor(html).own_href

    assert own and not own.endswith("/"), (
        f"на снимке адрес свой стоит как {own!r}: проверка держится на том, "
        "что косой черты у него нет, а у авторов она есть"
    )

    authors = {
        one.author_href.value
        for one in parse_thread(html, observed_at=WHEN, host="funpay.com").messages(
            accept_incomplete=True
        )
        if one.author_href.is_observed
    }
    assert f"{own}/" in authors, (
        "на снимке нет собственного сообщения с косой чертой - проверять нечего"
    )

    assert direction_of(_message(f"{own}/"), own) == "outbound"
    assert direction_of(_message(own), own) == "outbound"


def test_the_counterparty_is_inbound() -> None:
    """Требует объявить чужое сообщение входящим.

    Возвращает:
        None
    """
    assert direction_of(
        _message("https://funpay.com/users/999/"), "https://funpay.com/users/1"
    ) == ("inbound")


@pytest.mark.parametrize(
    ("author", "own"),
    [
        (None, "https://funpay.com/users/1"),
        ("https://funpay.com/users/999/", ""),
        (None, ""),
    ],
)
def test_without_both_sides_the_direction_is_unknown(author: str | None, own: str) -> None:
    """Требует честного незнания, когда сравнивать нечего.

    Ни отсутствие автора, ни отсутствие своего адреса не означают «писал не я».
    Объявить такое сообщение входящим значило бы согреть переписку по
    отсутствию опровержения - а тепло требует свидетельства.

    Аргументы:
        author (str | None): адрес автора.
        own (str): адрес собственного профиля.

    Возвращает:
        None
    """
    assert direction_of(_message(author), own) == "unknown"


def test_every_message_event_carries_a_direction() -> None:
    """Требует, чтобы направление было у КАЖДОГО события о сообщении.

    Поле объявлено контрактом обязательным. Событие без него не собирается по
    схеме, и получатель, написавший разбор по схеме, упал бы на первом же.

    Возвращает:
        None
    """
    html = _thread_html()
    thread = parse_thread(html, observed_at=WHEN, host="funpay.com")
    own = take_anchor(html).own_href

    events = diff_thread(frozenset(), thread, account_id="1", chat_id=NODE_ID, own_href=own)
    assert events, "на снимке не нашлось ни одного нового сообщения"

    seen = {str(one.payload["direction"]) for one in events}
    assert seen <= {"inbound", "outbound", "unknown"}, seen
    assert {"inbound", "outbound"} <= seen, (
        f"на снимке встретились только направления {sorted(seen)}: проверка "
        "требует обеих сторон, иначе она ничего не различает"
    )


def test_an_incoming_message_warms_the_dialog(no_clock: list[float]) -> None:
    """Требует, чтобы входящее сообщение согревало переписку.

    Это главная проверка набора. Без согревания ограничитель исходящих
    отвергает автоответ покупателю, который сам только что написал.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    router = Router()

    @router.on()
    def handle(event: object) -> None:
        return None

    with Client(transport=tape) as client:  # type: ignore[arg-type]
        assert client.engine._state.outbound.is_warm(NODE_ID, now_ms=_now_ms()) is False, (
            "переписка тёплая ещё до наблюдения - проверять нечего"
        )
        client.watch(router, max_iterations=4)
        warm = client.engine._state.outbound.is_warm(NODE_ID, now_ms=_now_ms())

    assert tape.threads_read, "цикл не пошёл дочитывать переписку - согревать было нечему"
    assert warm is True, (
        "входящее сообщение не согрело переписку. Ограничитель отвергнет "
        "автоответ покупателю, который написал сам"
    )


def test_our_own_message_does_not_warm_the_dialog(no_clock: list[float]) -> None:
    """Требует, чтобы СОБСТВЕННОЕ сообщение переписку не грело.

    Иначе ограничитель отменяет сам себя: первая отправка в холодную переписку
    делала бы её тёплой, и суточная квота холодных обращений не сработала бы ни
    разу.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape(only_our_own=True)
    router = Router()

    @router.on()
    def handle(event: object) -> None:
        return None

    with Client(transport=tape) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=4)
        warm = client.engine._state.outbound.is_warm(NODE_ID, now_ms=_now_ms())

    assert tape.threads_read, "цикл не пошёл дочитывать переписку"
    assert warm is False, (
        "переписка согрелась от нашего же сообщения. Так ограничитель отменяет "
        "сам себя: квота холодных обращений не сработает ни разу"
    )


def test_the_handler_answers_without_declaring_a_cold_outreach(no_clock: list[float]) -> None:
    """Проверяет то, ради чего всё и делалось: автоответ проходит.

    Обработчик отвечает покупателю прямо из цикла и НЕ объявляет обращение
    холодным - потому что оно и не холодное: покупатель написал сам.

    До согревания этот же вызов отвергался с cold_outreach_not_declared. Здесь
    проверяется не то, что сообщение дошло, - подставной канал отвечает что
    угодно, - а то, что ограничитель ПРОПУСТИЛ отправку.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.

    Возвращает:
        None
    """
    tape = _Tape()
    router = Router()
    outcome: list[Any] = []

    with Client(transport=tape) as client:  # type: ignore[arg-type]

        @router.on(EventType.MESSAGE_CREATED)
        def answer(event: Any) -> None:
            """Отвечает на входящее сообщение.

            Аргументы:
                event (Any): событие о новом сообщении.

            Возвращает:
                None
            """
            if event.payload.get("direction") != "inbound":
                return
            if outcome:
                return
            try:
                outcome.append(("ушло", client.chats.send_text(NODE_ID, "здравствуйте")))
            except Exception as exc:  # noqa: BLE001 - причина отказа и есть предмет проверки
                outcome.append(("отказ", type(exc).__name__, str(exc)))

        client.watch(router, max_iterations=4)

    assert outcome, "обработчик не увидел ни одного входящего сообщения"
    assert outcome[0][0] == "ушло", (
        f"ограничитель отверг автоответ: {outcome[0]}. Ровно это и означало, "
        "что автоответчика не существует"
    )
    assert tape.submitted, "отправка не дошла до канала"


def _now_ms() -> int:
    """Возвращает текущий момент стенными миллисекундами.

    Возвращает:
        int: миллисекунды от эпохи.
    """
    return int(datetime.now(UTC).timestamp() * 1000)


class _Tape:
    """Подставной транспорт: список продаж, список диалогов и переписка.

    Переписка на первом чтении отдаётся урезанной, а дальше целиком. Так у
    цикла появляется НОВОЕ сообщение: первое чтение курсора не имеет и молчит
    по правилу первого чтения.

    Аргументы:
        only_our_own (bool): отдавать ли на втором чтении только собственное
            новое сообщение. Нужно проверке, что своё сообщение не греет.
    """

    def __init__(self, *, only_our_own: bool = False) -> None:
        self._only_our_own = only_our_own
        self.paths: list[str] = []
        self.rounds = 0
        self.threads_read = 0
        self.submitted: list[tuple[str, dict[str, str]]] = []

    def fetch(self, path: str) -> Observation:
        """Отдаёт страницу по пути.

        Аргументы:
            path (str): запрошенный путь.

        Возвращает:
            Observation: наблюдение.
        """
        self.paths.append(path)
        if path.startswith("/orders"):
            self.rounds += 1
            return _observation(
                (FIXTURES / "orders-trade.logged.ru.skeleton.txt").read_text(encoding="utf-8"),
                url="https://funpay.com/orders/trade",
            )
        if "node=" in path or path.startswith("/chat/?"):
            self.threads_read += 1
            return _observation(self._thread(), url=f"https://funpay.com/chat/?node={NODE_ID}")
        return _observation(self._chats(), url="https://funpay.com/chat/")

    def _chats(self) -> str:
        """Отдаёт список диалогов, двигая позицию первой строки после круга.

        Идентификатор узла на снимке - подпись скелета вида T9:d#1, а чтение
        переписки требует букв и цифр. Подставляется пригодный: иначе цикл
        отбрасывает строку до сети и дочитывать не идёт вовсе.

        Возвращает:
            str: разметка списка диалогов.
        """
        html = (FIXTURES / "chat.logged.ru.skeleton.txt").read_text(encoding="utf-8")
        html = html.replace('data-id="T9:d#1"', f'data-id="{NODE_ID}"', 1)
        assert NODE_ID in html, "идентификатор узла не подставился"

        if self.rounds > 1:
            # Позиция последнего сообщения двигается КАЖДЫЙ круг, а не один раз.
            # Диалог должен быть дочитан дважды: первое чтение переписки курсора
            # не имеет и молчит по правилу первого чтения, и новым сообщение
            # становится только на втором.
            html = html.replace(
                'data-node-msg="T10:d#1"', f'data-node-msg="T10:d#{70 + self.rounds}"', 1
            )
        return html

    def _thread(self) -> str:
        """Отдаёт переписку: на втором чтении одно сообщение становится новым.

        Приём выбран вместо урезания страницы. Урезание правит разметку вслепую
        и ломает её незаметно; здесь же меняется РОВНО идентификатор одного
        сообщения, а чьё оно - известно по снимку и закреплено проверкой.

        Возвращает:
            str: разметка страницы диалога.
        """
        html = _thread_html()
        if self.threads_read < 2:
            return html

        # T18:adp#9 написано собеседником, T18:adp#10 - нами. Это прочитано со
        # снимка, а не угадано, и держится проверкой ниже.
        target = "T18:adp#10" if self._only_our_own else "T18:adp#9"
        renamed = html.replace(f'id="{target}"', 'id="T18:adp#900"', 1)
        assert renamed != html, f"сообщение {target} не нашлось на снимке"
        return renamed

    def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]) -> Observation:
        """Принимает отправку и отвечает подтверждением.

        Аргументы:
            path (str): путь обращения.
            fields (dict[str, str]): поля формы.
            headers (dict[str, str]): заголовки.

        Возвращает:
            Observation: наблюдение с ответом канала.
        """
        self.submitted.append((path, fields))
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


@pytest.fixture
def no_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет сон счётчиком и двигает монотонные часы вместе с ним.

    Бюджет пополняется по времени. Подмена, глотающая сон и оставляющая часы на
    месте, показывает ведро, которое не пополняется никогда.

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
    monkeypatch.setattr(engine_module, "monotonic", fake_monotonic)
    return slept
