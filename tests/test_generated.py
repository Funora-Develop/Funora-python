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

from funora import capabilities as caps
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


def test_unknown_capability_does_not_block_the_call() -> None:
    """Проверяет главное решение раздела возможностей.

    Вызов блокируется только при unsupported, то есть при позитивном
    свидетельстве отсутствия. Состояние unknown означает «ещё не выяснено»:
    неудачная проверка не доказывает, что возможности нет, и блокировать по ней
    значило бы запрещать работу из-за собственной неуверенности.

    Проверка стоит здесь, а не в спецификации, потому что ошибиться тут можно
    ровно один раз в каждой из шести реализаций, и внешне это будет выглядеть
    как «SDK ничего не умеет».

    Returns:
        None
    """
    assert caps.CapabilityState.UNKNOWN.usable
    assert not caps.CapabilityState.UNSUPPORTED.usable


def test_experimental_requires_opt_in() -> None:
    """Проверяет, что experimental недоступна без явного включения.

    Проверка переписана после того, как поймала собственную ошибку наоборот.
    Первая версия закрепляла usable=True у этого состояния, потому что такой
    признак стоит в спецификации внутри states. Но решение о вызове принимается
    по разделу predicates, и там experimental отсутствует. Признак означает
    «возможность работает», а не «звать разрешено», и генератор, выведший
    решение из него, пропускал экспериментальную возможность без включения,
    отменяя ту единственную ветку, ради которой состояние заведено.

    Returns:
        None
    """
    state = caps.CapabilityState.EXPERIMENTAL
    assert state.opt_in_required
    assert not state.usable, "usable отвечает на вопрос «звать без условий», а не «работает ли»"
    assert not state.allows_call(opted_in=False)
    assert state.allows_call(opted_in=True)

    for other in caps.CapabilityState:
        if other is not state:
            assert not other.opt_in_required


def test_unsupported_is_never_callable() -> None:
    """Проверяет, что явное включение не открывает отсутствующую возможность.

    Включение снимает предупреждение о нестабильности контракта, а не отменяет
    свидетельство того, что возможности нет.

    Returns:
        None
    """
    state = caps.CapabilityState.UNSUPPORTED
    assert not state.allows_call(opted_in=False)
    assert not state.allows_call(opted_in=True)


def test_call_decision_matches_spec_predicates() -> None:
    """Сверяет решение о вызове с предикатами спецификации напрямую.

    Проверка читает spec/capabilities.yaml и сравнивает с порождённым кодом. Она
    существует потому, что оба множества уже однажды разошлись: генератор строил
    их из описательных признаков вместо нормативных предикатов, и расхождение
    было незаметно, пока кто-то не попробовал применить спецификацию.

    Returns:
        None
    """
    spec = _spec_dir()
    if spec is None:
        pytest.skip("переменная FUNORA_SPEC_DIR не указывает на рабочую копию Funora-spec")

    import yaml

    doc = yaml.safe_load((spec / "spec" / "capabilities.yaml").read_text(encoding="utf-8"))
    predicates = doc["predicates"]
    assert predicates.get("normative"), "предикаты обязаны быть помечены нормативными"

    usable = set(predicates["is_usable"]["true_for"])
    opt_in = set(predicates["requires_opt_in"]["true_for"])

    for state in caps.CapabilityState:
        assert state.usable == (state.value in usable), (
            f"{state.value}: usable расходится с предикатом is_usable"
        )
        assert state.opt_in_required == (state.value in opt_in), (
            f"{state.value}: opt_in_required расходится с предикатом requires_opt_in"
        )


def test_degraded_is_usable() -> None:
    """Проверяет, что частичная деградация не запрещает вызов.

    Деградация означает, что часть данных со страницы извлечь не удалось.
    Запрещать вызов целиком означало бы терять и ту часть, которая читается.

    Returns:
        None
    """
    assert caps.CapabilityState.DEGRADED.usable


