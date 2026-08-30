"""Сквозная проверка автовыдачи: от появления заказа до ухода товара.

ЗАЧЕМ ОТДЕЛЬНЫЙ НАБОР, когда есть проверки решения и проверки очереди. Они
проверяют части, а ломается обычно стык. Здесь всё вместе и по-настоящему:
настоящий цикл наблюдения, настоящая очередь исходящих, настоящий файл
состояния, настоящий ограничитель.

Проверяется цепочка целиком:

    список продаж прочитан -> заказ новый -> событие порождено -> обработчик
    сопоставил заказ с лотом -> задание легло в очередь -> очередь разобрана в
    паузе -> запрос ушёл -> реестр записан на диск

И вторая половина, которая важнее первой: ПОВТОРНЫЙ ЗАПУСК того же не выдаёт
ничего.
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
from funora._observed import Observed
from funora._own_lots import OwnLot
from funora._state import StateFile
from funora._transport import Observation
from funora._watch import Router
from funora.bot import Bot, DeliveryPlan
from funora.events import EventType

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"

#: Диалог, в который уйдёт товар.
NODE_ID: Final[str] = "283028758"

#: Описание оплаченного заказа на снимке - первая строка таблицы.
PAID_DESCRIPTION: Final[str] = "T106:acops"

#: Описание ЗАКРЫТОГО заказа - вторая строка. Нужно, чтобы проверить ворота
#: состояния сквозным путём: лот и товар для него есть, и не выдаётся он
#: ровно потому, что заказ закрыт.
CLOSED_DESCRIPTION: Final[str] = "T107:acops"


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


class _Marketplace:
    """Подставная площадка: страницы по путям и приём отправки.

    Список продаж на ПЕРВОМ чтении отдаётся без первой строки, а дальше целиком.
    Так у цикла появляется НОВЫЙ заказ: первое чтение курсора не имеет и молчит
    по правилу первого чтения.
    """

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.submitted: list[dict[str, str]] = []
        self.orders_served = 0

    def fetch(self, path: str) -> Observation:
        """Отдаёт страницу по пути.

        Аргументы:
            path (str): запрошенный путь.

        Возвращает:
            Observation: наблюдение.
        """
        self.paths.append(path)
        if path.startswith("/orders"):
            self.orders_served += 1
            return _observation(self._orders(), url="https://funpay.com/orders/trade")
        if path.startswith("/chat/") and "node=" not in path:
            return _observation(
                (FIXTURES / "chat.logged.ru.skeleton.txt").read_text(encoding="utf-8"),
                url="https://funpay.com/chat/",
            )
        return _observation(_thread_html(), url=f"https://funpay.com/chat/?node={NODE_ID}")

    def _orders(self) -> str:
        """Отдаёт список продаж: сперва без первой строки, потом целиком.

        Возвращает:
            str: разметка списка продаж.
        """
        html = (FIXTURES / "orders-trade.logged.ru.skeleton.txt").read_text(encoding="utf-8")
        if self.orders_served > 1:
            return html

        # Первое чтение: убираем ДВЕ строки заказа - оплаченную и закрытую. На
        # втором обе окажутся новыми, и событий придёт два.
        #
        # Две, а не одна, нарочно: одно событие не проверяет ворота состояния.
        # При одном оплаченном заказе снятая проверка «оплачен ли» даёт тот же
        # исход, и мутация проходит незамеченной.
        #
        # Строка ищется по её селектору из контракта, а не по первой попавшейся
        # ссылке: первая ссылка документа лежит в шапке.
        cut = html
        for _ in range(2):
            at = cut.index('<a class="tc-item')
            end = cut.index("</a>", at) + len("</a>")
            cut = cut[:at] + cut[end:]

        assert cut.count('<a class="tc-item') == html.count('<a class="tc-item') - 2, (
            "строки заказов не вырезались"
        )
        return cut

    def submit(self, path: str, fields: dict[str, str], headers: dict[str, str]) -> Observation:
        """Принимает отправку и подтверждает её.

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
        """Закрывает подставную площадку.

        Возвращает:
            None
        """


def _lots() -> tuple[OwnLot, ...]:
    """Собирает собственные лоты, один из которых подойдёт заказу.

    Возвращает:
        tuple[OwnLot, ...]: Лоты.
    """
    missing: Observed[str] = Observed.missing("not_checked")

    def one(offer_id: str, description: str) -> OwnLot:
        """Собирает лот.

        Возвращает:
            OwnLot: Лот.
        """
        return OwnLot(
            offer_id=Observed.present(offer_id),
            offer_href=missing,
            server_text=missing,
            description_text=Observed.present(description),
            price_text=missing,
            currency_symbol_text=missing,
            sort_value=missing,
            row_index=0,
        )

    return (
        one("L1", PAID_DESCRIPTION),
        one("L2", CLOSED_DESCRIPTION),
        one("L3", "совсем другое"),
    )


