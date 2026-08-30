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
from time import monotonic
from typing import Any, Final

import pytest

from funora._client import Client
from funora._outbound import UNSAFE_SENDS_WITHOUT_LEDGER
from funora._state import StateFile
from funora._transport import Observation
from funora.budget import OUTBOUND_MESSAGES_PER_HOUR
from funora.errors import ConfigurationError, CursorIncompatibleError, FunoraError

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
        """Отдаёт страницу по пути.

        Раздача по путям нужна проверкам, которые вправду крутят цикл: он
        читает список продаж и список диалогов, и страница диалога вместо них
        разобралась бы неполно.

        Аргументы:
            path (str): запрошенный путь.

        Возвращает:
            Observation: наблюдение.
        """
        if path.startswith("/orders"):
            return _observation(
                (FIXTURES / "orders-trade.logged.ru.skeleton.txt").read_text(encoding="utf-8"),
                url="https://funpay.com/orders/trade",
            )
        if path.startswith("/chat/") and "node=" not in path:
            return _observation(
                (FIXTURES / "chat.logged.ru.skeleton.txt").read_text(encoding="utf-8"),
                url="https://funpay.com/chat/",
            )
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

    МОНОТОННАЯ МЕТКА БЕРЁТСЯ НАСТОЯЩАЯ, а не нулевая. Нулевая сравнивалась бы с
    показанием часов машины, и на аптайме больше суток запись выглядела бы
    просроченной: проверка падала бы не от поломки, а от того, что машину давно
    не перезагружали.

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
            governor.record(f"chat{index}", now_ms=now, now_s=monotonic())
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


def test_the_watch_loop_does_not_wipe_other_owners_of_the_file(tmp_path: Path) -> None:
    """ЗАКРЫВАЕТ ДЕФЕКТ, найденный перепроверкой.

    У файла состояния несколько владельцев: курсоры и гашение пишет цикл,
    реестр отправок - отправка, реестр выдач - автовыдача. Цикл записывал файл
    ЦЕЛИКОМ, и чужие ключи стирались на каждом шаге.

    Стирались молча, при зелёном прогоне: реестр выдач исчезал, и товар уходил
    второй раз по заказу, который в списке продаж всё ещё оплачен.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    from funora._watch import Router

    state = tmp_path / "state.json"
    StateFile(state).save({"delivery": {"done": [{"order_id": "A1", "at_ms": 1}]}})

    with Client(transport=_Tape(), state_path=state) as client:  # type: ignore[arg-type]
        # Шаг делается НАСТОЯЩИЙ: сохранение состояния живёт в теле цикла, и
        # при нуле проходов оно не выполнится ни разу - проверка проверяла бы
        # ничего.
        client.run(
            client.engine.watch(Router(), max_iterations=1, state_path=state),
            router=Router(),
        )

    stored = StateFile(state).load()
    assert "delivery" in stored, (
        f"ключ реестра выдач стёрт наблюдением: осталось {sorted(stored)}. "
        "Товар уйдёт второй раз по уже выданному заказу"
    )
    assert stored["delivery"]["done"][0]["order_id"] == "A1"


def test_the_adopted_file_restores_the_delivery_ledger_too(tmp_path: Path) -> None:
    """Требует, чтобы усыновление файла циклом поднимало и реестр выдач.

    Путей к файлу два: через Client(state_path=...) и через watch(state_path=...).
    Проверять надо оба - выпади один, бот, передавший путь только наблюдению,
    остался бы без памяти о выданном.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    from funora._watch import Router

    state = tmp_path / "state.json"
    StateFile(state).save({"delivery": {"done": [{"order_id": "B7", "at_ms": 5}]}})

    # Клиент БЕЗ state_path: файл достанется ему только от наблюдения.
    with Client(transport=_Tape()) as client:  # type: ignore[arg-type]
        assert len(client.engine.delivered) == 0
        client.run(
            client.engine.watch(Router(), max_iterations=0, state_path=state), router=Router()
        )

        assert client.engine.delivered.seen("B7") is True, (
            "усыновлённый файл не поднял реестр выдач: бот, передавший путь "
            "только наблюдению, выдаст товар второй раз"
        )


def test_the_adoption_merges_the_ledger_instead_of_replacing_it(tmp_path: Path) -> None:
    """Требует, чтобы вход в цикл не обнулял накопленное в памяти.

    Усыновление читает файл и восстанавливает реестр. Восстановление ЗАМЕЩАЕТ
    содержимое, и потому накопленное до входа в цикл пропадало: квота
    обнулялась посреди работы, без всякого перезапуска.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    from funora._watch import Router

    state = tmp_path / "state.json"
    StateFile(state).save(
        {"outbound": {"sent": [{"chat_id": "из_файла", "at_ms": 1}], "incoming": {}}}
    )

    with Client(transport=_Tape(), unsafe_sends_without_ledger=True) as client:  # type: ignore[arg-type]
        governor = client.engine._state.outbound
        governor.record("в_памяти", now_ms=_now_ms(), now_s=0.0)
        assert len(governor.snapshot()["sent"]) == 1

        client.run(
            client.engine.watch(Router(), max_iterations=0, state_path=state), router=Router()
        )

        chats = {one["chat_id"] for one in client.engine._state.outbound.snapshot()["sent"]}

    assert chats == {"из_файла", "в_памяти"}, (
        f"после входа в цикл в реестре {sorted(chats)}: накопленное в памяти "
        "потеряно, и квота обнулилась без перезапуска"
    )


def test_the_ledger_is_pruned_even_without_a_single_send(tmp_path: Path) -> None:
    """ЗАКРЫВАЕТ ДЕФЕКТ, найденный перепроверкой.

    Прополку реестра звала одна отправка. Бот, который наблюдает и не пишет -
    а таких большинство, - не прополаывал его никогда: метки тепла копились в
    файле, переживали своё окно и с каждым шагом уезжали на диск заново.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    from funora._watch import Router
    from funora.budget import COLD_OUTREACH_WINDOW_MS

    state = tmp_path / "state.json"
    stale = _now_ms() - COLD_OUTREACH_WINDOW_MS * 3
    StateFile(state).save(
        {"outbound": {"sent": [], "incoming": {"давний": stale, "свежий": _now_ms()}}}
    )

    with Client(transport=_Tape(), state_path=state) as client:  # type: ignore[arg-type]
        client.run(
            client.engine.watch(Router(), max_iterations=1, state_path=state),
            router=Router(),
        )

    left = StateFile(state).load()["outbound"]["incoming"]
    assert "давний" not in left, (
        f"просроченная метка тепла осталась: {sorted(left)}. Реестр растёт "
        "столько же, сколько живёт аккаунт, и уезжает на диск каждый шаг"
    )
    assert "свежий" in left, "прополка унесла и свежую метку"


