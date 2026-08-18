"""Проверки режима двух чтений.

Режим существует ради одного вывода: отличить монотонный счётчик от хеша
состояния. Поэтому здесь проверяется не только то, что сравнение считается, но и
то, что оно не создаёт файлов и не выносит значения в вывод. Нарушение любого из
этих двух условий делает режим непригодным: значения со страницы под
авторизацией не должны оказываться ни на диске, ни в тексте, который потом
попадёт в обсуждение.
"""

from __future__ import annotations

import pytest

from funora import observe as observe_mod
from funora._secret import CallableSecretProvider, Secret
from funora._transport import Observation, TransportSettings

NODE_BEFORE = "1000000001"
NODE_AFTER = "1000000005"
CHAT_TAG_BEFORE = "aa11bb22"
CHAT_TAG_AFTER = "zz99xx88"
DIALOG = "700000001"


def _page(node_msg: str, chat_tag: str) -> str:
    """Собирает страницу с маркером вошедшего и отслеживаемыми значениями.

    Args:
        node_msg (str): Значение data-node-msg.
        chat_tag (str): Значение data-chat.

    Returns:
        str: Разметка страницы.
    """
    return (
        "<html><body>"
        '<button class="navbar-toggle navbar-toggle-logged"></button>'
        f'<div class="hidden" data-chat="{chat_tag}"></div>'
        f'<a class="contact-item" data-id="{DIALOG}" data-node-msg="{node_msg}" '
        f'data-user-msg="{NODE_BEFORE}"></a>'
        "</body></html>"
    )


class _SequenceFetcher:
    """Отдаёт заранее заданные ответы по очереди.

    Args:
        secret (Secret): Секрет. Принимается для совместимости, не используется.
        settings (TransportSettings | None): Настройки. Не используются.
    """

    pages: list[str] = []
    calls: int = 0

    def __init__(self, secret: Secret, settings: TransportSettings | None = None) -> None:
        self._secret = secret

    def __enter__(self) -> _SequenceFetcher:
        """Входит в контекстный менеджер.

        Returns:
            _SequenceFetcher: Сам объект.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Выходит из контекстного менеджера.

        Args:
            *exc (object): Сведения об исключении. Не используются.

        Returns:
            None
        """
        return None

    def fetch(self, path: str) -> Observation:
        """Возвращает очередной подготовленный ответ.

        Args:
            path (str): Запрошенный путь. Не используется.

        Returns:
            Observation: Подготовленный результат.
        """
        html = type(self).pages[type(self).calls]
        type(self).calls += 1
        return Observation(
            status=200,
            final_url="https://funpay.com/chat/",
            html=html,
            elapsed_ms=10,
            redirects=0,
            content_length=len(html.encode("utf-8")),
        )


@pytest.fixture(autouse=True)
def _patch_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяет транспорт последовательностью ответов.

    Args:
        monkeypatch (pytest.MonkeyPatch): Инструмент подмены.

    Returns:
        None
    """
    _SequenceFetcher.calls = 0
    _SequenceFetcher.pages = [
        _page(NODE_BEFORE, CHAT_TAG_BEFORE),
        _page(NODE_AFTER, CHAT_TAG_AFTER),
    ]
    monkeypatch.setattr(observe_mod, "Fetcher", _SequenceFetcher)


def _provider() -> CallableSecretProvider:
    """Возвращает источник секрета для тестов.

    Returns:
        CallableSecretProvider: Источник, отдающий постоянное значение.
    """
    return CallableSecretProvider(lambda name: "x" * 32)


def _run(**kw: object) -> int:
    """Запускает сравнение без ожидания ввода.

    Args:
        **kw (object): Переопределения аргументов observe_compare.

    Returns:
        int: Код возврата.
    """
    args: dict[str, object] = {
        "path": "/chat/",
        "provider": _provider(),
        "wait": lambda prompt: "",
    }
    args.update(kw)
    return observe_mod.observe_compare(**args)  # type: ignore[arg-type]


def test_reports_growth_and_step(capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что рост счётчика виден вместе с величиной шага.

    Args:
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """
    assert _run() == 0
    out = capsys.readouterr().out
    assert "data-node-msg" in out
    assert "выросло на 4" in out


def test_non_numeric_change_has_no_step(capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что нечисловая метка меняется без направления.

    Args:
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """
    _run()
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "data-chat" in x)
    assert "изменилось" in line
    assert "на " not in line


def test_unchanged_value_is_not_listed(capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что неизменившееся значение не попадает в перечень.

    Args:
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """
    _run()
    out = capsys.readouterr().out
    assert "data-user-msg" not in out
    assert "без изменений:" in out


def test_output_contains_no_values(capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что ни одно наблюдаемое значение не попало в вывод.

    Args:
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """
    _run()
    out = capsys.readouterr().out
    for value in (NODE_BEFORE, NODE_AFTER, CHAT_TAG_BEFORE, CHAT_TAG_AFTER, DIALOG):
        assert value not in out, f"значение {value} попало в вывод"


def test_no_files_are_written(tmp_path: object, capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что режим не создаёт файлов.

    Args:
        tmp_path (object): Временный каталог. Используется как рабочий.
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """
    import os
    from pathlib import Path

    work = Path(str(tmp_path))
    old = Path.cwd()
    os.chdir(work)
    try:
        assert _run() == 0
    finally:
        os.chdir(old)
    assert list(work.iterdir()) == [], "режим сравнения не должен ничего записывать"


def test_unusable_first_read_stops_early(capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что непригодная страница прекращает сравнение.

    Сравнивать страницу входа со страницей чата бессмысленно: изменится всё, и
    вывод будет выглядеть как содержательный результат.

    Args:
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """
    _SequenceFetcher.pages = [
        '<html><body><button class="navbar-toggle navbar-toggle-guest"></button></body></html>',
        _page(NODE_AFTER, CHAT_TAG_AFTER),
    ]
    assert _run() == 2
    assert "непригодно" in capsys.readouterr().err


def test_missing_input_is_not_treated_as_success(capsys: pytest.CaptureFixture[str]) -> None:
    """Проверяет, что недоступный ввод прекращает работу с ошибкой.

    Args:
        capsys (pytest.CaptureFixture[str]): Перехват вывода.
    """

    def no_input(prompt: str) -> str:
        """Изображает недоступный ввод.

        Args:
            prompt (str): Приглашение. Не используется.

        Returns:
            str: Не возвращает, всегда возбуждает исключение.

        Raises:
            EOFError: Всегда.
        """
        raise EOFError

    assert _run(wait=no_input) == 1
