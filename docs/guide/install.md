# Установка

## Чего ещё нет

Эта сборка ещё не опубликована на PyPI. Устанавливайте её из исходников или
из собранного wheel.

```bash
git clone https://github.com/Funora-Develop/Funora-python.git
cd Funora-python
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

На Linux и macOS путь к интерпретатору другой - `.venv/bin/python`.

## Что требуется

| Что | Версия | Зачем |
|---|---|---|
| Python | 3.11 или новее | синтаксис и `StrEnum` |
| `httpx` | 0.27+ | транспорт |
| `selectolax` | 0.3.21+ | разбор разметки |

Зависимостей ровно две, и это осознанно: SDK, который тянет за собой пол-мира,
трудно поставить рядом с чужим кодом.

## Проверка, что всё встало

```python
import funora

print(funora.__version__)
```

## Что ещё лежит в пакете

Кроме библиотеки ставится инструмент наблюдений - `funora-observe`. Им сняты все
факты о протоколе, на которых стоит контракт. Он вам не понадобится, пока вы не
захотите закрыть один из [открытых вопросов](../protocol-questions.md); подробно
о нём - в [Как наблюдать](../observing.md).

!!! warning "Запускать его как `funora-observe` можно не всегда"

    Если виртуальное окружение не активировано, консольной команды в `PATH` нет.
    Зовите модулем:

    ```bash
    .venv/Scripts/python.exe -m funora.observe --help
    ```

## Сборка руководства локально

```bash
.venv/Scripts/python.exe -m pip install -e ".[docs]"
.venv/Scripts/python.exe -m mkdocs serve
```


## Проверки и сборка пакета

Для полного прогона нужна соседняя рабочая копия `Funora-spec` версии `0.53.0`.
CI закреплён на ревизии `aee8a058c00a1acb05508866d61b0f1337e5f71e`.
Укажите корень спецификации через `FUNORA_SPEC_DIR`.

```bash
python -m pip install -e ".[dev,docs]" build twine
python -m pytest -q --cov=funora --cov-fail-under=94
python tools/codegen.py --check
python -m mkdocs build --strict
python -m build
python -m twine check dist/*
python tools/check_distribution.py dist
```

Последняя команда устанавливает wheel в отдельное временное окружение и
проверяет импорт публичного API и CLI вне дерева исходников. Перед публикацией
CI также требует совпадения тега `v<версия>` с `pyproject.toml` и
`funora.__version__`. Публикация выполняется только после полного набора CI.