def test_every_capability_has_source_and_initial_state() -> None:
    """Проверяет полноту таблиц возможностей.

    Пропуск в таблице даёт отказ вида KeyError в момент вызова, то есть в
    работе, а не на сборке.

    Returns:
        None
    """
    for capability in caps.Capability:
        assert capability in caps.CAPABILITY_SOURCE
        assert capability in caps.CAPABILITY_INITIAL


def test_probe_capabilities_start_unknown() -> None:
    """Проверяет согласие источника состояния с начальным значением.

    Возможность, состояние которой выясняется проверкой, не может начинать со
    значения supported: это означало бы, что проверка уже выполнена, хотя её не
    было. Такое расхождение делает всю переговорную схему декоративной.

    Returns:
        None
    """
    for capability, source in caps.CAPABILITY_SOURCE.items():
        if source == "probe":
            assert caps.CAPABILITY_INITIAL[capability] is caps.CapabilityState.UNKNOWN, (
                f"{capability.value} выясняется проверкой, но начинает не с unknown"
            )


def test_capability_values_match_spec_names() -> None:
    """Проверяет, что значение члена совпадает с именем из спецификации.

    Значение уходит в журнал и в сообщения об ошибках, поэтому оно обязано
    совпадать с тем, что написано в спецификации, а не с именем члена.

    Returns:
        None
    """
    for capability in caps.Capability:
        assert capability.name == capability.value.replace(".", "_").upper()


def test_capability_state_refuses_boolean_coercion() -> None:
    """Проверяет, что состояние возможности нельзя привести к булеву.

    Состояний пять, и к двум они не сводятся. Запись
    ``if client.capability(cap): call()`` выглядит настолько естественно, что её
    пишут не задумываясь, - а состояние это строка, и любая непустая строка
    истинна. Проверка пропускала вызов при unsupported: ровно тот случай, ради
    которого она и написана.

    Тот же запрет стоит у Observed и по той же причине.

    Returns:
        None
    """
    from funora.capabilities import CapabilityState

    for state in CapabilityState:
        with pytest.raises(TypeError, match="булеву"):
            bool(state)

    # Сравнение и допустимость вызова при этом работают.
    assert CapabilityState.SUPPORTED.allows_call(opted_in=False)
    assert not CapabilityState.UNSUPPORTED.allows_call(opted_in=False)


def test_capability_of_an_unimplemented_operation_is_refused() -> None:
    """Проверяет отказ на возможности, под которую нет операции.

    Таблица начальных состояний отвечает на вопрос о ПЛОЩАДКЕ: наблюдается ли
    возможность там. Вызывающий спрашивает другое - «могу ли я это вызвать», - и
    одиннадцать возможностей отвечали ему supported, то есть «подтверждена и
    доступна», при том что метода под них в SDK нет вовсе.

    Код, который ветвится по состоянию, уходил в ветку «доступно» и падал на
    отсутствующем атрибуте - в лучшем случае. В худшем ветка не делала ничего.

    Returns:
        None
    """
    from funora._budget import Budget
    from funora._engine import IMPLEMENTED, Engine
    from funora._transport import TransportSettings
    from funora.capabilities import Capability
    from funora.errors import ConfigurationError

    engine = Engine(TransportSettings(), Budget())
    missing = sorted(set(Capability) - IMPLEMENTED, key=lambda item: item.value)
    assert missing, "если выполняется всё, проверка бессмысленна - её надо убрать"

    for capability in missing:
        with pytest.raises(ConfigurationError, match=capability.value):
            engine.capability(capability)


def test_capability_of_an_implemented_operation_answers() -> None:
    """Проверяет, что отказ не задел то, что выполняется.

    Returns:
        None
    """
    from funora._budget import Budget
    from funora._engine import IMPLEMENTED, Engine
    from funora._transport import TransportSettings
    from funora.capabilities import CapabilityState

    engine = Engine(TransportSettings(), Budget())
    for capability in IMPLEMENTED:
        assert isinstance(engine.capability(capability), CapabilityState)


