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
    evaluation = profile.evaluation_of(Capability.CHATS_SEND_TEXT)
    print(evaluation.evaluated_at, evaluation.source)
```

Состояний пять: `SUPPORTED`, `UNSUPPORTED`, `EXPERIMENTAL`, `DEGRADED`,
`UNKNOWN`. `observed_at` показывает время сборки снимка, а `evaluated_at` -
время оценки конкретной возможности. Повторное чтение профиля не обновляет
оценки и не обращается к сети.

`source` различает начальную таблицу (`static`), результат обычной операции
(`probe`) и сброс после отказа (`observed_failure`). Это основание оценки,
а не способ определения функции из реестра спецификации.

Ошибка авторизации или `ProtocolChangedError` сбрасывает наблюдения своей
полосы в `UNKNOWN`, сохраняя ограничения из начального контракта. Сброс
консервативный: касается всей полосы, поскольку общий шаблон мог измениться
сразу на нескольких страницах. Публичный рынок и личная сессия независимы.
Следующая успешная операция обновляет свою оценку; `resume()` возвращает
обе полосы к начальной таблице. Старые объекты профиля остаются снимками.

## Отзывы

`reviews.get(user_id, cursor=None)` читает одну страницу. Пример ограничивает
число запросов; замените идентификатор на нужного продавца.

```python
from funora import Completeness

with Client(EnvSecretProvider()) as client:
    cursor = None
    for _ in range(10):
        page = client.reviews.get("987654", cursor=cursor)
        for review in page.rows(accept_incomplete=True):
            print(review.rating, review.author_name.or_none(), review.text.or_none())
        if page.defects:
            print("Чтение повреждено:", page.defects)
            break
        cursor = page.next_cursor
        if cursor is None:
            print("Конец подтверждён:", page.completeness is Completeness.COMPLETE)
            break
    else:
        print("Достигнут предел страниц; продолжение:", cursor)
```

Курсор берётся из формы ответа и привязан к продавцу. Повторённый сервером
курсор вызывает `ProtocolChangedError`. `next_cursor=None` при неполном
результате не доказывает конец выдачи. Страницы не объединяются автоматически;
фильтр по оценке пока не поддерживается. У `AsyncClient` тот же вызов с `await`.

Курсор отзывов также можно сохранить строкой:

```python
saved = page.next_cursor.to_token() if page.next_cursor is not None else None
# После перезапуска передайте saved в client.reviews.get(user_id, cursor=saved).
```

`ReviewsCursor.from_token(saved)` восстанавливает объект. Сырой указатель
сервера сохраняется без изменения Unicode; несовместимая версия, другая
операция или продавец дают `CursorIncompatibleError`. Сохраняйте позицию после
обработки страницы; сам токен не подтверждает, что данные обработаны.

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

## Способы вывода средств

```python
page = client.account.balance()

for option in page.withdrawal_options:
    print(option.key, option.name.or_none(), option.saved_wallets)

for channel in page.withdrawal_channels:
    print(channel.currency, "->", channel.option_key, "|", channel.fee_text.or_none())
```

Отдельного запроса за ними нет: они лежат на той же странице баланса. Ходить на
площадку дважды за одним ответом мы не станем - тот же довод, по которому здесь
же приходят операции по счёту.

!!! success "Здесь наше наблюдение точнее чужого"

    Независимая реализация того же протокола держит перечень способов вывода
    **рукописным списком из восьми**. У площадки их **тринадцать**, и два из
    восьми неверны:

    - значения `binance` в перечне площадки нет вовсе - там `binance_usdc` и
      `binance_usdt`, и отправка `binance` была бы молча отброшена;
    - `fps` и `yandex` - **разные** способы, слитые у неё в один.

    Поэтому наш перечень **читается**, а не зашивается: рукописный список
    устаревает молча, и наш устарел бы так же.

### Комиссия - текстом

Слова «комиссия» в проекте до 31.08.2026 не было нигде. Названа она теперь, и
названа текстом: это строка на локали интерфейса, а строить расчёт **денег** на
переводе нельзя.

### Что со снимка не читается

Формат скелета сохраняет дословно **ключи** объектов, а не значения. Перечень
способов лежит ключами и уцелел целиком; ссылка канала на способ - значением, и
замаскирована.

То есть со снимка известно, **какие** способы есть, и неизвестно, **какой валютой
какой** из них доступен. Второе читается только с живой страницы.
