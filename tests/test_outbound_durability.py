"""Проверки долговечности реестра отправок.

ЧТО ЗДЕСЬ ЗАЩИЩАЕТСЯ. Пределы отправки часовые, а память процесса обнуляется
перезапуском. Реестр, живущий только в памяти, превращает тридцать сообщений в
час в тридцать на КАЖДЫЙ ЗАПУСК: бот под супервизором, который падает и
поднимается, обходит ограничитель полностью.

Механизм был написан наполовину. Методы snapshot и restore существовали и
проверялись поодиночке, признак durable стоял истинным по умолчанию, и не звал
их из рабочего кода никто. То есть контракт требовал отказывать без реестра, а
реализация отправляла и молчала.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from funora._client import Client
from funora._outbound import UNSAFE_SENDS_WITHOUT_LEDGER
from funora._state import StateFile
from funora._transport import Observation
from funora.budget import OUTBOUND_MESSAGES_PER_HOUR
from funora.errors import ConfigurationError, FunoraError

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
    """Подставной транспорт: страница диалога и приём отправки."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, str]] = []

    def fetch(self, path: str) -> Observation:
        """Отдаёт страницу диалога.

        Аргументы:
            path (str): запрошенный путь.

        Возвращает:
            Observation: наблюдение.
        """
        return _observation(_thread_html(), url=f"https://funpay.com/chat/?node={NODE_ID}")

    def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]) -> Observation:
        """Принимает отправку и отвечает ПОДТВЕРЖДЁННЫМ исходом.

        Имя узла берётся из самой просьбы, а не пишется наугад. Разойдись оно -
        и исход вышел бы неоднозначным, а за неоднозначным идёт сверка: три
        чтения переписки с паузами в одну, три и восемь секунд. Набор спал бы
        настоящие секунды и проверял бы заодно точность таймера.

        Аргументы:
            path (str): путь обращения.
            fields (dict[str, str]): поля формы.
            headers (dict[str, str]): заголовки.

        Возвращает:
            Observation: наблюдение с ответом канала.
        """
        self.submitted.append(dict(fields))
        asked = json.loads(fields["request"])["data"]["node"]
        answer = {
            "response": {},
            "objects": [
                {
                    "type": "chat_node",
                    "id": asked,
                    "data": {"node": {"name": asked}, "messages": [{"id": 1, "html": ""}]},
                }
            ],
        }
        return _observation(json.dumps(answer), url="https://funpay.com/runner/")

    def close(self) -> None:
        """Закрывает подставной транспорт.

        Возвращает:
            None
        """


def _now_ms() -> int:
    """Возвращает текущий момент стенными миллисекундами.

    Возвращает:
        int: миллисекунды от эпохи.
    """
    return int(datetime.now(UTC).timestamp() * 1000)


def _warm(client: Client) -> None:
    """Объявляет переписку тёплой, чтобы не мешал признак холода.

    Аргументы:
        client (Client): клиент.

    Возвращает:
        None
    """
    client.engine._state.outbound.note_incoming(NODE_ID, at_ms=_now_ms())


def test_sending_without_a_ledger_is_refused() -> None:
    """Требует отказа отправки без долговечного реестра.

    Контракт называет это fail_closed и объясняет чем: без реестра защита
    снимается перезапуском процесса.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape) as client:  # type: ignore[arg-type]
        _warm(client)
        # Именно ошибка НАСТРОЙКИ, а не исчерпанного бюджета. Разница
        # практическая: исчерпанный бюджет зовёт подождать, а ждать здесь
        # бесполезно ни секунду, ни час - настроить надо клиента.
        with pytest.raises(ConfigurationError, match="реестр"):
            client.chats.send_text(NODE_ID, "здравствуйте")

    assert not tape.submitted, "запрос ушёл, хотя реестра нет"


def test_the_escape_hatch_works_and_leaves_a_mark(tmp_path: Path) -> None:
    """Требует, чтобы послабление работало и было ВИДНО.

    Снять защиту можно. Снять её незаметно нельзя: контракт требует отметки в
    состоянии здоровья, и отметка эта - не украшение, а единственный способ
    узнать со стороны, что предел больше не соблюдается.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    tape = _Tape()
    with Client(transport=tape, unsafe_sends_without_ledger=True) as client:  # type: ignore[arg-type]
        _warm(client)
        client.chats.send_text(NODE_ID, "здравствуйте")
        marks = frozenset(client.engine._unsafe)

    assert tape.submitted, "послабление не сработало"
    assert UNSAFE_SENDS_WITHOUT_LEDGER in marks, (
        "защита снята, а следа не осталось: узнать об этом со стороны нечем"
    )


