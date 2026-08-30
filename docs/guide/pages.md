# Страницы, а не списки

Каждая читающая операция возвращает **страницу**: объект, который знает не
только записи, но и то, насколько он им доверяет.

```python
from funora import Client, EnvSecretProvider

with Client(EnvSecretProvider()) as client:
    page = client.orders.list()

    print(page.completeness)   # полнота
    print(page.rows_total)     # сколько строк нашлось на странице
    print(page.rows_accepted)  # сколько удалось собрать
    print(page.rows_rejected)  # сколько отброшено
    print(page.defects)        # что именно не собралось

    for order in page.rows():
        ...
```

## Зачем так

Представьте обычный вариант: метод возвращает `list`, и в списке двадцать
заказов из тридцати - у десяти разметка изменилась, и разбор их пропустил.

Вызывающий видит двадцать заказов. Проверить ему нечем: список как список.
Обработчик «выдать товар по всем оплаченным» отработает по двадцати, и десять
покупателей будут ждать, пока кто-нибудь не пожалуется.

**Неполнота, отданная молча, неотличима от полноты.** Единственный способ это
исправить - сделать её видимой в типе результата.

## Три состояния полноты

```python
from funora import Completeness

Completeness.COMPLETE  # собрано всё, что было на странице
Completeness.PARTIAL   # часть строк не собралась, и их видно в page.defects
Completeness.UNKNOWN   # неизвестно даже это
```

## Что делает `rows()`

```python
page.rows()                          # при неполноте бросит IncompleteResultError
page.rows(accept_incomplete=True)    # отдаст то, что собралось
```

По умолчанию неполная страница **не отдаёт записи молча**. Чтобы их получить,
нужно сказать это вслух - и тем самым в коде появится место, которое видно при
ревью.

```python
from funora import Client, Completeness, EnvSecretProvider
from funora.errors import IncompleteResultError

with Client(EnvSecretProvider()) as client:
    page = client.orders.list()

    try:
        orders = page.rows()
    except IncompleteResultError:
        # Решение принимает вызывающий, а не SDK: одному важнее свежесть,
        # другому - полнота.
        if page.completeness is Completeness.PARTIAL:
            orders = page.rows(accept_incomplete=True)
        else:
            raise
```

## Дефекты

`page.defects` - перечень того, что именно не собралось. У каждого есть код,
тяжесть и место.

```python
from funora import Severity

for defect in page.defects:
    print(defect.code, defect.severity, defect.detail)

# Severity.FIELD - не собралось поле, строка в остальном цела
# Severity.ROW   - не собралась строка, остальные целы
# Severity.PAGE  - не собралась страница целиком
```

Тяжесть - не украшение. `ROW` означает «двадцать записей из тридцати верны»,
`PAGE` - «доверять нельзя ничему».

Рядом с полнотой лежит `page.reason` - почему полнота именно такая. При
`COMPLETE` там пусто, при остальных - короткая причина.

!!! tip "Дефекты стоит логировать, даже когда вы принимаете неполноту"

    Код дефекта - первое, что понадобится, когда площадка сменит разметку.
    Он же - готовое содержание issue.

## Момент наблюдения

У каждой страницы есть `observed_at` - момент, когда ответ был получен.

```python
page = client.orders.list()
print(page.observed_at)
```

Он нужен не для красоты: страница, прочитанная минуту назад, и страница,
прочитанная час назад, - разные основания для решения, а по самим записям
отличить их нельзя.
