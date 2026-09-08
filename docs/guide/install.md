# Установка

## Тестовая сборка

Версия `0.0.1.dev1` распространяется через GitHub pre-release.
Скачивание, установка wheel и проверка контрольной суммы описаны в
[заметке о выпуске](../pre-alpha.md). В PyPI эта сборка не публикуется.

Для работы с исходниками используйте тот же тег:

```bash
git clone --branch v0.0.1.dev1 https://github.com/Funora-Develop/Funora-python.git
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

Для полного прогона нужна соседняя рабочая копия `Funora-spec` версии `0.59.0`.
CI закреплён на ревизии `a1c66789b42b8d44a94a7a8489059e708c1bd0d7`.
Укажите корень спецификации через `FUNORA_SPEC_DIR`. В корне этой копии
выполните `npm ci`: полный набор использует зависимости раннера conformance.

```bash
python -m pip install -e ".[dev,docs]" build twine
python -m pytest -q --cov=funora --cov-fail-under=100
python tools/codegen.py --check
python -m mkdocs build --strict
python -m build
python -m twine check dist/*
python tools/check_distribution.py dist
```

Последняя команда устанавливает wheel в отдельное временное окружение и
проверяет импорт публичного API и CLI вне дерева исходников. Перед публикацией
CI также требует совпадения тега `v<версия>` с `pyproject.toml` и
`funora.__version__`. Сборка выпуска выполняется только после полного набора CI.
Для тегов `.dev` шаг публикации в PyPI пропускается; проверенные wheel и sdist
прикладываются к GitHub pre-release вместе с `SHA256SUMS`.
