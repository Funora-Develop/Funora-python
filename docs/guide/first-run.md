# Первый запуск

## Самое короткое, что работает

```python
from funora import Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    page = client.orders.list()
    for order in page.rows():
        print(order.order_id, order.description_text)
```

Три вещи в этих пяти строках стоит разобрать.

## Клиент - контекстный менеджер

`with` не украшение: на выходе закрывается пул соединений. Без него соединения
останутся висеть до сборки мусора, а на длинной работе - до конца процесса.

Можно и вручную:

```python
from funora import Client, EnvSecretProvider

client = Client(EnvSecretProvider())
try:
    page = client.orders.list()
finally:
    client.close()
```

## Операции разложены по сервисам

Клиент сам ничего не читает. Читают сервисы, и их имена совпадают с разделами
площадки.

| Сервис | Что за ним |
|---|---|
| `client.orders` | продажи |
| `client.chats` | диалоги и переписка |
| `client.lots` | свои лоты и витрина продавца |
| `client.reviews` | отзывы |
| `client.account` | аккаунт, баланс, здоровье сессии |
| `client.catalog` | разделы площадки |

Полный перечень операций - в главах раздела «Операции». Каждая из них
проверяется прогоном: метод, упомянутый в этом руководстве и исчезнувший из
кода, роняет сборку.

## Результат - страница, а не список

`client.orders.list()` возвращает не `list`, а `OrdersPage`, и записи достают
методом `rows()`. Это не многословность ради многословности - объяснение в
следующей главе, [Страницы](pages.md), и оно того стоит.

## Что ещё умеет сам клиент

### Язык площадки

```python
from funora import Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    client.orders.list()
    print(client.locale.value)  # (1)!
```

1. Язык становится известен **после** первого ответа, а не при создании
   клиента: до первого запроса читать нечего. До него `client.locale` -
   ненаблюдённое значение, и `.value` бросит исключение. Про это - в главе
   [Наблюдённые значения](observed.md).

### Возможности

Не всё, что объявлено контрактом, реализовано этим SDK - и не всё, что
реализовано, доступно вашему аккаунту. Спросить можно заранее:

```python
from funora import Capability, CapabilityState, Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    if client.capability(Capability.CHATS_SEND_TEXT) is CapabilityState.SUPPORTED:
        ...
```

### Настройки транспорта

```python
from funora import Client, EnvSecretProvider, TransportSettings

settings = TransportSettings(
    connect_timeout_s=5.0,
    read_timeout_s=15.0,
    max_connections=2,
)

with Client(EnvSecretProvider(), settings=settings) as client:
    ...
```

Умолчания выбраны в сторону осторожности: четыре соединения, ограничение на
размер ответа и на размер после распаковки. Последнее - защита от ответа,
который распаковывается в гигабайты.

## Куда дальше

Прежде чем писать что-то полезное, прочитайте две следующие главы. Они про то,
как устроен **любой** ответ этого SDK, и без них остальное будет выглядеть
странно многословным.
