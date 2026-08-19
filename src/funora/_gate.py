"""Ворота возможности: пускать вызов или отказать.

Ворота задают два вопроса, а не один, и порядок между ними значения не имеет -
важно, что задаются оба. Первый: есть ли позитивное свидетельство того, что
возможности нет. Второй: не требует ли состояние явного включения, которого
вызывающий не давал.

Ошибиться здесь легко ровно одним способом: спросить «работает ли возможность»
вместо «можно ли звать её прямо сейчас». Это разные вопросы, и у состояния
experimental ответы на них противоположны. Так и произошло в первой версии
порождённого модуля возможностей.

Отдельно стоит объяснить, почему unknown пропускается. Состояние означает «ещё
не выяснено», а не «нет». Блокировать по нему значило бы запрещать работу из-за
собственной неуверенности, и SDK выглядел бы как не умеющий ничего. Позитивным
свидетельством отсутствия может быть только полученный ответ, а не отсутствие
ответа.
"""

from __future__ import annotations

from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import ExperimentalCapabilityError, UnsupportedCapabilityError

__all__ = ["check_capability"]


def check_capability(
    capability: Capability,
    *,
    state: CapabilityState | None = None,
    opted_in: bool = False,
) -> CapabilityState:
    """Проверяет, разрешён ли вызов возможности.

    Args:
        capability (Capability): Проверяемая возможность.
        state (CapabilityState | None): Текущее состояние. По умолчанию берётся
            начальное из спецификации.
        opted_in (bool): Включил ли вызывающий возможность явно.

    Returns:
        CapabilityState: Состояние, с которым вызов пропущен.

    Raises:
        UnsupportedCapabilityError: Если есть позитивное свидетельство того, что
            возможности нет.
        ExperimentalCapabilityError: Если состояние требует явного включения, а
            вызывающий его не дал. Контракт такой возможности может измениться,
            и молча его принимать нельзя за того, кто про это не знает.
    """
    current = state if state is not None else CAPABILITY_INITIAL[capability]

    # Решение берётся у порождённого allows_call, а не собирается здесь заново.
    # Правило объявлено нормативным в spec/capabilities.yaml, и второй его
    # экземпляр разошёлся бы с первым молча: состояния те же, поведение разное.
    if current.allows_call(opted_in=opted_in):
        return current

    if current.opt_in_required:
        raise ExperimentalCapabilityError(
            f"возможность {capability.value} экспериментальна: контракт может "
            "измениться. Включите её явно, если готовы к этому"
        )

    raise UnsupportedCapabilityError(
        f"возможность {capability.value} отсутствует по наблюдению. "
        "Это не догадка: состояние выставляется только по полученному ответу"
    )
