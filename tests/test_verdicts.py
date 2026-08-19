"""Проверки соответствия вердиктов ошибкам.

Главная проверка здесь - полнота таблицы. Она обходит все пары, которые
классификатор способен вернуть, и требует записи для каждой. Пара без записи
означает, что реализации выберут ошибку сами, а расходятся они как раз на
негативных ветках: там ошибка заметна позже всего и стоит дороже всего.

Перечень пар собирается из исходника классификатора, а не пишется рядом:
написанный рядом он устарел бы в тот момент, когда кто-нибудь добавит сигнатуру.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from funora._classify import DEFAULT_SIGNATURES, ResponseClass, Verdict
from funora._verdicts import error_for
from funora.errors import (
    AccessBlockedError,
    ChallengeRequiredError,
    InvalidCredentialsError,
    ProtocolChangedError,
    RateLimitedError,
    SessionExpiredError,
)
from funora.response_classes import RESPONSE_CLASSES, VERDICT_ERRORS

#: Исходник классификатора. Перечень причин собирается из него.
_SOURCE = Path(__file__).parent.parent / "src" / "funora" / "_classify.py"


def _reasons_in_source() -> set[str]:
    """Собирает все машиночитаемые причины, которые способен вернуть классификатор.

    Returns:
        set[str]: Причины, включая имена сигнатур с префиксом ``signature:``.
    """
    text = _SOURCE.read_text(encoding="utf-8")
    reasons = set(re.findall(r'reason="([a-z0-9_]+)"', text))
    reasons |= set(re.findall(r'"(http_\d{3})"', text))
    reasons |= {f"signature:{sig.name}" for sig in DEFAULT_SIGNATURES}
    return reasons


def test_response_classes_match_the_enum() -> None:
    """Проверяет, что перечень классов совпадает с перечислением в коде.

    Расхождение означает, что спецификация и реализация разошлись в самом
    словаре, а не в поведении, и любое сравнение поведения потеряет смысл.

    Returns:
        None
    """
    assert {c.value for c in ResponseClass} == set(RESPONSE_CLASSES)


def test_every_reason_has_a_table_entry() -> None:
    """Проверяет полноту таблицы по всем причинам из исходника.

    Returns:
        None
    """
    known = {reason for _, reason in VERDICT_ERRORS}
    missing = _reasons_in_source() - known
    assert not missing, f"причины без записи в таблице: {sorted(missing)}"


def test_every_class_has_at_least_one_entry() -> None:
    """Проверяет, что ни один класс ответа не остался без записи.

    Returns:
        None
    """
    covered = {cls for cls, _ in VERDICT_ERRORS}
    assert covered == set(RESPONSE_CLASSES)


def test_ok_produces_no_error() -> None:
    """Проверяет, что пригодный ответ не превращается в ошибку.

    Returns:
        None
    """
    verdict = Verdict(cls=ResponseClass.OK, reason="identity_confirmed")
    assert error_for(verdict) is None


def test_session_expired_splits_by_history() -> None:
    """Проверяет разделение истёкшей сессии и неверного секрета.

    Выглядят они одинаково, а лечатся по-разному: первую имеет смысл обновить и
    повторить, второй повторять бессмысленно, и попытка обновления только
    добавит подозрительных запросов к аккаунту, который и так под вопросом.

    Returns:
        None
    """
    verdict = Verdict(
        cls=ResponseClass.LOGIN_REQUIRED,
        reason="signature:guest_navbar",
        matched="guest_navbar",
    )
    assert isinstance(error_for(verdict, session_ever_valid=True), SessionExpiredError)
    assert isinstance(error_for(verdict, session_ever_valid=False), InvalidCredentialsError)


def test_rate_limited_is_not_access_blocked() -> None:
    """Проверяет, что превышение частоты не превращается в блокировку.

    Блокировка трактуется как отказ с закрытым замком. Попади код 429 в неё,
    первое же попадание в ограничение остановило бы опрос навсегда, а политика
    повторов для этого случая осталась бы недостижимым кодом.

    Returns:
        None
    """
    error = error_for(Verdict(cls=ResponseClass.RATE_LIMITED, reason="http_429"))
    assert isinstance(error, RateLimitedError)
    assert not isinstance(error, AccessBlockedError)
    assert RateLimitedError.retryable


def test_access_blocked_is_not_retryable() -> None:
    """Проверяет, что настоящая блокировка повтору не подлежит.

    Returns:
        None
    """
    error = error_for(Verdict(cls=ResponseClass.BLOCKED, reason="http_403"))
    assert isinstance(error, AccessBlockedError)
    assert not AccessBlockedError.retryable


@pytest.mark.parametrize("provisional", [True, False])
def test_provisional_flag_travels_to_the_instance(provisional: bool) -> None:
    """Проверяет перенос признака непроверенности в экземпляр ошибки.

    Признак живёт на экземпляре, а не на классе: непроверенность - свойство
    конкретного решения. Один и тот же ChallengeRequiredError может быть поднят
    и по структурному признаку, и по догадке о тексте, и повторять во втором
    случае нельзя.

    Args:
        provisional (bool): Было ли решение принято непроверенной сигнатурой.

    Returns:
        None
    """
    verdict = Verdict(
        cls=ResponseClass.CHALLENGE,
        reason="signature:challenge_text",
        matched="challenge_text",
        provisional=provisional,
    )
    error = error_for(verdict)
    assert isinstance(error, ChallengeRequiredError)
    assert error.provisional is provisional  # type: ignore[attr-defined]


def test_unknown_pair_is_loud() -> None:
    """Проверяет, что неизвестная пара не превращается в выдуманную ошибку.

    Придуманная на месте ошибка разойдётся между реализациями именно там, где
    расхождение дороже всего.

    Returns:
        None
    """
    verdict = Verdict(cls=ResponseClass.UNKNOWN, reason="совершенно новая причина")
    with pytest.raises(ProtocolChangedError):
        error_for(verdict)


def test_error_message_carries_no_page_content() -> None:
    """Проверяет, что в текст ошибки не попадает содержимое страницы.

    Текст уходит в журнал и в issue, поэтому персональных данных и текста
    страницы в нём быть не должно.

    Returns:
        None
    """
    verdict = Verdict(
        cls=ResponseClass.BLOCKED,
        reason="signature:blocked_text",
        matched="blocked_text",
        detail={"pattern": r"\bбан\b"},
    )
    error = error_for(verdict)
    assert error is not None
    assert "бан" not in str(error)
