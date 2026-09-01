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

## Смена валюты показа

```python
from funora import Capability, Client, EnvSecretProvider

with Client(
    EnvSecretProvider(),
    experimental={Capability.ACCOUNT_SWITCH_CURRENCY},
) as client:
    result = client.account.switch_currency("USD")

    if result.confirmation_required:
        print("площадка просит подтверждения:", result.confirmation_text.or_none())
    elif result.switched:
        print("суммы теперь в долларах")
```

!!! danger "Побочное действие глобально"

    После смены **каждая** страница отдаёт другие числа, и всякое последующее
    чтение вернёт не то, что вернуло бы прежде.

    Дороже всего это для снимков рынка: сравнение двух снимков, снятых по разные
    стороны от смены, объявит сменившейся **каждую** цену - без единой ошибки и
    без следа.

### Подтверждать за вас реализация не станет

В запросе есть поле подтверждения, и туда **всегда** уходит отрицание.

Площадка отвечает двумя ветками: либо валюта сменена сразу, либо возвращается
окно подтверждения - и тогда **смены не было**. Вторая ветка отдаётся исходом:
решать, соглашаться ли, вправе только человек.

### Курс из окна не разбирается

Внутри окна лежит курс обмена. Независимая реализация того же протокола достаёт
его регулярным выражением из абзаца - то есть **из текста на локали интерфейса**.

Локаль привязана к аккаунту, а не к адресу, и смена языка ломает такой разбор
молча. Мы отдаём текст как есть: прочитать его глазами вы можете, вывести из него
число реализация не станет.

### Суммы заказов за настройкой не следуют

Наблюдено прямо: два сбора списка продаж при разной текущей валюте дали один и
тот же набор знаков. Сумма заказа показана в валюте **своей сделки**, и смена
показа её не трогает.
