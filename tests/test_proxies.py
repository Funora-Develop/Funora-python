"""Проверки перечня прокси и реакции на ограничение частоты.

Набор стережёт то, что легче всего сделать неправильно. Прокси - не способ
ходить чаще, а способ ходить откуда-то: спецификация привязывает бюджет к
сетевой идентичности, то есть к паре «исходящий адрес, целевой хост», и прокси
меняет первую половину пары.

Отсюда главная проверка набора: переключение НЕ отменяет отступления. Прокси,
получивший ограничение частоты, остывает по объявленному правилу независимо от
того, ушла работа на другой или нет. Реализация, возвращающаяся к нему раньше
срока, продолжала бы в прежнем темпе с другого адреса - а это ровно то, за что
площадка и ограничивает.
"""

from __future__ import annotations

import pytest

from funora._identity import Identity, IdentityRegistry, identity_of
from funora._proxies import DEFAULT_ACCOUNT, Proxy, ProxyPool
from funora.budget import RATE_LIMIT_RESPONSE
from funora.errors import ConfigurationError

#: Хост площадки.
HOST = "funpay.com"

#: Момент отсчёта, монотонные секунды. Задан явно: со случайными часами
#: проверять поведение во времени нечем.
NOW = 1000.0


def _pool(*names: str) -> tuple[ProxyPool, IdentityRegistry]:
    """Собирает перечень прокси со своим реестром идентичностей.

    Реестр свой, а не общий на процесс: общий протекает между проверками.

    Args:
        *names (str): Имена прокси в порядке предпочтения.

    Returns:
        tuple[ProxyPool, IdentityRegistry]: Перечень и его реестр.
    """
    registry = IdentityRegistry()
    pool = ProxyPool(
        tuple(Proxy(name, f"https://{name}.example:8080") for name in names),
        host=HOST,
        registry=registry,
    )
    return pool, registry


def test_account_stays_on_its_proxy() -> None:
    """Проверяет, что аккаунт держится одного прокси.

    Аккаунт, ходивший с одного адреса и вдруг сменивший его, выглядит иначе, чем
    аккаунт с постоянным адресом. Перебирать прокси по кругу значило бы
    размазывать один аккаунт по всем адресам - то есть делать ровно то, чего
    привязка избегает.

    Returns:
        None
    """
    pool, _ = _pool("первый", "второй", "третий")

    chosen = [pool.choose("acc-1", NOW)[0] for _ in range(5)]
    assert len(set(chosen)) == 1, f"аккаунт перебрал прокси: {chosen}"
    assert chosen[0] == identity_of("первый", HOST), "взят не первый в перечне"


def test_different_accounts_may_share_a_proxy() -> None:
    """Проверяет, что порядок перечня уважается для всех аккаунтов.

    Вызывающий назвал прокси в том порядке, в каком хочет ими пользоваться.
    Раскидывать аккаунты по разным адресам без просьбы значило бы решать за
    него.

    Returns:
        None
    """
    pool, _ = _pool("первый", "второй")

    assert pool.choose("acc-1", NOW)[0] == pool.choose("acc-2", NOW)[0]


def test_rate_limit_moves_the_account_and_cools_the_proxy() -> None:
    """Проверяет главное: переключение не отменяет отступления.

    Прокси, получивший ограничение, остывает по объявленному правилу, и работа
    уходит на следующий. Вернуться к первому раньше срока нельзя: иначе
    переключение означало бы «продолжать в прежнем темпе с другого адреса».

    Returns:
        None
    """
    pool, registry = _pool("первый", "второй")

    first, _ = pool.choose("acc-1", NOW)
    pool.note_limit(first, NOW)

    second, url = pool.choose("acc-1", NOW)
    assert second != first, "аккаунт остался на остывающем прокси"
    assert url is not None

    identity = registry.get(first)
    assert identity.is_cooling(NOW), "прокси не остывает после ограничения"
    assert identity.capacity_factor == RATE_LIMIT_RESPONSE.capacity_multiplier, (
        "ёмкость не урезана: следующий залп будет ровно таким же, каким был до ограничения"
    )


def test_cooling_proxy_is_not_reused_before_its_time() -> None:
    """Проверяет, что к остывающему прокси не возвращаются раньше срока.

    Это вторая половина того же правила. Отступление без соблюдения срока -
    видимость отступления.

    Returns:
        None
    """
    pool, _ = _pool("первый", "второй")

    first, _ = pool.choose("acc-1", NOW)
    pool.note_limit(first, NOW)
    pool.choose("acc-1", NOW)

    # Внутри срока остывания первый не берётся даже для нового аккаунта.
    half = NOW + RATE_LIMIT_RESPONSE.cooldown_ms / 2000
    assert pool.choose("acc-2", half)[0] != first

    # После срока - берётся снова: отступление конечно.
    after = NOW + RATE_LIMIT_RESPONSE.cooldown_ms / 1000 + 1
    assert pool.choose("acc-3", after)[0] == first


def test_all_cooling_refuses_instead_of_going_direct() -> None:
    """Проверяет отказ, когда остывают все прокси.

    Молчаливый возврат к прямому соединению раскрыл бы адрес, который
    вызывающий намеренно прятал, - и сделал бы это ровно в тот момент, когда он
    меньше всего этого ждёт.

    Returns:
        None
    """
    pool, _ = _pool("первый", "второй")

    for name in (identity_of("первый", HOST), identity_of("второй", HOST)):
        pool.note_limit(name, NOW)

    with pytest.raises(ConfigurationError, match="остывают"):
        pool.choose("acc-1", NOW)


