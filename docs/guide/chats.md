# Диалоги и переписка

## Список диалогов

```python
from funora import Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    page = client.chats.list()

    for chat in page.rows():
        print(chat.node_id, chat.counterparty_name.or_none(), chat.unread)
```

| Поле | Что это |
|---|---|
| `node_id` | идентификатор диалога, им зовутся остальные операции |
| `counterparty_name` | имя собеседника |
| `preview_text` | последняя строка, как показана в списке |
| `unread` | диалог непрочитан |
| `time_text` | время последнего сообщения, текстом |
| `last_message_position`, `own_position` | служебные метки порядка |

!!! warning "`unread` - выведенный признак, а не наблюдённый"

    Он получен из расхождения двух позиций в строке диалога, а расхождения при
    непрочитанном диалоге не наблюдалось ни разу: снимка списка с
    непрочитанным диалогом у проекта нет.

    Поэтому у него уверенность `Confidence.INFERRED` - и это видно в самом
    значении:

    ```python
    from funora import Confidence

    if chat.unread.confidence is Confidence.INFERRED:
        ...  # правило выведено рассуждением, а не увидено
    ```

    Закрывается это одним снимком списка с непрочитанным диалогом - см.
    [открытые вопросы](../protocol-questions.md).

## Переписка

```python
from funora import Client, EnvSecretProvider, Origin

with Client(EnvSecretProvider()) as client:
    thread = client.chats.thread("123456")

    for message in thread.messages():
        who = "площадка" if message.origin is Origin.SYSTEM else "человек"
        print(who, message.author_name.or_none(), message.text.or_none())
```

Записи достаются методом `messages()`, а не `rows()` - переписка это не список
строк таблицы, - но правила те же: при неполноте нужен явный
`accept_incomplete=True`.

### Происхождение сообщения

```python
from funora import Origin

Origin.HUMAN    # написал человек
Origin.SYSTEM   # написала площадка
Origin.UNKNOWN  # не определено
```

Различать это важно: сообщение площадки об оплате выглядит как обычное
сообщение в диалоге, и обработчик, читающий подряд, примет его за слова
покупателя.

!!! danger "Сообщение площадки не подтверждает оплату"

    Даже верно опознанное системное сообщение подтверждением не является: оно
    могло относиться к другому заказу, устареть или прийти по платежу, который
    потом отменили.

    Площадка предупреждает об этом сама - первым сообщением в каждом диалоге.
    Источник истины про деньги один: [список продаж](orders.md).

### Ссылки наружу

```python
for message in thread.messages():
    if message.external_links:
        print("в сообщении есть ссылки на чужие хосты:", message.external_links)
```

Поле собирается разбором, а не доверием к тексту: ссылка на чужой хост в
сообщении от «поддержки» - самый обычный способ увести аккаунт.

## Что здесь ещё не работает

Отметка диалога прочитанным (`chats.mark_read`) и отправка изображения
(`chats.send_image`) объявлены контрактом и не написаны: запроса, которым
площадка это делает, никто не наблюдал.

Позвав их, вы получите `NotImplementedOperationError` - отказ **самой Funora**,
а не встроенную ошибку Python. Разница практическая: общий перехват
`FunoraError` его поймает, и остановится операция, а не весь ваш цикл.

Отправка текста - работает, и ей посвящена [отдельная глава](sending.md).
