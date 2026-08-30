# Ошибки

Все отказы Funora - потомки `FunoraError`. Это обещание: общий перехват поймает
всё, что бросает SDK, и остановится операция, а не ваш процесс.

```python
from funora import Client, EnvSecretProvider, FunoraError

with Client(EnvSecretProvider()) as client:
    try:
        page = client.orders.list()
    except FunoraError as error:
        log.error("не прочиталось: %s", error)
```

## Что у ошибки можно спросить

```python
try:
    ...
except FunoraError as error:
    error.stable_id             # устойчивое имя, годное для if и для журнала
    error.retryable             # имеет ли смысл повторить
    error.side_effects_possible # могло ли действие всё-таки произойти
    error.user_actionable       # может ли человек что-то с этим сделать
```

`side_effects_possible` - самое важное из четырёх для операций записи. Оно
отвечает на вопрос «повторять или нет» честнее любого таймаута.

## Семейства

### Вход и доступ

| Ошибка | Когда |
|---|---|
| `AuthenticationError` | сессия не годится |
| `SessionExpiredError` | ключ протух |
| `InvalidCredentialsError` | ключ неверен |
| `ChallengeRequiredError` | площадка показывает проверку |
| `AccessBlockedError` | доступ закрыт |

### Транспорт

| Ошибка | Когда |
|---|---|
| `NetworkError` | не дошли |
| `TimeoutError` | не дождались |
| `RateLimitedError` | площадка ограничила темп |
| `RemoteServerError` | площадка ответила ошибкой |

### Разбор

| Ошибка | Когда |
|---|---|
| `ProtocolChangedError` | страница изменилась настолько, что разбирать нечего |
| `ParseError` | не разобралось |
| `UnexpectedResponseError` | пришло не то, чего ждали |
| `IncompleteResultError` | результат неполон, а согласия на неполноту не было |
| `UnobservedFieldError` | попытка прочитать `.value` у ненаблюдённого поля |

### Ваш вызов

| Ошибка | Когда |
|---|---|
| `UsageError` | вызов неверен |
| `ValidationError` | аргумент не годится |
| `ConfigurationError` | настроено неверно |
| `CapabilityError` | возможность недоступна |
| `NotImplementedOperationError` | операция объявлена контрактом и не написана |
| `BudgetExhaustedError` | бюджет исчерпан |

!!! note "`NotImplementedOperationError` - это про SDK, а не про площадку"

    Площадка функцию имеет, руки не дошли. Отличать это от `UnsupportedCapabilityError`
    («площадка такого не умеет») важно: во втором случае ждать нечего.

## Разбирать по типу, а не по тексту

```python
from funora.errors import RateLimitedError, SessionExpiredError

try:
    client.chats.send_text(node_id, text)
except SessionExpiredError:
    refresh_key()
except RateLimitedError as error:
    sleep_until(error)
```

Текст ошибки написан для человека и меняется. `stable_id` и класс - не меняются.

## Чего исключение НЕ означает при отправке

Ровно наоборот тому, к чему привыкли: **исключение означает, что сообщение не
ушло**. Если исход неизвестен, вы получите не исключение, а значение с исходом
`UNCONFIRMED`. Подробно - в главе [Отправка](sending.md).
