"""Перечень прокси и выбор того, через который сейчас идти.

Прокси здесь - не способ ходить чаще, а способ ходить ОТКУДА-ТО. Спецификация
привязывает бюджет к сетевой идентичности, то есть к паре «исходящий адрес,
целевой хост», и прокси меняет первую половину пары: у каждого свой запас
токенов, своё остывание и свой счёт ограничений.

Из этого следует всё остальное.

Переключение не отменяет отступления. Прокси, получивший ограничение частоты,
остывает по объявленному правилу независимо от того, ушла работа на другой или
нет: вернуться к нему раньше срока нельзя. Иначе переключение означало бы
«продолжать в прежнем темпе с другого адреса», а не «дать этому адресу
отдохнуть».

Аккаунт привязан к прокси. Аккаунт, ходивший с одного адреса и вдруг сменивший
его, выглядит иначе, чем аккаунт, у которого адрес постоянен: привязка держит
пару стабильной, пока прокси жив. Смена происходит только когда прежний не
может работать - остывает после ограничения либо не отвечает.

Порядок перечня уважается. Вызывающий назвал прокси в том порядке, в каком хочет
ими пользоваться; перебирать по кругу значило бы размазывать один аккаунт по
всем адресам, а это ровно то, чего привязка избегает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Final

from ._identity import REGISTRY, IdentityRegistry, identity_of
from .errors import ConfigurationError

__all__ = ["Proxy", "ProxyPool", "DEFAULT_ACCOUNT"]

#: Имя аккаунта, под которым клиент выбирает идентичность до того, как
#: наблюдение назвало настоящее.
#:
#: Клиент заводится раньше, чем становится известен аккаунт: секрет у него
#: есть, а идентификатор аккаунта читается со страницы. Привязка при первом
#: же наблюдении перезаписывается настоящим именем.
DEFAULT_ACCOUNT: Final[str] = "self"

_log = logging.getLogger("funora.proxies")

#: Схемы, через которые ходить нельзя.
#:
#: Секрет уходит в заголовке каждого запроса, и прокси видит весь трафик. Схема
#: без шифрования до прокси означает, что ключ читает любой на пути до него -
#: то есть прокси, поставленный ради приватности, её и отменяет.
_INSECURE_SCHEMES: Final[frozenset[str]] = frozenset({"http"})


@dataclass(frozen=True, slots=True)
class Proxy:
    """Один прокси из перечня.

    Attributes:
        name (str): Имя для журналов и для имени идентичности. Адрес сюда не
            попадает намеренно: он может нести пароль, а имя уходит в журналы.
        url (str): Адрес прокси в форме, которую понимает транспорт.
    """

    name: str
    url: str


class ProxyPool:
    """Перечень прокси и выбор пригодного.

    Args:
        proxies (tuple[Proxy, ...]): Прокси в порядке предпочтения. Пустой
            перечень означает прямое соединение.
        host (str): Целевой хост. Входит в имя идентичности: один и тот же
            прокси к разным хостам - разные идентичности, потому что
            ограничения применяет хост.
        registry (IdentityRegistry): Реестр идентичностей. По умолчанию общий
            на процесс.

    Raises:
        ConfigurationError: Если два прокси названы одинаково либо адрес
            небезопасен.
    """

    __slots__ = ("_by_account", "_host", "_proxies", "_registry")

    def __init__(
        self,
        proxies: tuple[Proxy, ...] = (),
        *,
        host: str,
        registry: IdentityRegistry | None = None,
    ) -> None:
        names = [proxy.name for proxy in proxies]
        if len(set(names)) != len(names):
            raise ConfigurationError(
                "имена прокси повторяются: имя входит в имя идентичности, и "
                "одинаковые имена свели бы два разных адреса в одно ведро токенов"
            )
        for proxy in proxies:
            scheme = proxy.url.split("://", 1)[0].lower()
            if scheme in _INSECURE_SCHEMES:
                raise ConfigurationError(
                    f"прокси {proxy.name} объявлен по схеме {scheme}: секрет "
                    "уходит в заголовке каждого запроса, и без шифрования до "
                    "прокси его читает любой на пути. Возьмите https или socks5"
                )

        self._proxies = proxies
        self._host = host
        self._registry = registry if registry is not None else REGISTRY
        self._by_account: dict[str, str] = {}

    @property
    def proxies(self) -> tuple[Proxy, ...]:
        """Перечень прокси в порядке предпочтения.

        Returns:
            tuple[Proxy, ...]: Объявленные прокси.
        """
        return self._proxies

    def _identity_names(self) -> tuple[str, ...]:
        """Собирает имена идентичностей в порядке предпочтения.

        Returns:
            tuple[str, ...]: Имена. При пустом перечне - одно прямое соединение.
        """
        if not self._proxies:
            return (identity_of(None, self._host),)
        return tuple(identity_of(proxy.name, self._host) for proxy in self._proxies)

    def _url_by_identity(self, name: str) -> str | None:
        """Находит адрес прокси по имени идентичности.

        Args:
            name (str): Имя идентичности.

        Returns:
            str | None: Адрес либо None при прямом соединении.
        """
        for proxy in self._proxies:
            if identity_of(proxy.name, self._host) == name:
                return proxy.url
        return None

    def choose(self, account_id: str, now: float | None = None) -> tuple[str, str | None]:
        """Выбирает идентичность для аккаунта.

        Привязка держится, пока прокси работает: аккаунт, ходивший с одного
        адреса и вдруг сменивший его, выглядит иначе, чем аккаунт с постоянным
        адресом. Смена происходит только когда прежний остывает после
        ограничения либо был отставлен как неработающий.

        Args:
            account_id (str): Аккаунт, для которого идёт запрос.
            now (float | None): Момент. По умолчанию текущий.

        Returns:
            tuple[str, str | None]: Имя идентичности и адрес прокси. Адрес None
            означает прямое соединение.

        Raises:
            ConfigurationError: Если остывают все объявленные прокси. Отказ
                вслух честнее молчаливого возврата к прямому соединению: прямое
                соединение раскрывает адрес, который вызывающий намеренно
                прятал.
        """
        moment = monotonic() if now is None else now
        names = self._identity_names()

        bound = self._by_account.get(account_id)
        if bound in names and not self._registry.get(bound).is_cooling(moment):
            return bound, self._url_by_identity(bound)

        chosen = self._registry.healthy(names, moment)
        if chosen is None:
            cooling = ", ".join(
                f"{name} ещё {self._registry.get(name).cooldown_until - moment:.0f} с"
                for name in names
            )
            raise ConfigurationError(
                "все объявленные прокси остывают после ограничения частоты: "
                f"{cooling}. Возврат к прямому соединению не делается: он "
                "раскрыл бы адрес, который вы намеренно прятали"
            )

        if bound is not None and bound != chosen:
            _log.warning(
                "аккаунт %s переведён с %s на %s: прежний остывает после ограничения частоты",
                account_id,
                bound,
                chosen,
            )
        self._by_account[account_id] = chosen
        return chosen, self._url_by_identity(chosen)

    def note_limit(self, name: str, now: float | None = None) -> None:
        """Учитывает ограничение частоты, полученное этой идентичностью.

        Args:
            name (str): Имя идентичности.
            now (float | None): Момент. По умолчанию текущий.

        Returns:
            None
        """
        self._registry.get(name).note_limit(monotonic() if now is None else now)

    def note_success(self, name: str) -> None:
        """Учитывает успешный запрос этой идентичности.

        Args:
            name (str): Имя идентичности.

        Returns:
            None
        """
        self._registry.get(name).note_success()

    def bound_to(self, account_id: str) -> str | None:
        """Сообщает, к какой идентичности привязан аккаунт.

        Args:
            account_id (str): Аккаунт.

        Returns:
            str | None: Имя идентичности либо None, если привязки ещё нет.
        """
        return self._by_account.get(account_id)