def test_the_mark_is_set_by_fact_not_by_request(tmp_path: Path) -> None:
    """Требует ставить отметку по ФАКТУ снятия защиты, а не по просьбе.

    Попросивший послабление и передавший файл состояния защиту не снимал.
    Сказать о нём обратное значило бы соврать в отчёте о здоровье - и приучить
    читателя не верить отметке.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    with Client(  # type: ignore[arg-type]
        transport=_Tape(),
        state_path=tmp_path / "state.json",
        unsafe_sends_without_ledger=True,
    ) as client:
        assert UNSAFE_SENDS_WITHOUT_LEDGER not in client.engine._unsafe, (
            "отметка поставлена при живом реестре: защита не снималась"
        )


def test_the_ledger_survives_a_restart(tmp_path: Path) -> None:
    """Требует, чтобы отправка пережила перезапуск процесса.

    Это главная проверка набора. Обнулись реестр - и часовая квота стала бы
    квотой на запуск.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"

    with Client(transport=_Tape(), state_path=state) as first:  # type: ignore[arg-type]
        _warm(first)
        first.chats.send_text(NODE_ID, "первое")
        before = len(first.engine._state.outbound.snapshot()["sent"])

    assert before == 1, before
    assert state.is_file(), "файл состояния не записан"

    # Второй клиент - это и есть перезапуск процесса.
    with Client(transport=_Tape(), state_path=state) as second:  # type: ignore[arg-type]
        after = second.engine._state.outbound.snapshot()["sent"]

    assert len(after) == 1, (
        f"после перезапуска в реестре {len(after)} отправок вместо одной: "
        "часовая квота обнулилась вместе с памятью"
    )
    assert after[0]["chat_id"] == NODE_ID


def test_the_hourly_limit_is_not_reset_by_a_restart(tmp_path: Path) -> None:
    """Требует, чтобы перезапуск не возвращал право на новые отправки.

    Проверка идёт не по числу записей, а по РЕШЕНИЮ ограничителя: записи можно
    хранить и не смотреть на них.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"

    with Client(transport=_Tape(), state_path=state) as first:  # type: ignore[arg-type]
        governor = first.engine._state.outbound
        now = _now_ms()
        governor.note_incoming(NODE_ID, at_ms=now)
        for index in range(OUTBOUND_MESSAGES_PER_HOUR):
            governor.record(f"chat{index}", now_ms=now, now_s=0.0)
        first.engine._save_ledger(now_ms=now)

    with Client(transport=_Tape(), state_path=state) as second:  # type: ignore[arg-type]
        second.engine._state.outbound.note_incoming(NODE_ID, at_ms=_now_ms())
        with pytest.raises(FunoraError) as failure:
            second.chats.send_text(NODE_ID, "тридцать первое")

    assert "messages_per_hour" in str(failure.value), (
        f"упёрлись не в часовой предел: {failure.value}. Значит реестр перезапуск не пережил"
    )


def test_the_ledger_does_not_clobber_the_cursor(tmp_path: Path) -> None:
    """Требует, чтобы запись реестра не затирала курсоры наблюдения.

    Файл состояния один, а владельцев у него двое: цикл пишет курсоры раз в шаг,
    отправка пишет реестр сразу. Запись целиком вместо правки отправила бы
    перезапуск в холодный старт - молча.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"
    file = StateFile(state)
    file.save({"cursor": {"orders": {"A": "paid"}}, "dedup": {"e1": 1}})

    with Client(transport=_Tape(), state_path=state) as client:  # type: ignore[arg-type]
        _warm(client)
        client.chats.send_text(NODE_ID, "здравствуйте")

    stored: dict[str, Any] = file.load()
    assert stored.get("cursor") == {"orders": {"A": "paid"}}, (
        f"курсор затёрт записью реестра: {stored.get('cursor')}"
    )
    assert stored.get("dedup") == {"e1": 1}, "гашение повторов затёрто"
    assert stored.get("outbound", {}).get("sent"), "реестр не записан"


def test_the_watch_loop_adopts_its_state_file_as_the_ledger(tmp_path: Path) -> None:
    """Требует, чтобы файл наблюдения служил и реестром отправок.

    Иначе бот, честно передавший state_path в наблюдение, всё равно отправлял бы
    без долговечного реестра - и отказывал бы себе сам, не понимая почему.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    from funora._watch import Router

    state = tmp_path / "state.json"
    with Client(transport=_Tape()) as client:  # type: ignore[arg-type]
        assert client.engine._state.outbound.durable is False, "реестр найден там, где его нет"

        # Достаточно начать наблюдение: усыновление происходит на входе в цикл.
        core = client.engine.watch(Router(), max_iterations=0, state_path=state)
        client.run(core, router=Router())

        assert client.engine._state.outbound.durable is True, (
            "цикл получил файл состояния и не сделал его реестром: отправка "
            "будет отказывать при живом файле"
        )