def _run(state: Path, market: _Marketplace) -> tuple[int, list[str]]:
    """Прогоняет один «запуск процесса»: наблюдение с автовыдачей.

    Аргументы:
        state (Path): файл состояния.
        market (_Marketplace): подставная площадка.

    Возвращает:
        tuple[int, list[str]]: сколько запросов отправки ушло и решения.
    """
    reasons: list[str] = []
    router = Router()

    with Client(transport=market, state_path=state) as client:  # type: ignore[arg-type]
        bot = Bot(client, router)
        plan = DeliveryPlan(
            # Товар есть у ОБОИХ лотов. Закрытый заказ не выдастся не потому,
            # что выдавать нечего, а потому, что он закрыт: иначе ворота
            # состояния этой проверкой не проверялись бы вовсе.
            goods={"L1": "вот ваш ключ", "L2": "ключ от закрытого"},
            # Узел диалога на снимке не читается числом, и подставляется он
            # здесь: проверка о выдаче, а не о разборе страницы заказа.
            chat_of=lambda order_id: NODE_ID,
        )
        delivery = bot.deliveries(plan, on_hold=lambda one: reasons.append(one.reason))

        @router.on(EventType.ORDER_CREATED)
        def on_order(event: Any) -> None:
            """Пробует выдать по новому заказу.

            Аргументы:
                event (Any): событие о создании заказа.

            Возвращает:
                None
            """
            page = client.orders.list()
            for row in page.rows():
                if row.order_id == event.payload["order_id"]:
                    delivery.handle(row, _lots(), page_completeness=page.completeness)

        # Переписка согрета: покупатель написал сам. Иначе ограничитель
        # отвергнет ответ, и проверка проверяла бы его, а не выдачу.
        client.engine._state.outbound.note_incoming(
            NODE_ID, at_ms=int(datetime.now(UTC).timestamp() * 1000)
        )
        bot.run(max_iterations=3)

    return len(market.submitted), reasons


def test_an_order_appears_and_the_goods_go_out(no_clock: list[float], tmp_path: Path) -> None:
    """Проверяет цепочку целиком, от нового заказа до ушедшего запроса.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"
    market = _Marketplace()

    sent, reasons = _run(state, market)

    assert sent == 1, (
        f"ушло {sent} запросов вместо одного. Причины отказов: {reasons}. Пути: {market.paths}"
    )
    assert "status_not_paid" in reasons, (
        f"закрытый заказ не отвергнут по состоянию, причины: {reasons}. "
        "Товар для него задан, лот опознан - значит ворота состояния не "
        "сработали"
    )
    request = json.loads(market.submitted[0]["request"])
    assert request["data"]["content"] == "вот ваш ключ"

    stored = StateFile(state).load()
    assert stored["delivery"]["done"], "реестр выдач не записан на диск"
    assert stored["outbound"]["sent"], "реестр отправок не записан"
    assert stored.get("account"), "файл не закреплён за аккаунтом"


def test_a_restart_delivers_nothing_a_second_time(no_clock: list[float], tmp_path: Path) -> None:
    """ГЛАВНАЯ ПРОВЕРКА НАБОРА: перезапуск не выдаёт тот же заказ снова.

    Заказ в списке продаж остаётся оплаченным, курсор после перезапуска
    восстанавливается не всегда, а товар необратим. Единственное, что стоит
    между покупателем и вторым ключом, - реестр выдач на диске.

    Аргументы:
        no_clock (list[float]): счётчик пауз вместо сна.
        tmp_path (Path): временный каталог.

    Возвращает:
        None
    """
    state = tmp_path / "state.json"

    first, _ = _run(state, _Marketplace())
    assert first == 1, "первый запуск не выдал"

    # Второй «процесс»: тот же файл состояния, свежая площадка. Курсор нарочно
    # сбрасывается - так выглядит перезапуск после неполного чтения, при
    # котором заказ приходит новым во второй раз.
    stored = StateFile(state).load()
    stored.pop("cursor", None)
    stored.pop("dedup", None)
    StateFile(state).save(stored)

    second_market = _Marketplace()
    second, reasons = _run(state, second_market)

    assert second == 0, (
        f"после перезапуска ушло {second} запросов: товар выдан второй раз по тому же заказу"
    )
    assert "already_delivered" in reasons, f"отказа по реестру выдач не было, причины: {reasons}"


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
