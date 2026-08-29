"""Проверяет, что причина отказа в повторе видна вызывающему.

Матрица решений различает пять исходов, и один из них - reconcile_first -
означает не «повторять бессмысленно», а «повторять НЕЛЬЗЯ, сперва сверься».
Разница появляется ровно на операциях записи: отправленное сообщение могло
пройти, неоднозначным был исход, а не результат.

Снаружи эти исходы были неотличимы: движок делал голый raise, и причина уходила
в журнал в лучшем случае. На чтениях безвредно, на первой же записи - тот самый
сигнал, ради которого матрица и заведена.
"""

from __future__ import annotations

import pytest

from funora._budget import Budget
from funora._engine import Engine, Fetch
from funora._retry import RETRY_REASON_ATTR
from funora._transport import TransportSettings
from funora.errors import FunoraError, TransportError
from funora.retry import RetryDecision


def _drive(engine: Engine, core, failure: BaseException) -> BaseException:
    """Крутит ядро, отвечая на каждое обращение одним и тем же отказом.

    Аргументы:
        engine (Engine): движок.
        core: сопрограмма операции.
        failure (BaseException): чем отвечать на просьбу об обращении.

    Возвращает:
        BaseException: то, что ядро выпустило наружу.
    """
    pending: BaseException | None = None
    for _ in range(64):
        try:
            request = core.throw(pending) if pending else core.send(None)
        except StopIteration:  # pragma: no cover - операция не должна завершиться
            pytest.fail("операция завершилась успехом, а ожидался отказ")
        except FunoraError as escaped:
            return escaped
        pending = failure if isinstance(request, Fetch) else None
    pytest.fail("ядро не выпустило отказ наружу за разумное число шагов")


def test_a_refusal_to_retry_carries_its_reason_outward() -> None:
    """Требует, чтобы отказ в повторе нёс машиночитаемую причину.

    Возвращает:
        None
    """
    engine = Engine(TransportSettings(), Budget())
    escaped = _drive(engine, engine.read_chats(), TransportError("сети нет"))

    reason = getattr(escaped, RETRY_REASON_ATTR, None)
    assert reason is not None, (
        "отказ в повторе вышел наружу без причины: вызывающий не отличит "
        "«повторять бессмысленно» от «сперва сверься»"
    )
    assert isinstance(reason, str) and reason


def test_the_reconcile_first_decision_is_named_by_the_matrix() -> None:
    """Требует, чтобы имя решения совпадало с объявленным в матрице.

    Проверка держит СВЯЗЬ имени с контрактом. Разойдись они - вызывающий
    сверялся бы со строкой, которой матрица не знает, и молча перестал бы
    сверяться вовсе.

    Возвращает:
        None
    """
    assert str(RetryDecision.RECONCILE_FIRST) == "reconcile_first"


def test_every_refusal_in_the_matrix_can_be_told_apart() -> None:
    """Требует, чтобы решения матрицы различались по имени.

    Признак ставится на ВСЯКИЙ отказ, а не только на reconcile_first: правило
    «переносим одну причину из пяти» разошлось бы с матрицей молча, стоит
    добавить в неё шестую.

    Возвращает:
        None
    """
    names = [str(one) for one in RetryDecision]
    assert len(set(names)) == len(names), f"решения матрицы неразличимы по имени: {names}"
