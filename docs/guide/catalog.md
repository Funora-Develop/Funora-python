# Каталог и поля раздела

`client.catalog.categories()` читает игры, варианты и разделы с корня площадки.
Записи доступны через `page.games()`. При неполном результате требуется
`accept_incomplete=True`.

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
