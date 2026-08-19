"""Проверки фасада клиента.

Сети здесь нет: транспорт подменяется подставным, отдающим заранее заготовленные
ответы из снимков. Сна тоже нет - его подменяет счётчик, иначе проверка повторов
шла бы столько же, сколько идут сами повторы, и её выключили бы первой.

Набор проверяет порядок шагов и решения, а не разбор: разбор проверен отдельно и
от сети не зависит вовсе.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import monotonic

import pytest

import funora._client as client_module
from funora._budget import Budget
from funora._chats import ChatsPage
from funora._client import Client
from funora._orders import Completeness
from funora._transport import Observation
from funora.capabilities import Capability, CapabilityState
from funora.errors import (
    BudgetExhaustedError,
    ConfigurationError,
    InvalidCredentialsError,
    NetworkError,
    RateLimitedError,
    SessionExpiredError,
    UnsupportedCapabilityError,
)

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


def _observation(html: str, **overrides: object) -> Observation:
    """Собирает результат обращения из готовой разметки.

    Args:
        html (str): Тело ответа.
        **overrides (object): Переопределения полей наблюдения.

    Returns:
        Observation: Наблюдение, пригодное для подстановки в клиент.
    """
    base = Observation(
        status=200,
        final_url="https://funpay.com/orders/trade",
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(html.encode("utf-8")),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


class _FakeFetcher:
    """Подставной транспорт, отдающий заготовленные ответы.

    Args:
        responses (list[Observation | Exception]): Что отдавать на каждое
            обращение по порядку. Исключение поднимается вместо ответа.
    """

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def fetch(self, path: str) -> Observation:
        """Отдаёт следующий заготовленный ответ.

        Args:
            path (str): Запрошенный путь. Не используется.

        Returns:
            Observation: Заготовленное наблюдение.

        Raises:
            Exception: Если в очереди стоит исключение.
        """
        self.calls += 1
        item = self._responses.pop(0) if self._responses else self._responses
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    def close(self) -> None:
        """Закрывает подставной транспорт.

        Returns:
            None
        """


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет сон счётчиком пауз.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        list[float]: Список длительностей, которые клиент собирался проспать.
    """
    slept: list[float] = []
    monkeypatch.setattr(client_module, "sleep", slept.append)
    return slept


def _client(responses: list[object]) -> Client:
    """Собирает клиент с подставным транспортом.

    Args:
        responses (list[object]): Заготовленные ответы.

    Returns:
        Client: Клиент, готовый к вызову.
    """
    return Client(transport=_FakeFetcher(responses))  # type: ignore[arg-type]


def test_complete_read_marks_capability_supported() -> None:
    """Проверяет запись состояния возможности по успешному чтению.

    Returns:
        None
    """
    with _client([_observation(_page("orders-trade.logged.ru"))]) as client:
        assert client.capability(Capability.ORDERS_LIST) is CapabilityState.SUPPORTED
        page = client.orders.list()
        assert page.completeness is Completeness.COMPLETE
        assert len(page.rows()) == 3
        assert client.capability(Capability.ORDERS_LIST) is CapabilityState.SUPPORTED


def test_partial_read_degrades_the_capability() -> None:
    """Проверяет, что неполное чтение переводит возможность в деградацию.

    Деградация не запрещает вызовы: часть данных читается, и терять её значило бы
    наказывать вызывающего за поломку вёрстки.

    Returns:
        None
    """
    broken = _page("orders-trade.logged.ru").replace(
        '<div class="tc-status text-primary">', '<div class="tc-gone">'
    )
    with _client([_observation(broken)]) as client:
        page = client.orders.list()
        assert page.completeness is Completeness.PARTIAL
        assert client.capability(Capability.ORDERS_LIST) is CapabilityState.DEGRADED
        assert client.capability(Capability.ORDERS_LIST).usable