def test_implemented_capabilities_are_the_ones_actually_called() -> None:
    """Проверяет, что перечень выполняемых заработан, а не объявлен.

    Без этой проверки правило разворачивается наизнанку: возможность вписывают в
    перечень, состояние отвечает supported, а операции по-прежнему нет - то же
    самое обещание, но теперь с разрешения.

    Сверяется с исходником цикла: возможность считается выполняемой, если она
    вправду подставляется в чтение.

    Returns:
        None
    """
    import re
    from pathlib import Path as _Path

    from funora._engine import IMPLEMENTED

    source = (_Path(__file__).resolve().parent.parent / "src" / "funora" / "_engine.py").read_text(
        encoding="utf-8"
    )
    called = set(re.findall(r"fetch_ok\(\s*(Capability\.[A-Z_]+)", source))
    called |= set(re.findall(r"capability = (Capability\.[A-Z_]+)", source))

    declared = {f"Capability.{item.name}" for item in IMPLEMENTED}
    assert declared <= called, (
        f"объявлено выполняемым, но нигде не вызывается: {sorted(declared - called)}"
    )
    assert called <= declared, (
        f"вызывается, но не объявлено выполняемым: {sorted(called - declared)} - "
        "состояние такой возможности будет отвергнуто зря"
    )


def test_safety_comes_from_the_spec_not_from_a_hand_written_list() -> None:
    """Проверяет, что безопасность операций больше не рукописная.

    Безопасность - половина нормативного входа решения о повторе; вторая
    половина, класс ошибки, порождается из errors.yaml и проверяется на
    свежесть. Первая жила рукописным перечислением с примечанием «значения взяты
    из спецификации», и смена безопасности операции с safe на unsafe не
    отражалась нигде: ни в порождённом коде, ни в проверке.

    Повторить небезопасную операцию значит выполнить её дважды - отправить
    покупателю второе сообщение.

    Returns:
        None
    """
    import funora._retry as retry_module
    from funora.operations import OPERATIONS, Safety

    assert retry_module.Safety is Safety, (
        "модуль повторов держит своё перечисление безопасности вместо порождённого"
    )
    assert OPERATIONS, "таблица операций пуста"
    assert {operation.safety for operation in OPERATIONS.values()} <= set(Safety)


def test_every_operation_names_a_declared_capability() -> None:
    """Проверяет, что операции не ссылаются на несуществующие возможности.

    Связь объявлена в двух файлах спецификации, и до порождения её не сверял
    никто: операция могла требовать возможности, которой нет, и обнаружилось бы
    это на вызове.

    Returns:
        None
    """
    from funora.capabilities import Capability
    from funora.operations import OPERATIONS

    declared = {item.value for item in Capability}
    for name, operation in OPERATIONS.items():
        assert operation.capability in declared, (
            f"операция {name} требует возможности {operation.capability}, "
            "которой нет в перечислении"
        )


def test_read_operations_are_declared_safe() -> None:
    """Проверяет безопасность выполняемых сегодня операций.

    Все три выполняемые операции - чтения, и объявлены безопасными. Проверка
    ловит правку в спецификации, которая объявит чтение небезопасным: цикл
    наблюдения повторяет чтения свободно, и небезопасное чтение он повторять бы
    не стал - то есть наблюдение молча остановилось бы на первом же отказе сети.

    Returns:
        None
    """
    from funora._engine import IMPLEMENTED
    from funora.operations import OPERATIONS, Safety

    by_capability = {op.capability: op for op in OPERATIONS.values()}
    for capability in IMPLEMENTED:
        operation = by_capability.get(capability.value)
        assert operation is not None, (
            f"возможность {capability.value} выполняется, а операции под неё в спецификации нет"
        )
        assert operation.safety is Safety.SAFE, (
            f"операция {operation.name} объявлена {operation.safety}, а цикл "
            "наблюдения повторяет её как безопасную"
        )