def test_a_ledger_of_another_account_is_refused(tmp_path: Path) -> None:
    """ЗАКРЫВАЕТ ПОСЛЕДНИЙ ДЕФЕКТ, найденный аудитом.

    Файл состояния сверял формат, семейство адаптера и версию канонической
    формы - и не сверял, ЧЬИ в нём записи.

    Цена не только в квоте. Хуже реестр ВЫДАННОГО: заказ второго аккаунта с тем
    же номером считался бы уже выданным, и товар покупателю не ушёл бы вовсе -
    без отказа, без строки в журнале, без единого следа.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"
    StateFile(state).save({"account": "https://funpay.com/users/111", "outbound": {}})

    with (
        Client(transport=_Tape(), state_path=state) as client,  # type: ignore[arg-type]
        pytest.raises(CursorIncompatibleError, match="другим аккаунтом|другому аккаунту"),
    ):
        client.chats.send_text(NODE_ID, "здравствуйте")

    assert not _Tape().submitted, "запрос ушёл под чужим реестром"


def test_the_file_is_stamped_with_the_account_on_first_use(tmp_path: Path) -> None:
    """Требует закрепить файл за аккаунтом, как только тот стал известен.

    Привязка ленивая по необходимости: аккаунт известен только из ответа
    площадки, а файл открывается в конструкторе. Зато закрепить его можно в тот
    момент, когда узнали, - и это раньше любой отправки.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"

    with Client(transport=_Tape(), state_path=state) as client:  # type: ignore[arg-type]
        assert not StateFile(state).load().get("account"), "файл закреплён до первого чтения"
        _warm(client)
        client.chats.send_text(NODE_ID, "здравствуйте")

    stamped = StateFile(state).load().get("account")
    assert stamped, "файл не закреплён за аккаунтом после отправки"
    assert "funpay.com/users/" in str(stamped), stamped


def test_the_same_account_keeps_working(tmp_path: Path) -> None:
    """Требует, чтобы свой же файл принимался без возражений.

    Защита, отвергающая всех, проходит проверку выше и делает SDK непригодным.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"

    # Переписки РАЗНЫЕ: пауза на одну переписку - тридцать секунд, и второй
    # запуск в тот же диалог упёрся бы в неё, а проверка не о ней.
    for node in ("283028758", "283028759"):
        with Client(transport=_Tape(), state_path=state) as client:  # type: ignore[arg-type]
            client.engine._state.outbound.note_incoming(node, at_ms=_now_ms())
            client.chats.send_text(node, "здравствуйте")

    stored = StateFile(state).load()
    assert stored.get("account"), "закрепление потерялось"
    assert len(stored["outbound"]["sent"]) == 2, (
        f"в реестре {len(stored['outbound']['sent'])} отправок вместо двух: "
        "второй запуск не дописал"
    )


def test_an_unreadable_identity_neither_binds_nor_refuses(tmp_path: Path) -> None:
    """Требует молчать, когда личность со страницы не читается.

    Сверять нечем: адрес собственного профиля снимается, только если оба узла
    меню дали один и тот же. Разошлись - адреса нет вовсе.

    ВЫБРАНА ТЕРПИМОСТЬ, и вот довод. Незнание личности случается от смены
    разметки, а не от смены аккаунта: у подменённого аккаунта личность как раз
    читается, и на ней сверка срабатывает. Отказывать при неизвестной личности
    значило бы ронять отправку от любой правки меню на площадке.

    Проверяется обе половины: не отказали И не закрепили. Закрепить неизвестным
    значило бы записать в файл пустоту и сделать его совместимым с чем угодно.

    Аргументы:
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"

    class _NoIdentity(_Tape):
        """Лента, отдающая страницу БЕЗ ссылки на собственный профиль."""

        def fetch(self, path: str) -> Observation:
            """Отдаёт страницу диалога со снятой ссылкой на свой профиль.

            Аргументы:
                path (str): запрошенный путь.

            Возвращает:
                Observation: наблюдение.
            """
            html = _thread_html().replace("user-link-dropdown", "user-link-was-dropdown")
            return _observation(html, url=f"https://funpay.com/chat/?node={NODE_ID}")

    with Client(transport=_NoIdentity(), state_path=state) as client:  # type: ignore[arg-type]
        _warm(client)
        # Отправка проходит: сверять нечем, и отказ здесь означал бы падение от
        # любой правки меню.
        client.chats.send_text(NODE_ID, "здравствуйте")

    stored = StateFile(state).load()
    assert not stored.get("account"), (
        f"файл закреплён неизвестной личностью: {stored.get('account')!r}. "
        "Такое закрепление делает его совместимым с чем угодно"
    )
    assert stored["outbound"]["sent"], "отправка не записалась"
