"""Проверки инструмента наблюдения.

Сеть здесь не трогается: транспорт подменяется. Проверяется то, ради чего
инструмент написан, - что на диск попадает скелет и описание происхождения,
а сырой HTML не попадает никуда.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from funora import _transport
from funora import observe as observe_mod
from funora._secret import CallableSecretProvider, Secret, SecretNotFoundError
from funora._transport import Observation, TransportSettings

CANARY_TEXT = "Иван Петров"
CANARY_ID = "98765"

PAGE = f"""
<html><body>
  <a class="user-link" href="/users/{CANARY_ID}/">{CANARY_TEXT}</a>
  <div class="order" data-id="{CANARY_ID}">Заказ {CANARY_ID}</div>
</body></html>
"""


class _FakeFetcher:
    """Подменяет транспорт заранее заданным ответом.

    Args:
        secret (Secret): Секрет. Не используется, принимается для совместимости.
        settings (TransportSettings | None): Настройки. Не используются.
    """

    html: str = PAGE
    status: int = 200
    final_url: str = "https://funpay.com/orders/trade"

    def __init__(self, secret: Secret, settings: TransportSettings | None = None) -> None:
        self._secret = secret

    def __enter__(self) -> _FakeFetcher:
        """Входит в контекстный менеджер.

        Returns:
            _FakeFetcher: Сам объект.
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
        """Возвращает заранее заданный ответ.

        Args:
            path (str): Запрошенный путь. Не используется.

        Returns:
            Observation: Подготовленный результат.
        """
        return Observation(
            status=self.status,
            final_url=self.final_url,
            html=self.html,
            elapsed_ms=12,
            redirects=0,
            content_length=len(self.html.encode("utf-8")),
        )


@pytest.fixture(autouse=True)
def _patch_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяет транспорт на всё время теста.

    Args:
        monkeypatch (pytest.MonkeyPatch): Инструмент подмены.

    Returns:
        None
    """
    _FakeFetcher.html = PAGE
    _FakeFetcher.status = 200
    _FakeFetcher.final_url = "https://funpay.com/orders/trade"
    monkeypatch.setattr(observe_mod, "Fetcher", _FakeFetcher)


def _provider() -> CallableSecretProvider:
    """Возвращает источник секрета для тестов.

    Returns:
        CallableSecretProvider: Источник, отдающий фиктивный ключ.
    """
    return CallableSecretProvider(lambda name: "test-key-value")


def test_writes_skeleton_and_provenance(tmp_path: Path) -> None:
    """Проверяет, что на диск попадают оба файла.

    Args:
        tmp_path (Path): Временный каталог pytest.
    """
    code = observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider())
    assert code == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["orders_trade.ru.provenance.json", "orders_trade.ru.skeleton.txt"]


def test_raw_html_never_written(tmp_path: Path) -> None:
    """Проверяет, что ни в одном записанном файле нет исходных данных.

    Это главная проверка инструмента. Сырой HTML со страницы под авторизацией
    не должен попадать на диск ни под каким именем.

    Args:
        tmp_path (Path): Временный каталог pytest.
    """
    observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider())
    for f in tmp_path.iterdir():
        content = f.read_text(encoding="utf-8")
        assert CANARY_TEXT not in content, f"имя пользователя найдено в {f.name}"
        assert CANARY_ID not in content, f"идентификатор найден в {f.name}"


def test_provenance_fields(tmp_path: Path) -> None:
    """Проверяет состав описания происхождения.

    Args:
        tmp_path (Path): Временный каталог pytest.
    """
    observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider(), locale="en")
    data = json.loads((tmp_path / "orders_trade.en.provenance.json").read_text(encoding="utf-8"))
    for key in (
        "path", "captured_at", "locale", "http_status", "classification",
        "classification_reason", "classification_provisional", "format",
    ):
        assert key in data, f"в описании нет поля {key}"
    assert data["locale"] == "en"
    assert data["format"] == "structural-skeleton-v1"


def test_login_page_returns_code_2(tmp_path: Path) -> None:
    """Проверяет, что страница входа даёт отдельный код возврата.

    Ноль означал бы, что страницу можно разбирать. Для страницы входа это
    неверно, и различать эти случаи должен именно код возврата.

    Args:
        tmp_path (Path): Временный каталог pytest.
    """
    _FakeFetcher.html = '<html><body><input type="password"></body></html>'
    code = observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider())
    assert code == 2
    data = json.loads((tmp_path / "orders_trade.ru.provenance.json").read_text(encoding="utf-8"))
    assert data["classification"] == "login_required"


def test_missing_secret_returns_code_1(tmp_path: Path) -> None:
    """Проверяет код возврата при недоступном секрете.

    Args:
        tmp_path (Path): Временный каталог pytest.
    """

    def boom(name: str) -> str:
        raise SecretNotFoundError("нет ключа")

    code = observe_mod.observe(
        path="/orders/trade", out_dir=tmp_path, provider=CallableSecretProvider(boom)
    )
    assert code == 1
    assert not list(tmp_path.iterdir()), "при отказе ничего писать на диск не нужно"


def test_cli_parses_arguments(tmp_path: Path) -> None:
    """Проверяет разбор аргументов командной строки.

    Args:
        tmp_path (Path): Временный каталог pytest.
    """
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "golden_key").write_text("value", encoding="utf-8")

    code = observe_mod.main([
        "/orders/trade",
        "--out", str(tmp_path / "out"),
        "--secret-file", str(secrets),
        "--locale", "ru",
    ])
    assert code == 0
    assert (tmp_path / "out" / "orders_trade.ru.skeleton.txt").exists()


def test_transport_warns_on_debug_logging(caplog: pytest.LogCaptureFixture) -> None:
    """Проверяет предупреждение при включённом отладочном журнале HTTP-стека.

    На уровне DEBUG httpx печатает заголовки запроса, среди которых Cookie с
    сессионным ключом. Молча работать в таком режиме нельзя.

    Args:
        caplog (pytest.LogCaptureFixture): Перехватчик журнала.
    """
    import logging

    _transport._warned = False
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.WARNING, logger="funora.transport"):
            _transport._warn_if_headers_logged()
        assert "DEBUG" in caplog.text
        assert "сессионный ключ" in caplog.text
    finally:
        logging.getLogger("httpx").setLevel(logging.NOTSET)
        _transport._warned = False