def test_truncated_body_is_a_network_error(no_sleep: list[float]) -> None:
    """Проверяет обнаружение оборванного ответа.

    Страница, оборванная посреди таблицы, проходит и классификацию, и разбор:
    вызывающий получил бы половину заказов с нулём повреждений. Это
    правдоподобный неверный ответ, о неверности которого узнать неоткуда.

    Обрыв тела считается транспортным отказом, поэтому повторяется. Счётчик пауз
    здесь обязателен: без него набор ждёт настоящие секунды отступления, и его
    выключают первым.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    html = _page("orders-trade.logged.ru")
    truncated = _observation(html[: len(html) // 2], declared_length=len(html.encode("utf-8")))
    with _client([truncated] * 6) as client, pytest.raises(NetworkError):
        client.orders.list()
    assert no_sleep, "обрыв тела обязан повторяться, значит паузы были"


def test_guest_page_without_prior_session_is_invalid_credentials() -> None:
    """Проверяет разделение неверного секрета и истёкшей сессии.

    Сессия ни разу не подтверждалась, значит повторять и обновлять бессмысленно:
    попытка обновления только добавит подозрительных запросов к аккаунту,
    который и так под вопросом.

    Returns:
        None
    """
    guest = _observation(_page("orders-trade.guest.ru"))
    with _client([guest]) as client, pytest.raises(InvalidCredentialsError):
        client.orders.list()


def test_guest_page_after_valid_session_is_session_expired() -> None:
    """Проверяет тот же случай после подтверждённой сессии.

    Returns:
        None
    """
    good = _observation(_page("orders-trade.logged.ru"))
    guest = _observation(_page("orders-trade.guest.ru"))
    with _client([good, guest]) as client:
        client.orders.list()
        with pytest.raises(SessionExpiredError):
            client.orders.list()


def test_rate_limited_is_retried_and_respects_the_header(no_sleep: list[float]) -> None:
    """Проверяет повтор после превышения частоты.

    Проверяется заодно, что 429 не останавливает клиента навсегда: раньше этот
    код отображался в блокировку с закрытым замком.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    limited = _observation("", status=429, retry_after_ms=2000)
    good = _observation(_page("orders-trade.logged.ru"))
    with _client([limited, good]) as client:
        page = client.orders.list()
        assert page.completeness is Completeness.COMPLETE
        assert no_sleep == [2.0], "пауза обязана быть взята из заголовка"


def test_rate_limited_gives_up_after_the_policy_limit(no_sleep: list[float]) -> None:
    """Проверяет, что повторы не бесконечны.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    limited = _observation("", status=429, retry_after_ms=1000)
    with _client([limited] * 10) as client, pytest.raises(RateLimitedError):
        client.orders.list()


def test_unsupported_capability_blocks_before_the_network() -> None:
    """Проверяет, что ворота срабатывают до обращения к сети.

    Тратить запрос на заведомо запрещённый вызов бессмысленно, а на площадке,
    считающей запросы, ещё и вредно.

    Returns:
        None
    """
    with _client([]) as client:
        client._state.capabilities[Capability.ORDERS_LIST] = CapabilityState.UNSUPPORTED
        with pytest.raises(UnsupportedCapabilityError):
            client.orders.list()
        assert client._fetcher.calls == 0  # type: ignore[attr-defined]


def test_session_flag_flips_only_after_a_good_read() -> None:
    """Проверяет момент, когда сессия считается подтверждённой.

    Returns:
        None
    """
    with _client([_observation(_page("orders-trade.logged.ru"))]) as client:
        assert not client._state.session_ever_valid
        client.orders.list()
        assert client._state.session_ever_valid


def test_incomplete_page_still_needs_acknowledgement() -> None:
    """Проверяет, что фасад не признаёт неполноту за вызывающего.

    Returns:
        None
    """
    broken = _page("orders-trade.logged.ru").replace(
        '<div class="tc-status text-primary">', '<div class="tc-gone">'
    )
    with _client([_observation(broken)]) as client:
        page = client.orders.list()
        with pytest.raises(Exception, match="неполон"):
            page.rows()
        assert len(page.rows(accept_incomplete=True)) == 3


def test_client_needs_a_secret_or_a_transport() -> None:
    """Проверяет отказ при вызове без секрета и без транспорта.

    Повтор здесь не поможет: обратиться к площадке не от кого, и исправлять надо
    вызов, а не окружение.

    Returns:
        None
    """
    with pytest.raises(ConfigurationError):
        Client()


def test_budget_is_spent_per_request(no_sleep: list[float]) -> None:
    """Проверяет, что бюджет расходуется на каждый отправленный запрос.

    Расходуется именно запрос, а не логическая операция: повтор - тоже запрос.
    Считать иначе означало бы сделать шторм повторов бесплатным ровно в тот
    момент, когда площадке хуже всего.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    budget = Budget()
    good = _observation(_page("orders-trade.logged.ru"))
    with Client(transport=_FakeFetcher([good] * 5), budget=budget) as client:  # type: ignore[arg-type]
        before = budget.reserve(0.0)
        assert before.granted
        for _ in range(3):
            client.orders.list()

    spent = 0
    probe = Budget()
    while probe.reserve(0.0).granted:
        spent += 1
    remaining = 0
    while budget.reserve(0.0).granted:
        remaining += 1
    assert remaining < spent, "бюджет не израсходован ни на один запрос"


