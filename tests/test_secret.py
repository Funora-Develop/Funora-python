"""Проверки модуля секретов.

Главный тест здесь - канареечный. Он не проверяет отдельные методы, а прогоняет
секрет через все выходные каналы разом и падает, если канареечное значение
нашлось хотя бы в одном байте. Проверять каналы по одному бессмысленно: утечка
всегда происходит через тот, о котором не подумали.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import pickle
import traceback
from pathlib import Path

import pytest

from funora._secret import (
    CallableSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    Secret,
    SecretNotFoundError,
    SecretProvider,
)

#: Значение, которое не должно встретиться ни в одном выходном канале.
CANARY = "FUNORA_CANARY_9f3c1a7e5b204d88"


def test_reveal_returns_value() -> None:
    """Проверяет, что значение доступно через явный вызов."""
    assert Secret(CANARY).reveal() == CANARY


def test_empty_value_rejected() -> None:
    """Проверяет, что пустой секрет создать нельзя."""
    with pytest.raises(ValueError):
        Secret("")
    with pytest.raises(ValueError):
        Secret("   ")


def test_repr_and_str_are_masked() -> None:
    """Проверяет, что repr и str не содержат значения."""
    s = Secret(CANARY, label="golden_key")
    assert CANARY not in repr(s)
    assert CANARY not in str(s)
    assert "golden_key" in repr(s)


def test_fstring_is_masked() -> None:
    """Проверяет, что подстановка в f-строку не раскрывает значение.

    Без перекрытия __format__ вызов f"{secret}" ушёл бы в format у str и напечатал
    бы значение, обойдя __str__.
    """
    s = Secret(CANARY)
    assert CANARY not in f"{s}"
    assert CANARY not in f"{s:>40}"
    # format и процентное форматирование вызваны намеренно: это отдельные пути
    # приведения к строке, и каждый из них надо проверить на утечку. Замена их на
    # f-строку убрала бы смысл теста.
    assert CANARY not in "{}".format(s)  # noqa: UP032
    assert CANARY not in "%s" % (s,)  # noqa: UP031


def test_serialization_blocked() -> None:
    """Проверяет, что секрет нельзя вынести за пределы процесса."""
    s = Secret(CANARY)
    with pytest.raises(TypeError):
        pickle.dumps(s)
    with pytest.raises(TypeError):
        copy.copy(s)
    with pytest.raises(TypeError):
        copy.deepcopy(s)
    with pytest.raises(TypeError):
        json.dumps(s)


def test_equality_and_hash() -> None:
    """Проверяет сравнение и использование в множествах."""
    assert Secret(CANARY) == Secret(CANARY)
    assert Secret(CANARY) != Secret(CANARY + "x")
    assert Secret(CANARY) != CANARY
    assert len({Secret(CANARY), Secret(CANARY)}) == 1


def test_canary_not_in_any_output_channel(caplog: pytest.LogCaptureFixture) -> None:
    """Прогоняет секрет через все выходные каналы и ищет канареечное значение.

    Каналы: repr, str, format, вывод logging на уровне DEBUG, текст исключения,
    полный traceback, json-сериализация содержащей структуры.

    Args:
        caplog (pytest.LogCaptureFixture): Перехватчик журнала pytest.
    """
    s = Secret(CANARY, label="golden_key")
    channels: list[str] = [repr(s), str(s), f"{s}", format(s, ">10")]

    with caplog.at_level(logging.DEBUG):
        log = logging.getLogger("funora.test")
        log.debug("секрет в сообщении: %s", s)
        log.debug("секрет в аргументе: %r", s)
        log.debug("секрет в словаре: %s", {"key": s})
    channels.append(caplog.text)

    try:
        raise RuntimeError(f"не удалось использовать {s}")
    except RuntimeError as exc:
        channels.append(str(exc))
        channels.append("".join(traceback.format_exception(exc)))

    try:
        json.dumps({"secret": s})
    except TypeError as exc:
        channels.append(str(exc))

    channels.append(str({"secret": s}))
    channels.append(str([s]))

    leaked = [i for i, c in enumerate(channels) if CANARY in c]
    assert not leaked, f"канареечное значение найдено в каналах: {leaked}"


def test_env_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет чтение секрета из переменной окружения.

    Args:
        monkeypatch (pytest.MonkeyPatch): Инструмент подмены окружения.
    """
    monkeypatch.setenv("FUNORA_GOLDEN_KEY", CANARY)
    p = EnvSecretProvider()
    assert p.get("golden_key").reveal() == CANARY

    monkeypatch.delenv("FUNORA_GOLDEN_KEY")
    with pytest.raises(SecretNotFoundError):
        p.get("golden_key")


