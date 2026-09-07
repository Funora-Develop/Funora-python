# Каталог и поля раздела

`client.catalog.categories()` читает игры, варианты и разделы с корня площадки.
Записи доступны через `page.games()`. При неполном результате требуется
`accept_incomplete=True`.

Полный каталог хранится в памяти клиента сутки по политике контракта.
`client.catalog.categories(refresh=True)` принудительно читает его заново.
Повторный вызов из кэша сохраняет исходный `observed_at`; это время наблюдения,
а не обращения к методу. Параллельные обычные вызовы одного клиента используют
один запрос. Неполный результат не кэшируется.

Кэш сбрасывается при ошибке протокола, смене сессии, локали, валюты показа и
`client.resume()`. Между запусками он не сохраняется, поэтому новая версия
адаптера всегда начинает с нового чтения.

## Поиск игр

`client.catalog.search(query)` отправляет запрос серверному поиску. Метод
доступен без секрета аккаунта, в том числе у `Client(public_only=True)`.

```python
from funora import Client

with Client(public_only=True) as client:
    page = client.catalog.search("Minecraft")
    print(page.query, page.completeness, page.reason)
    for game in page.games(accept_incomplete=True):
        print(game.title_text.or_none(), game.href.or_none())
```

SDK убирает пробелы по краям и приводит запрос к нижнему регистру, как форма
площадки. Пустой запрос, управляющие символы, некорректный Unicode и более
200 знаков после нормализации вызывают `ValidationError` до HTTP.
Предел длины установлен SDK, ограничение площадки не наблюдено.

Результат - `CatalogPage`. `query` хранит нормализованный запрос; у полного
каталога это `None`. Скрытые варианты и все серверные совпадения сохраняются:
локального фильтра по вхождению названия нет. Поиск не меняет суточный кэш
`categories()`.

Сервер возвращает JSON с полем `html`. Непустая выдача имеет полноту `unknown`,
поскольку число всех совпадений не подтверждено; повреждённые карточки дают
`partial`. Только явный пустой `html` при проверенной целостности HTTP даёт
`complete`. Поэтому получение игр обычно требует `accept_incomplete=True`.
Неизвестный JSON или чужая разметка вызывают `ProtocolChangedError`.

## Поля раздела

`client.catalog.field_schema(section_id)` читает фильтры публичной страницы
`/lots/{section_id}/`. Это схема поиска предложений, а не форма создания лота.

```python
schema = client.catalog.field_schema("922")
for field in schema.fields():
    print(field.input_name.or_none(), field.kind)
    for option in field.options:
        print(option.value.value, option.label_text.or_none())
```

Наблюдены виды `choice` и `range`. Пустое значение варианта сохраняется:
оно означает отсутствие ограничения. Подпись поля может быть ненаблюдённой,
если интерфейс показывает только кнопки вариантов.

Неизвестный вид получает `kind="unknown"` и делает результат неполным.
Доступ к такому результату требует `schema.fields(accept_incomplete=True)`.
Отсутствующий блок полей даёт `UnsupportedCapabilityError` для текущего
раздела; это не отключает чтение других разделов.

У `AsyncClient` те же методы вызываются с `await`.
