<p align="center">
  <img src="https://raw.githubusercontent.com/Funora-Develop/.github/main/assets/funora-python.svg" width="76" height="76" alt="">
</p>

<h1 align="center">Funora для Python</h1>

<p align="center"><em>Эталонная реализация контракта Funora.</em></p>

<p align="center">
  <img alt="status" src="https://img.shields.io/badge/status-draft-6E7681?style=flat-square">
  <img alt="pypi" src="https://img.shields.io/badge/pypi-funora%20reserved-3B6FA0?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-2F7D95?style=flat-square">
  <img alt="FunPay" src="https://img.shields.io/badge/FunPay-unofficial-B4501E?style=flat-square">
</p>

<p align="center"><a href="README.en.md">English</a></p>

---

> **Неофициальный проект.** Funora не аффилирована с FunPay, не одобрена ею и никак с ней не связана.
> Работает с приватным веб-интерфейсом, который может измениться в любой момент без предупреждения.
> Использование может привести к блокировке аккаунта и заморозке средств - этот риск несёте вы.
> Прочитайте [DISCLAIMER.md](DISCLAIMER.md) прежде, чем строить на этом то, что приносит вам деньги.

## Статус: `draft`

Выпущенного пакета нет, устанавливать пока нечего. Работают три операции чтения,
контракт не стабилизирован и меняется.

## Что это

Python SDK для FunPay. Здесь протокол прорабатывается первым; остальные языки
реализуют его заново по спецификации, а не портируют этот код построчно.

```python
from funora import Client

with Client(secret) as client:
    page = client.orders.list()
    for order in page.rows():
        print(order.order_id, order.description_text)
```

## Что уже читается

| Операция | Возвращает |
|---|---|
| `client.orders.list()` | список заказов сокращёнными записями |
| `client.chats.list()` | список диалогов |
| `client.chats.thread(id)` | переписку с определением происхождения сообщений |

Операции записи не реализованы. Сейчас это SDK только для чтения.

## Чего SDK не умеет, и почему это написано здесь

Обычно такой раздел прячут. Здесь он на видном месте, потому что перечисленное
влияет на то, стоит ли вам вообще брать эту библиотеку сегодня.

**Не отвечает на вопрос «оплачен ли заказ».** Соответствия классов разметки
статусам мы не наблюдали, поэтому поле статуса выдаётся ненаблюдённым, а не
значением `unknown`. Второе означало бы «прочитали и не опознали», тогда как мы
не прочитали.

**Не даёт ни суммы числом, ни точного времени.** Машиночитаемого времени на
странице заказов нет вовсе, валюта не наблюдалась. Есть текст для показа.

**Не подтверждает оплату сообщением из переписки.** Даже верно опознанное
сообщение площадки не является подтверждением: оно могло относиться к другому
заказу, устареть, прийти по отменённому платежу. Площадка предупреждает об этом
сама, первым сообщением в каждом диалоге. Источник истины - только список продаж.

**Не дочитывает длинные списки.** Разметки постраничной навигации не
наблюдалось, и обещать курсор, которого адаптер выдать не может, опаснее, чем не
обещать.

## Как устроено

Три решения, которые видно в первом же вызове.

**Результат - страница, а не список.** Записи получают методом `rows()`, и при
неполноте нужен явный `accept_incomplete=True`. Молча отданный неполный список
неотличим от полного, и обработчик примет решение по данным, которых нет.

**Поля различают «пусто» и «не наблюдалось».** `None` одинаково выглядит для
обоих случаев, а решения по ним противоположные: пустое описание перечитывать
незачем, отсутствующее - повод заподозрить изменение вёрстки. Поэтому чтение
`.value` у ненаблюдённого поля бросает исключение, а не возвращает `None`.

**Механические части порождаются из спецификации.** Ошибки, возможности,
политики повторов, бюджет и таблица соответствия вердиктов ошибкам не пишутся
руками ни в одном из шести SDK. Сборка падает, если порождённое отстало от
источника.

