"""Проверяемая связь контракта, Python API, тестов и оставшихся ограничений.

Матрица хранит только рукописные соответствия. Возможности, результаты,
безопасность и происхождение берутся из контракта, ограничения - из реестра.
Наличие ссылки на тест не доказывает его успех: все тесты выполняет общий CI.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
from pathlib import Path

import yaml

from funora import _aclient, _client
from funora._engine import Engine
from funora._watch import PRODUCIBLE
from funora.contract import SPEC_VERSION
from funora.events import EventType
from funora.operations import OPERATIONS

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "tests/fixtures/completeness.json"
REPORT_PATH = ROOT / "docs/completeness.md"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def validate(matrix, spec: Path, root: Path = ROOT):
    """Отказывает при потерянной операции, ограничении или проверке поведения."""
    items = yaml.safe_load(
        (spec / "spec/conformance/not-implemented.yaml").read_text(encoding="utf-8")
    )["items"]
    declared = {}
    for path in sorted((spec / "spec/services").glob("*.yaml")):
        declared.update(yaml.safe_load(path.read_text(encoding="utf-8"))["operations"])
    if set(matrix["operations"]) != set(declared) or set(declared) != set(OPERATIONS):
        raise ValueError("состав операций матрицы, спецификации и SDK разошёлся")
    if set(matrix["backlog"]) != set(items):
        raise ValueError("состав ограничений матрицы и реестра разошёлся")
    missing = {one.value for one in EventType if one not in PRODUCIBLE}
    if set(matrix["missing_events"]) != missing:
        raise ValueError("состав отсутствующих событий разошёлся с реализацией")

    test_names = {}
    bindings = set()
    for name, row in matrix["operations"].items():
        operation = OPERATIONS[name]
        body = declared[name]
        for field in ("capability", "returns", "safety"):
            if getattr(operation, field) != body[field]:
                raise ValueError(f"{name}: устаревшее поле {field}")
        provenance = body.get("request_provenance", {}).get("confidence", "")
        if operation.request_provenance != provenance:
            raise ValueError(f"{name}: устаревшее происхождение протокола")
        binding = row["service"], row["method"]
        if binding in bindings:
            raise ValueError(f"{name}: повтор метода в матрице")
        bindings.add(binding)
        if not callable(getattr(Engine, row["engine"], None)):
            raise ValueError(f"{name}: отсутствует метод Engine.{row['engine']}")
        methods = []
        for module, prefix in ((_client, ""), (_aclient, "Async")):
            service = getattr(module, prefix + row["service"], None)
            method = getattr(service, row["method"], None)
            if not callable(method):
                raise ValueError(f"{name}: отсутствует метод {prefix}{row['service']}")
            methods.append(method)
        if inspect.signature(methods[0]) != inspect.signature(methods[1]):
            raise ValueError(f"{name}: сигнатуры sync/async различаются")
        if inspect.iscoroutinefunction(methods[0]) or not inspect.iscoroutinefunction(methods[1]):
            raise ValueError(f"{name}: нарушена модель выполнения sync/async")
        promised = operation.returns
        result = (
            "None"
            if promised == "void"
            else f"tuple[{promised[:-2]}, ...]"
            if promised.endswith("[]")
            else promised
        )
        if inspect.signature(methods[0]).return_annotation != result:
            raise ValueError(f"{name}: тип результата не совпадает с контрактом")
        if not set(row["limits"]) <= set(items):
            raise ValueError(f"{name}: ссылка на отсутствующее ограничение")
        if not row["tests"]:
            raise ValueError(f"{name}: не названа проверка поведения")
        for reference in row["tests"]:
            file, test = reference.split("::")
            if file not in test_names:
                path = root / file
                if not path.is_file() or not file.startswith("tests/test_"):
                    raise ValueError(f"{name}: отсутствует набор {file}")
                tree = ast.parse(path.read_text(encoding="utf-8"))
                test_names[file] = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")
                }
            if test not in test_names[file]:
                raise ValueError(f"{name}: отсутствует проверка {reference}")
    for module, prefix in ((_client, ""), (_aclient, "Async")):
        actual = {
            (name.removeprefix(prefix), method)
            for name, service in vars(module).items()
            if name.endswith("Service") and isinstance(service, type)
            for method, value in vars(service).items()
            if not method.startswith("_") and callable(value)
        }
        if actual != bindings:
            raise ValueError(f"{module.__name__}: методы служб вне матрицы")
    return items


def cell(value):
    return " ".join(str(value).split()).replace("|", "&#124;")


def render(matrix, items):
    third_party = sum(one.request_provenance == "third_party_report" for one in OPERATIONS.values())
    lines = [
        "# Полнота Python SDK",
        "",
        "<!-- Порождено tools/completeness.py; рукописные связи: "
        "tests/fixtures/completeness.json. -->",
        "",
        f"Контракт {SPEC_VERSION}: {len(OPERATIONS)} операций доступны через Client и AsyncClient; "
        f"{third_party} частично опираются на сторонний протокол. "
        f"Порождаются {len(PRODUCIBLE)} из {len(EventType)} видов событий. "
        f"Открытых пунктов реестра: {len(items)}, включая один пункт вне Python.",
        "",
        "Это карта реализации и проверок, а не сертификат готовности площадки. "
        "Тесты на записанных и синтетических ответах не заменяют собственные наблюдения. "
        "100% строк SDK не означает проверку всех ветвей или реализацию всего целевого API.",
        "",
        "## Операции и проверки",
        "",
        "Имена после `client.` одинаковы у обоих клиентов; у AsyncClient вызов "
        "ожидают через `await`. "
        "Матрица проверяет сигнатуры и передачу аргументов в Engine, результат и выбор транспорта. "
        "Ниже названа опорная проверка поведения каждого метода; остальные сценарии "
        "выполняет общий набор тестов.",
        "",
        "| Операция | Метод клиента | Метод Engine | Результат | Проверка поведения |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, row in matrix["operations"].items():
        service = row["service"].removesuffix("Service").lower()
        tests = "<br>".join(f"`{one}`" for one in row["tests"])
        lines.append(
            f"| `{name}` | `{service}.{row['method']}` | `{row['engine']}` | "
            f"`{OPERATIONS[name].returns}` | {tests} |"
        )
    lines += [
        "",
        "## Возможности и границы протокола",
        "",
        "`third_party_report` означает недостающее собственное наблюдение запроса, "
        "ответа либо его эффекта. "
        "`observed` ниже означает отсутствие этой пометки в контракте; известные "
        "ограничения сохраняются. "
        "Безопасность повтора (`safe`, `idempotent`, `unsafe`) не является разрешением на запись. "
        "Для сторонних записей сохраняется явный opt-in.",
        "",
        "| Операция | Capability | Повтор | Основание | Связанные ограничения |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, row in matrix["operations"].items():
        one = OPERATIONS[name]
        limits = ", ".join(f"`{item}`" for item in row["limits"]) or "Общие ограничения ниже"
        lines.append(
            f"| `{name}` | `{one.capability}` | `{one.safety.value}` | "
            f"{one.request_provenance or 'observed'} | {limits} |"
        )
    lines += [
        "",
        "## Отсутствующие события",
        "",
        "Регистрация обработчика каждого из этих событий отклоняется с ConfigurationError. "
        "Изменения цены рынка не заменяют события собственного лота.",
        "",
        "| Событие | Оставшаяся работа |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | {cell(note)} |" for name, note in matrix["missing_events"].items()]
    lines += [
        "",
        "## Открытые ограничения и этапы",
        "",
        "Группы пересекаются. Запись закрывается после реализации и проверки её условий, "
        "а не после появления имени метода. Межъязыковой пункт не блокирует Python.",
        "",
        "B - наблюдения; C - чтение и модели; D - управление лотами; E - события; "
        "F - сессия и runner; G - нагрузка и обработчики; H - ошибки и совместимость; "
        "I - финансовые операции. A - актуализация контрактов и этой матрицы.",
        "",
        "| Пункт | Этап | Формулировка реестра |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| `{name}` | {matrix['backlog'][name]} | {cell(item['what'])} |"
        for name, item in items.items()
    ]
    lines += [
        "",
        "## Объём сверх текущих сервисных операций",
        "",
        "- Создание, полная правка и удаление лотов; чтение и сохранение собственных chips.",
        "- Реквизиты, кошельки и вывод: `account.withdraw` сейчас отклоняется "
        "типизированной ошибкой "
        "и не входит в контрактные операции. Нужны контракт, защита повторов и отдельная приёмка.",
        "- Выбор разделов поднятия; проверка состава публичного профиля и страницы лота.",
        "- Сверка составных сценариев Cardinal: пакетные истории чатов, загрузка изображения "
        "отдельно от отправки, lookup диалогов и logout. Новый метод нужен при самостоятельном "
        "поведении, а не ради копирования вспомогательных имён.",
        "",
        "## Runtime и итоговая приёмка",
        "",
        "`monitoring.plan/watch`, восстановление очередей, бюджеты, дедупликация и "
        "журнал исходящих "
        "проверяются отдельно от HTTP-операций: `tests/test_monitoring_history.py`, "
        "`tests/test_monitoring_contract.py`, `tests/test_watch_contract.py`, "
        "`tests/test_budget_concurrency.py`, `tests/test_outbound_restore.py`. "
        "Оставшиеся ограничения runtime перечислены в общей таблице.",
        "",
        "Для выпуска нужны проверенные живые сценарии, ветви ошибок и отмены, длительный прогон, "
        "сборка и установка wheel/sdist и миграция состояния. См. [проверки](validation.md) "
        "и [ограничения](limits.md).",
        "",
        "Обновление: `python tools/completeness.py`; проверка без записи: "
        "`python tools/completeness.py --check`. Обе команды требуют `FUNORA_SPEC_DIR`.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--spec-dir", default=os.environ.get("FUNORA_SPEC_DIR"))
    args = parser.parse_args()
    if not args.spec_dir:
        parser.error("требуется FUNORA_SPEC_DIR или --spec-dir")
    matrix = load_matrix()
    try:
        report = render(matrix, validate(matrix, Path(args.spec_dir)))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.check:
        if not REPORT_PATH.is_file() or REPORT_PATH.read_text(encoding="utf-8") != report:
            parser.error("матрица устарела: выполните python tools/completeness.py")
    else:
        REPORT_PATH.write_text(report, encoding="utf-8")
    print("Матрица операций, событий и ограничений сверена")


if __name__ == "__main__":
    main()