def test_env_provider_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что пустая переменная считается отсутствующей.

    Args:
        monkeypatch (pytest.MonkeyPatch): Инструмент подмены окружения.
    """
    monkeypatch.setenv("FUNORA_GOLDEN_KEY", "")
    with pytest.raises(SecretNotFoundError):
        EnvSecretProvider().get("golden_key")


def test_callable_provider() -> None:
    """Проверяет источник на основе функции, включая отказ."""
    p = CallableSecretProvider(lambda name: CANARY)
    assert p.get("golden_key").reveal() == CANARY

    def boom(name: str) -> str:
        raise RuntimeError("менеджер секретов недоступен")

    with pytest.raises(SecretNotFoundError):
        CallableSecretProvider(boom).get("golden_key")

    with pytest.raises(SecretNotFoundError):
        CallableSecretProvider(lambda name: "").get("golden_key")


def test_callable_provider_error_does_not_leak() -> None:
    """Проверяет, что при отказе источника значение не попадает в текст ошибки."""

    def boom(name: str) -> str:
        raise RuntimeError(f"сбой при обработке {CANARY}")

    try:
        CallableSecretProvider(boom).get("golden_key")
    except SecretNotFoundError as exc:
        assert CANARY not in str(exc)
        assert CANARY in str(exc.__cause__), "исходная причина должна сохраняться"


def test_file_provider(tmp_path: Path) -> None:
    """Проверяет чтение секрета из файла.

    Args:
        tmp_path (Path): Временный каталог, создаваемый pytest.
    """
    (tmp_path / "golden_key").write_text(CANARY + "\n", encoding="utf-8")
    if os.name == "posix":
        (tmp_path / "golden_key").chmod(0o600)

    p = FileSecretProvider(tmp_path)
    assert p.get("golden_key").reveal() == CANARY

    with pytest.raises(SecretNotFoundError):
        p.get("missing")

    (tmp_path / "empty").write_text("  \n", encoding="utf-8")
    if os.name == "posix":
        (tmp_path / "empty").chmod(0o600)
    with pytest.raises(SecretNotFoundError):
        p.get("empty")


@pytest.mark.skipif(os.name != "posix", reason="права файлов проверяются только на POSIX")
def test_file_provider_rejects_world_readable(tmp_path: Path) -> None:
    """Проверяет отказ при файле, доступном посторонним.

    Args:
        tmp_path (Path): Временный каталог, создаваемый pytest.
    """
    f = tmp_path / "golden_key"
    f.write_text(CANARY, encoding="utf-8")
    f.chmod(0o644)
    with pytest.raises(SecretNotFoundError):
        FileSecretProvider(tmp_path).get("golden_key")


def test_providers_satisfy_protocol(tmp_path: Path) -> None:
    """Проверяет, что все источники удовлетворяют протоколу.

    Args:
        tmp_path (Path): Временный каталог, создаваемый pytest.
    """
    assert isinstance(EnvSecretProvider(), SecretProvider)
    assert isinstance(CallableSecretProvider(lambda n: "x"), SecretProvider)
    assert isinstance(FileSecretProvider(tmp_path), SecretProvider)


def test_the_secret_file_option_accepts_a_file_as_well_as_a_directory(
    tmp_path: Path,
) -> None:
    """Требует принимать и сам файл ключа, и каталог, где он лежит.

    Ключ командной строки называется --secret-file, а принимался только
    каталог: человек, прочитавший имя буквально, указывал файл и получал
    «файл секрета не найден: golden_key/golden_key».

    Имя обещало одно, поведение требовало другого - и это стоило трёх попыток
    подряд у того, кто снимал наблюдения.

    Возвращает:
        None
    """
    target = tmp_path / "golden_key"
    target.write_text("значение-ключа\n", encoding="utf-8")

    by_directory = FileSecretProvider(tmp_path, check_permissions=False)
    by_file = FileSecretProvider(target, check_permissions=False)

    assert by_directory.get("golden_key").reveal() == "значение-ключа"
    assert by_file.get("golden_key").reveal() == "значение-ключа", (
        "указание самого файла не сработало, хотя ключ так и называется"
    )


def test_a_missing_secret_file_says_what_to_do(tmp_path: Path) -> None:
    """Требует, чтобы отказ называл оба допустимых способа.

    Отказ, говорящий только «не найден», оставляет человека гадать, что именно
    от него хотят - путь к файлу или к каталогу.

    Возвращает:
        None
    """
    provider = FileSecretProvider(tmp_path / "нет-такого", check_permissions=False)
    with pytest.raises(SecretNotFoundError) as refused:
        provider.get("golden_key")

    text = str(refused.value)
    assert "сам файл" in text and "каталог" in text, f"отказ не говорит, что делать: {text}"


def test_a_missing_secret_file_points_at_the_near_miss(tmp_path: Path) -> None:
    """Требует называть похожий файл, лежащий рядом.

    Блокнот дописывает расширение, когда файла ещё не было: человек сохраняет
    golden_key и получает golden_key.txt. Отказ, говорящий только «не найден»,
    оставляет его гадать - при том что нужный файл лежит рядом и виден.

    Это случилось на живой съёмке и стоило ещё одного круга.

    Возвращает:
        None
    """
    (tmp_path / "golden_key.txt").write_text("значение\n", encoding="utf-8")

    provider = FileSecretProvider(tmp_path / "golden_key", check_permissions=False)
    with pytest.raises(SecretNotFoundError) as refused:
        provider.get("golden_key")

    text = str(refused.value)
    assert "golden_key.txt" in text, f"отказ не назвал соседа: {text}"
    assert "--secret-file" in text, "отказ не показал готовой команды"
    # Значение самого секрета в отказе не появляется никогда.
    assert "значение" not in text, f"содержимое файла попало в отказ: {text}"


def test_a_missing_secret_file_without_a_near_miss_stays_short(tmp_path: Path) -> None:
    """Требует не выдумывать соседа там, где его нет.

    Подсказка, срабатывающая всегда, перестаёт быть подсказкой.

    Возвращает:
        None
    """
    provider = FileSecretProvider(tmp_path / "golden_key", check_permissions=False)
    with pytest.raises(SecretNotFoundError) as refused:
        provider.get("golden_key")

    assert "Рядом лежит" not in str(refused.value), (
        f"подсказка о соседе выдана без соседа: {refused.value}"
    )