Подробнее - в [docs/architecture.md](docs/architecture.md).

## Наблюдения за протоколом

Пакет содержит инструмент `funora-observe`, которым собраны все факты о
протоколе, на которых стоит спецификация. Он сохраняет структурный скелет
страницы: разметка целиком, текст и значения атрибутов заменены подписями.

- [docs/observations.md](docs/observations.md) - что установлено и как проверить.
- [docs/protocol-questions.md](docs/protocol-questions.md) - что осталось открытым.
- [tests/fixtures/pages/README.md](tests/fixtures/pages/README.md) - формат
  снимков и почему их можно публиковать.

## Проект целиком

Funora - это один контракт, реализованный нативно на нескольких языках. Меняется язык,
но не ментальная модель: `Client`, сервисы, события, роутер, фильтры, middleware и
таксономия ошибок означают одно и то же везде.

| Репозиторий | Что это | Статус |
|---|---|---|
| [Funora](https://github.com/Funora-Develop/Funora) | Один контракт, один набор тестовых векторов, нативный SDK на каждый язык. | `design` |
| [Funora-spec](https://github.com/Funora-Develop/Funora-spec) | Канонический контракт, который реализует каждый SDK. | `design` |
| [Funora-codegen](https://github.com/Funora-Develop/Funora-codegen) | Генерирует скучную повторяющуюся часть каждого SDK. | `design` |
| [Funora-conformance](https://github.com/Funora-Develop/Funora-conformance) | Тестовый контракт между языками. | `design` |
| [Funora-python](https://github.com/Funora-Develop/Funora-python) | Эталонная реализация контракта Funora. | `design` |
| [Funora-javascript](https://github.com/Funora-Develop/Funora-javascript) | Исходник на TypeScript, на выходе JavaScript и декларации типов. | `planned` |
| [Funora-java](https://github.com/Funora-Develop/Funora-java) | Java SDK. | `planned` |
| [Funora-dotnet](https://github.com/Funora-Develop/Funora-dotnet) | .NET SDK. | `planned` |
| [Funora-cpp](https://github.com/Funora-Develop/Funora-cpp) | C++ SDK. | `planned` |
| [Funora-c](https://github.com/Funora-Develop/Funora-c) | C SDK - самый узкий контракт в проекте. | `planned` |
| [Funora-docs](https://github.com/Funora-Develop/Funora-docs) | Документация всех SDK из одного источника. | `design` |
| [Funora-examples](https://github.com/Funora-Develop/Funora-examples) | Сквозные примеры, которые реально прогоняет CI. | `planned` |

## Участие в разработке

Сначала прочитайте [CONTRIBUTING.md](https://github.com/Funora-Develop/.github/blob/main/CONTRIBUTING.md).

Полезнее всего сейчас три вещи.

Снимки страниц в состояниях, которых у нас нет: заказы в разных статусах,
непрочитанный диалог, длинный список с постраничной навигацией. Каждый такой
снимок закрывает пункт в [docs/protocol-questions.md](docs/protocol-questions.md).

Разбор спецификации в [Funora-spec](https://github.com/Funora-Develop/Funora-spec):
она проверяется употреблением, и первая же попытка её применить дала восемнадцать
мест, где она противоречила сама себе.

Реализация операций чтения по уже написанным правилам извлечения.

## Безопасность

Никогда не вставляйте сессионный ключ, сырой HTML со страницы под авторизацией или содержимое
личной переписки в публичный issue. Сессионный ключ FunPay - это доступ ко всему аккаунту.
Сообщайте приватно через [Security Advisories](https://github.com/Funora-Develop/Funora/security/advisories/new),
подробности - в [SECURITY.md](https://github.com/Funora-Develop/.github/blob/main/SECURITY.md).

## Лицензия

[Apache-2.0](LICENSE) © Funora Contributors