def test_budget_is_shared_between_clients() -> None:
    """Проверяет, что общий бюджет действительно общий.

    Площадке видна сетевая идентичность, а не то, сколько клиентов мы завели у
    себя. Заведи каждый свой бюджет - общий предел обходится тривиально.

    Returns:
        None
    """
    budget = Budget()
    good = _observation(_page("orders-trade.logged.ru"))
    now = monotonic()

    first = Client(transport=_FakeFetcher([good] * 50), budget=budget)  # type: ignore[arg-type]
    second = Client(transport=_FakeFetcher([good] * 50), budget=budget)  # type: ignore[arg-type]

    first.orders.list()
    after_first = 0
    probe = Budget()
    while probe.reserve(now).granted:
        after_first += 1

    second.orders.list()
    left = 0
    while budget.reserve(now).granted:
        left += 1

    assert left < after_first - 1, "два клиента расходовали разные бюджеты"

    first.close()
    second.close()


def test_exhausted_budget_does_not_send_the_request(no_sleep: list[float]) -> None:
    """Проверяет, что при исчерпании запрос не отправляется вовсе.

    В этом весь смысл ошибки: она означает решение SDK не ходить, а не ответ
    площадки.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    # Опустошать надо по тем же часам, которые спросит клиент. Опустошение в
    # момент ноль ничего не даёт: к настоящему монотонному моменту ведро успеет
    # восполниться, и проверка станет зелёной, ничего не проверив.
    budget = Budget(names=("write",))
    now = monotonic()
    while budget.reserve(now).granted:
        pass

    fetcher = _FakeFetcher([_observation(_page("orders-trade.logged.ru"))])
    with Client(transport=fetcher, budget=budget) as client:  # type: ignore[arg-type]
        with pytest.raises(BudgetExhaustedError):
            client.orders.list()
        assert fetcher.calls == 0, "запрос отправлен несмотря на исчерпанный бюджет"


def test_chats_list_reads_the_dialog_list() -> None:
    """Проверяет вторую операцию чтения.

    Returns:
        None
    """
    with _client([_observation(_page("chat.logged.ru"))]) as client:
        page = client.chats.list()
        assert isinstance(page, ChatsPage)
        assert page.completeness is Completeness.COMPLETE
        assert len(page.rows()) == 47


def test_chats_and_orders_share_the_capability_gate() -> None:
    """Проверяет, что вторая операция проходит те же ворота.

    Порядок шагов общий для всех операций чтения намеренно: скопированный
    порядок расходится, и две операции одного клиента начинают вести себя
    по-разному на одной и той же странице.

    Returns:
        None
    """
    with _client([]) as client:
        client._state.capabilities[Capability.CHATS_LIST] = CapabilityState.UNSUPPORTED
        with pytest.raises(UnsupportedCapabilityError):
            client.chats.list()
        assert client._fetcher.calls == 0  # type: ignore[attr-defined]


def test_capabilities_are_tracked_separately() -> None:
    """Проверяет, что деградация одной операции не задевает другую.

    Сломанная разметка заказов ничего не говорит о разметке переписки, и
    запрещать вторую из-за первой значило бы терять работоспособное.

    Returns:
        None
    """
    broken_orders = _page("orders-trade.logged.ru").replace(
        '<div class="tc-status text-primary">', '<div class="tc-gone">'
    )
    with _client([_observation(broken_orders), _observation(_page("chat.logged.ru"))]) as client:
        client.orders.list()
        assert client.capability(Capability.ORDERS_LIST) is CapabilityState.DEGRADED
        assert client.capability(Capability.CHATS_LIST) is not CapabilityState.DEGRADED

        client.chats.list()
        assert client.capability(Capability.CHATS_LIST) is CapabilityState.SUPPORTED
        assert client.capability(Capability.ORDERS_LIST) is CapabilityState.DEGRADED
