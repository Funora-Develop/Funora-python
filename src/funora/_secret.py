"""Работа с сессионными секретами.

Модуль намеренно написан раньше транспорта. Сессионный ключ FunPay - это не токен
с ограниченными правами, а доступ ко всему аккаунту: кто им владеет, тот читает
переписку, видит заказы и действует от имени продавца. Если сначала появится
транспорт, ключ успеет протечь в отладочный вывод HTTP-клиента раньше, чем
появится тип, который его защищает.

Что этот модуль гарантирует:
  * значение не попадает в repr, str, format и текст исключений;
  * значение не сериализуется ни json, ни pickle, ни copy;
  * получить значение можно только явным вызовом reveal().

Чего он не гарантирует и не может: сторонний HTTP-клиент с включённым отладочным
логированием, APM-агент и плагин внутри процесса прочитают ключ в момент отправки
запроса. Граница защиты проходит по краю этого проекта, и делать вид, что она
шире, было бы обманом.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "Secret",
    "SecretProvider",
    "EnvSecretProvider",
    "CallableSecretProvider",
    "FileSecretProvider",
    "SecretNotFoundError",
]

#: Текст, который выводится вместо значения во всех строковых представлениях.
_MASK: Final[str] = "Secret(<redacted>)"


class SecretNotFoundError(RuntimeError):
    """Источник не смог предоставить секрет.

    Отдельный тип нужен, чтобы отличать отсутствие ключа в конфигурации от
    отказа площадки принять существующий ключ: пользователю в этих случаях надо
    делать разное.
    """


class Secret:
    """Обёртка над сессионным секретом, не раскрывающая значение при выводе.

    Все строковые представления возвращают маску. Значение доступно только через
    reveal(), и такой вызов видно при чтении кода - в отличие от неявной
    подстановки в f-строку.

    Args:
        value (str): Значение секрета. Пустая строка недопустима.
        label (str): Короткая метка для диагностики, например ``golden_key``.
            Попадает в вывод repr вместо значения.

    Raises:
        ValueError: Если значение пустое или состоит из пробельных символов.
    """

    __slots__ = ("_value", "_label")

    def __init__(self, value: str, label: str = "secret") -> None:
        if not value or not value.strip():
            raise ValueError("секрет не может быть пустым")
        self._value = value
        self._label = label

    def reveal(self) -> str:
        """Возвращает значение секрета.

        Единственный способ получить значение. Вызов намеренно назван так, чтобы
        он бросался в глаза при чтении кода и при поиске по репозиторию.

        Returns:
            str: Значение секрета.
        """
        return self._value

    @property
    def label(self) -> str:
        """Метка секрета для диагностики.

        Returns:
            str: Метка, переданная при создании. Значения не содержит.
        """
        return self._label

    def __repr__(self) -> str:
        """Возвращает безопасное представление для отладки.

        Returns:
            str: Маска с меткой, без значения.
        """
        return f"Secret({self._label}=<redacted>)"

    def __str__(self) -> str:
        """Возвращает безопасное строковое представление.

        Returns:
            str: Маска без значения.
        """
        return _MASK

    def __format__(self, spec: str) -> str:
        """Возвращает маску при подстановке в f-строку.

        Перекрыт намеренно: без него ``f"{secret}"`` вызвал бы format у str и
        напечатал значение, обойдя __str__.

        Args:
            spec (str): Спецификация формата. Игнорируется.

        Returns:
            str: Маска без значения.
        """
        return _MASK

    def __eq__(self, other: object) -> bool:
        """Сравнивает два секрета по значению.

        Args:
            other (object): Объект для сравнения.

        Returns:
            bool: True, если другой объект - Secret с тем же значением.
        """
        if not isinstance(other, Secret):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        """Возвращает хэш секрета.

        Хэшируется значение, чтобы Secret можно было класть в множества и
        использовать как ключ словаря.

        Returns:
            int: Хэш значения.
        """
        return hash(self._value)

    def __reduce__(self) -> Any:
        """Запрещает сериализацию через pickle.

        Raises:
            TypeError: Всегда. Сериализованный секрет пережил бы процесс и
                оказался бы на диске или в очереди задач.
        """
        raise TypeError("Secret не сериализуется: значение оказалось бы вне процесса")

    def __copy__(self) -> Any:
        """Запрещает копирование.

        Raises:
            TypeError: Всегда. Копия обходит контроль за числом мест,
                где живёт значение.
        """
        raise TypeError("Secret не копируется")

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        """Запрещает глубокое копирование.

        Args:
            memo (dict[int, Any]): Служебный словарь copy.deepcopy. Игнорируется.

        Raises:
            TypeError: Всегда.
        """
        raise TypeError("Secret не копируется")


@runtime_checkable
class SecretProvider(Protocol):
    """Источник секретов.

    Протокол намеренно узкий: одна операция. Пользователь может подключить
    менеджер секретов, файл с ограниченными правами или собственный код, не
    завися от того, как устроен клиент.
    """

    def get(self, name: str) -> Secret:
        """Возвращает секрет по имени.

        Args:
            name (str): Логическое имя секрета, например ``golden_key``.

        Returns:
            Secret: Найденный секрет.

        Raises:
            SecretNotFoundError: Если секрет недоступен.
        """
        ...


class EnvSecretProvider:
    """Источник, читающий секреты из переменных окружения.

    Простейший рабочий вариант. Подходит для разработки и для контейнеров, но не
    защищает от чтения другими процессами того же пользователя.

    Args:
        prefix (str): Префикс переменной. Имя ``golden_key`` при префиксе
            ``FUNORA_`` читается из ``FUNORA_GOLDEN_KEY``.
    """

    __slots__ = ("_prefix",)

    def __init__(self, prefix: str = "FUNORA_") -> None:
        self._prefix = prefix

    def get(self, name: str) -> Secret:
        """Возвращает секрет из переменной окружения.

        Args:
            name (str): Логическое имя секрета.

        Returns:
            Secret: Значение переменной, обёрнутое в Secret.

        Raises:
            SecretNotFoundError: Если переменная не задана или пуста.
        """
        var = f"{self._prefix}{name.upper()}"
        value = os.environ.get(var)
        if not value:
            raise SecretNotFoundError(f"переменная окружения {var} не задана")
        return Secret(value, label=name)


class CallableSecretProvider:
    """Источник, вызывающий переданную функцию.

    Нужен, чтобы подключить менеджер секретов, не описывая для него отдельный
    класс. Функция вызывается при каждом обращении, поэтому ротация ключа
    подхватывается без перезапуска процесса.

    Args:
        fn (Callable[[str], str]): Функция, возвращающая значение по имени.
            Должна поднимать исключение, если секрет недоступен.
    """

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[str], str]) -> None:
        self._fn = fn

    def get(self, name: str) -> Secret:
        """Возвращает секрет, полученный от функции.

        Args:
            name (str): Логическое имя секрета.

        Returns:
            Secret: Значение, обёрнутое в Secret.

        Raises:
            SecretNotFoundError: Если функция вернула пустое значение или упала.
        """
        try:
            value = self._fn(name)
        except Exception as exc:
            raise SecretNotFoundError(f"источник не смог выдать секрет {name}") from exc
        if not value:
            raise SecretNotFoundError(f"источник вернул пустой секрет {name}")
        return Secret(value, label=name)


class FileSecretProvider:
    """Источник, читающий секрет из файла.

    Файл читается при каждом обращении: это позволяет сменить ключ, не
    перезапуская процесс. Права доступа проверяются на POSIX-системах; на Windows
    проверка пропускается, потому что режим файла там не отражает реальную
    модель доступа.

    Args:
        directory (Path): Каталог, в котором лежат файлы секретов. Имя файла
            совпадает с именем секрета.
        check_permissions (bool): Проверять ли, что файл недоступен для чтения
            другими пользователями.
    """

    __slots__ = ("_dir", "_check")

    def __init__(self, directory: Path, check_permissions: bool = True) -> None:
        self._dir = directory
        self._check = check_permissions

    def get(self, name: str) -> Secret:
        """Возвращает секрет, прочитанный из файла.

        Args:
            name (str): Логическое имя секрета, оно же имя файла.

        Returns:
            Secret: Содержимое файла без завершающих пробельных символов.

        Raises:
            SecretNotFoundError: Если файл отсутствует, пуст или доступен на
                чтение посторонним.
        """
        path = self._dir / name
        if not path.is_file():
            raise SecretNotFoundError(f"файл секрета не найден: {path}")

        if self._check and os.name == "posix":
            mode = path.stat().st_mode & 0o077
            if mode:
                raise SecretNotFoundError(f"файл {path} доступен посторонним; ожидаются права 0600")

        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise SecretNotFoundError(f"файл секрета пуст: {path}")
        return Secret(value, label=name)
