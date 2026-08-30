# Аккаунт, баланс, разделы

## Кто вы

```python
from funora import Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    me = client.account.get()
    print(me.user_id.or_none(), me.username.or_none(), me.locale.or_none())
```

`account.get()` кэшируется на время жизни клиента: личность за один сеанс не
меняется. Перечитать принудительно - `account.refresh()`.

## Здоровье сессии

```python
with Client(EnvSecretProvider()) as client:
    health = client.account.health()

    if not health.is_usable:
        print("сессия непригодна:", health.reason)
```

| Поле | Что это |
|---|---|
| `response_class` | вердикт классификатора: что за страницу отдала площадка |
| `is_usable` | можно ли работать |
| `reason` | машиночитаемая причина |
| `provisional` | вердикт предварительный, а не окончательный |
| `from_cache` | ответ взят из кэша, а не свежий |

Это дешёвый способ узнать, что ключ протух или площадка показывает проверку, - не
дожидаясь исключения посреди работы.

## Баланс и операции

```python
with Client(EnvSecretProvider()) as client:
    page = client.account.balance()

    for balance in page.balances:
        print(balance)

    for row in page.transactions():
        print(row)
```

!!! warning "Суммы здесь тоже текстовые"

    Читать их числом SDK не берётся: валюта и разделители на странице
    показываются так, как площадка решит показывать. См. [Что Funora сегодня не
    может](../limits.md).

## Возможности

```python
from funora import Capability, CapabilityState

with Client(EnvSecretProvider()) as client:
    profile = client.account.capabilities()

    print(profile.state_of(Capability.CHATS_SEND_TEXT))
```

Состояний пять: `SUPPORTED`, `UNSUPPORTED`, `EXPERIMENTAL`, `DEGRADED`,
`UNKNOWN`. Профиль - снимок на момент `observed_at`, а не вечная истина.

## Отзывы

```python
with Client(EnvSecretProvider()) as client:
    page = client.reviews.get("987654")

    for review in page.rows():
        print(review.rating, review.author_name.or_none(), review.text.or_none())
```

## Разделы площадки

```python
with Client(EnvSecretProvider()) as client:
    page = client.catalog.categories()

    for game in page.games():
        print(game)
```

Это то самое дерево, из которого берутся идентификаторы разделов для
[лотов](lots.md).
