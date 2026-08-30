# Наблюдённые значения

Второе решение, которое видно в каждом ответе: поля не отдаются голыми
значениями. Они обёрнуты в `Observed`.

```python
from funora import Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    for order in client.orders.list().rows():
        title = order.description_text  # это Observed[str], а не str
```

## Зачем

У поля, прочитанного с чужой страницы, три судьбы, а не две:

| Состояние | Что случилось | Что делать |
|---|---|---|
| `PRESENT` | поле есть, в нём значение | пользоваться |
| `EMPTY` | поле есть, и оно пусто | пользоваться, значение пустое |
| `NOT_OBSERVED` | поля не нашлось | **не** считать пустым |

`None` одинаково выглядит для второго и третьего. А решения по ним
противоположные: пустое описание перечитывать незачем, **отсутствующее** -
повод заподозрить смену разметки и не строить на нём логику.

## Как читать

```python
value = order.description_text

if value.is_observed:
    print(value.value)      # безопасно
```

`is_observed` истинно для **двух** состояний из трёх - и для `PRESENT`, и для
`EMPTY`: оба означают «поле прочитано». У пустого `.value` вернёт пустое
значение, а не бросит.

Есть и короткие формы:

```python
value.or_none()             # значение либо None
value.get("без описания")   # значение либо ваше умолчание
```

!!! danger "`.value` у ненаблюдённого поля бросает исключение"

    ```python
    value.value  # UnobservedFieldError, если поля не нашлось
    ```

    Это сделано нарочно. Верни оно `None` - и различие, ради которого весь тип
    заведён, исчезло бы в первой же строке пользовательского кода.

## Почему поля не нашлось

```python
value = order.description_text

if not value.is_observed:
    print(value.presence)  # Presence.NOT_OBSERVED
    print(value.reason)    # машиночитаемая причина
```

Причина - строка вроде `selector_missing`: короткая, стабильная и годная для
`if`. Человеческий текст меняется, и код на нём ломается молча.

## Уверенность

```python
from funora import Confidence

value.confidence is Confidence.OBSERVED  # правило выведено из снимка страницы
value.confidence is Confidence.INFERRED  # правило выведено рассуждением
```

`INFERRED` означает: разметку с таким полем никто не видел, правило написано по
соседним. Такое поле работает - и первым же перестанет работать при смене
вёрстки.

У ненаблюдённого поля уверенности нет вовсе (`None`): уверенность в отсутствии
не определена.

## Практика

Плохо:

```python
title = order.description_text.or_none() or "без описания"
```

Здесь пустое описание и отсутствующее слились в одно - ровно то, от чего тип
защищает.

Хорошо:

```python
from funora import Presence

value = order.description_text

if value.presence is Presence.PRESENT:
    title = value.value
elif value.presence is Presence.EMPTY:
    title = ""
else:
    # Поля не нашлось. Это не «пусто», это «разметка изменилась».
    log.warning("описание не прочитано: %s", value.reason)
    title = "без описания"
```

!!! note "Так писать нужно не везде"

    Там, где ошибка дёшева - показать текст в консоли, - `get()` с умолчанием
    честнее и короче. Различать состояния стоит там, где на поле принимается
    решение: выдать товар, ответить покупателю, изменить цену.
