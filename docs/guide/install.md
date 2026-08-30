# Установка

## Чего ещё нет

Пакета на PyPI нет: `pip install funora` установит не то, что вы ищете. Пока
ставится из исходников.

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