def test_direct_connection_is_the_default() -> None:
    """Проверяет, что без прокси клиент ходит напрямую.

    Прокси - настройка, а не обязанность.

    Returns:
        None
    """
    pool, _ = _pool()

    name, url = pool.choose(DEFAULT_ACCOUNT, NOW)
    assert url is None
    assert name == identity_of(None, HOST)


def test_insecure_proxy_scheme_is_refused() -> None:
    """Проверяет отказ на прокси без шифрования.

    Секрет уходит в заголовке каждого запроса, и прокси видит весь трафик. Схема
    без шифрования до прокси означает, что ключ читает любой на пути, - то есть
    прокси, поставленный ради приватности, её и отменяет.

    Returns:
        None
    """
    with pytest.raises(ConfigurationError, match="http"):
        ProxyPool((Proxy("плохой", "http://p.example:3128"),), host=HOST)


def test_duplicate_proxy_names_are_refused() -> None:
    """Проверяет отказ на одинаковых именах.

    Имя входит в имя идентичности, и одинаковые имена свели бы два разных адреса
    в одно ведро токенов: запросы через второй адрес тратили бы бюджет первого.

    Returns:
        None
    """
    with pytest.raises(ConfigurationError, match="повторяются"):
        ProxyPool(
            (Proxy("один", "https://a.example:8080"), Proxy("один", "https://b.example:8080")),
            host=HOST,
        )


def test_capacity_does_not_fall_below_the_floor() -> None:
    """Проверяет нижний предел урезания ёмкости.

    Дальше урезать бессмысленно: при таком темпе клиент уже почти не ходит, а
    ограничения продолжаются - значит дело не в темпе, и лечится оно остановкой,
    а не дальнейшим замедлением.

    Returns:
        None
    """
    identity = Identity(name="проба@funpay.com")
    for step in range(12):
        identity.note_limit(NOW + step)

    assert identity.capacity_factor == RATE_LIMIT_RESPONSE.min_capacity_factor


def test_recovery_is_slower_than_the_fall() -> None:
    """Проверяет несимметричность восстановления.

    Симметричное восстановление даёт автоколебания: система отступает, тут же
    возвращается к прежней частоте, получает ограничение снова и так по кругу.

    Returns:
        None
    """
    identity = Identity(name="проба@funpay.com")
    identity.note_limit(NOW)
    after_limit = identity.capacity_factor

    for _ in range(RATE_LIMIT_RESPONSE.successes_per_step):
        identity.note_success()
    after_recovery = identity.capacity_factor

    assert after_recovery > after_limit, "ёмкость не возвращается вовсе"
    assert after_recovery < 1.0, (
        "ёмкость вернулась целиком за одно окно успехов - это и есть симметричное "
        "восстановление, дающее автоколебания"
    )

    fall = 1.0 - after_limit
    rise = after_recovery - after_limit
    assert rise < fall, f"подъём {rise:.3f} не медленнее падения {fall:.3f}"


def test_a_limit_resets_the_recovery_count() -> None:
    """Проверяет, что ограничение обнуляет счёт успехов.

    Иначе накопленные до ограничения успехи вернули бы ёмкость сразу после него
    - то есть отступление отменялось бы собственной историей.

    Returns:
        None
    """
    identity = Identity(name="проба@funpay.com")
    identity.note_limit(NOW)

    for _ in range(RATE_LIMIT_RESPONSE.successes_per_step - 1):
        identity.note_success()

    identity.note_limit(NOW + 1)
    before = identity.capacity_factor
    identity.note_success()

    assert identity.capacity_factor == before, "один успех после ограничения вернул ёмкость"


def test_limits_outside_the_window_start_over() -> None:
    """Проверяет, что окно учёта ограничений конечно.

    Иначе счётчик копился бы вечно, и первое ограничение через сутки считалось
    бы третьим подряд - с остыванием втрое дольше положенного.

    Returns:
        None
    """
    identity = Identity(name="проба@funpay.com")
    identity.note_limit(NOW)
    assert identity.limits_seen == 1

    beyond = NOW + RATE_LIMIT_RESPONSE.window_ms / 1000 + 1
    identity.note_limit(beyond)
    assert identity.limits_seen == 1, "счёт ограничений не начался заново за пределами окна"


def test_scaled_bucket_does_not_refill_beyond_its_new_ceiling() -> None:
    """Проверяет, что урезание ёмкости вправду действует.

    Ведро, полное по прежней мерке, при уменьшенной ёмкости отдало бы залпом
    больше, чем новая ёмкость позволяет: урезание не подействовало бы до первого
    исчерпания.

    Returns:
        None
    """
    from funora._budget import TokenBucket
    from funora.budget import BUCKETS

    bucket = TokenBucket(BUCKETS["host"])
    bucket.scale(0.5)
    ceiling = BUCKETS["host"].capacity * 0.5

    assert bucket.tokens <= ceiling

    # Долгое пополнение не поднимает запас выше урезанного потолка.
    bucket.take(NOW, 1.0)
    bucket._refill(NOW + 10_000)
    assert bucket.tokens <= ceiling, "пополнение обошло урезанную ёмкость"
