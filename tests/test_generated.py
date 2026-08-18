"""Проверки порождённых из спецификации модулей.

Набор отвечает на два разных вопроса, и путать их не стоит.

Первый: не отстал ли порождённый файл от спецификации. Это проверяется только
при доступной рабочей копии Funora-spec и работает так же, как сверка
селекторов: файл строится заново и сравнивается посимвольно. Расхождение
означает, что спецификацию изменили, а сборку не перезапустили, - и без такой
проверки шесть реализаций разъехались бы молча, каждая оставаясь внутри себя
непротиворечивой.

Второй: выполняются ли свойства, которые обязаны выполняться при любом
содержимом спецификации. Уникальность кодов, достижимость от корня, отсутствие
пропусков в реестрах. Эти проверки не требуют спецификации и работают всегда,
потому что защищают от ошибки в самом генераторе, а не в источнике.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from funora import errors as mod

#: Корень репозитория, от которого ищется генератор.
ROOT = Path(__file__).resolve().parent.parent

#: Все классы ошибок, объявленные в порождённом модуле.
CLASSES = [getattr(mod, name) for name in mod.__all__ if name.endswith("Error")]


def _spec_dir() -> Path | None:
    """Возвращает путь к рабочей копии спецификации, если он задан.

    Returns:
        Path | None: Каталог Funora-spec либо None, если переменная окружения
        FUNORA_SPEC_DIR не задана или указывает не туда.
    """
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / "spec" / "errors" / "errors.yaml").is_file() else None


def test_generated_file_matches_spec() -> None:
    """Проверяет, что порождённый модуль не отстал от спецификации.

    Returns:
        None
    """
    spec = _spec_dir()
    if spec is None:
        pytest.skip("переменная FUNORA_SPEC_DIR не указывает на рабочую копию Funora-spec")

    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import codegen
    finally:
        sys.path.pop(0)

    for name, body in codegen.generate(spec).items():
        current = (ROOT / "src" / "funora" / name).read_text(encoding="utf-8")
        assert current == body, (
            f"{name} отстал от спецификации. Перестройте: python tools/codegen.py"
        )


def test_every_error_descends_from_root() -> None:
    """Проверяет, что каждая ошибка достижима от корня иерархии.

    Ошибка вне иерархии не ловится обработчиком, написанным на базовый тип, и
    обнаруживается это в работе, а не на тестах.

    Returns:
        None
    """
    for cls in CLASSES:
        assert issubclass(cls, mod.FunoraError)


def test_abi_codes_are_unique() -> None:
    """Проверяет уникальность числовых кодов.

    Код одинаков во всех шести SDK и служит для опознания ошибки при передаче
    между реализациями. Совпадение двух кодов означает, что одна ошибка будет
    истолкована как другая.

    Returns:
        None
    """
    codes = [cls.abi_code for cls in CLASSES]
    assert len(codes) == len(set(codes))


def test_stable_ids_are_unique() -> None:
    """Проверяет уникальность устойчивых идентификаторов.

    Returns:
        None
    """
    ids = [cls.stable_id for cls in CLASSES]
    assert len(ids) == len(set(ids))


def test_registries_cover_every_class() -> None:
    """Проверяет, что оба реестра содержат все ошибки.

    Пропуск в реестре даёт худший вид отказа: поиск возвращает None, вызывающий
    подставляет базовый тип, и ошибка теряет и класс, и признаки.

    Returns:
        None
    """
    assert len(mod.ERROR_BY_STABLE_ID) == len(CLASSES)
    assert len(mod.ERROR_BY_ABI_CODE) == len(CLASSES)
    for cls in CLASSES:
        assert mod.ERROR_BY_STABLE_ID[cls.stable_id] is cls
        assert mod.ERROR_BY_ABI_CODE[cls.abi_code] is cls


def test_budget_error_is_not_a_transport_error() -> None:
    """Проверяет решение спецификации, которое легко потерять при правке.

    Исчерпание бюджета намеренно не является разновидностью ошибки транспорта.
    Иначе оно попало бы под политику повторов для транспорта, и клиент начал бы
    повторять запросы именно тогда, когда бюджет уже кончился.

    Returns:
        None
    """
    assert not issubclass(mod.BudgetExhaustedError, mod.TransportError)
    assert issubclass(mod.BudgetExhaustedError, mod.BudgetError)


def test_transport_errors_are_retryable() -> None:
    """Проверяет согласие признака повторяемости с иерархией.

    Ошибка транспорта повторяема по определению: соединение не установилось,
    значит запрос не дошёл. Непомеченная ошибка под этим родителем означала бы
    расхождение между иерархией и политикой повторов.

    Returns:
        None
    """
    for cls in CLASSES:
        if issubclass(cls, mod.TransportError):
            assert cls.retryable, f"{cls.__name__} под TransportError, но не повторяема"


def test_no_error_is_both_unretryable_and_silent() -> None:
    """Проверяет, что неповторяемая ошибка с побочным действием объяснена.

    Сочетание «повторять нельзя» и «действие могло произойти» - самое опасное
    для вызывающего: он не знает, состоялось ли действие, и не может выяснить
    это повтором. Такие ошибки обязаны иметь непустое пояснение.

    Returns:
        None
    """
    for cls in CLASSES:
        if not cls.retryable and cls.side_effects_possible:
            assert cls.__doc__ and len(cls.__doc__.strip()) > 40, (
                f"{cls.__name__} требует внятного пояснения: повтор запрещён, "
                "а действие могло произойти"
            )
