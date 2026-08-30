"""Проверки инструмента наблюдения.

Сеть здесь не трогается: транспорт подменяется. Проверяется то, ради чего
инструмент написан, - что на диск попадает скелет и описание происхождения,
а сырой HTML не попадает никуда.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from funora import _transport
from funora import observe as observe_mod
from funora._secret import CallableSecretProvider, Secret, SecretNotFoundError
from funora._skeleton import SKELETON_FORMAT
from funora._transport import Observation, TransportSettings

CANARY_TEXT = "Иван Петров"
CANARY_ID = "98765"

PAGE = f"""
<html><body>
  <button class="navbar-toggle navbar-toggle-logged"></button>
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


def test_a_different_snapshot_is_never_overwritten(tmp_path: Path) -> None:
    """Требует НЕ затирать чужой снимок, лежащий под тем же именем.

    Имя строится по пути с обезличенными сегментами, и потому у РАЗНЫХ страниц
    оно совпадает: /users/111/ и /users/222/ обе дают users_n.

    Снимок невосполним. Он снят в конкретную минуту, при конкретном состоянии
    аккаунта, и второй раз таким же не будет: заказы закрываются, диалоги
    уходят вниз, лоты правятся. Затирание молча уничтожало бы свидетельство, на
    котором стоят правила извлечения.

    Args:
        tmp_path (Path): Временный каталог pytest.

    Returns:
        None
    """
    target = tmp_path / "orders_trade.ru.skeleton.txt"
    target.write_text("<html>совсем другой снимок</html>", encoding="utf-8")

    code = observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider())

    assert code == 1, "снимок затёрт: наблюдение уничтожено молча"
    assert target.read_text(encoding="utf-8") == "<html>совсем другой снимок</html>"


def test_the_same_snapshot_is_rewritten_without_complaint(tmp_path: Path) -> None:
    """Требует, чтобы повторная съёмка ТОЙ ЖЕ страницы проходила.

    Защита, отвергающая всякую повторную съёмку, сделала бы инструмент
    одноразовым: пересниять страницу после правки формата скелета - обычное
    дело.

    Args:
        tmp_path (Path): Временный каталог pytest.

    Returns:
        None
    """
    assert observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider()) == 0
    assert observe_mod.observe(path="/orders/trade", out_dir=tmp_path, provider=_provider()) == 0


def test_two_different_pages_collide_by_name(tmp_path: Path) -> None:
    """Закрепляет САМУ причину, ради которой защита заведена.

    Проверка не о защите, а о площадке и об имени: у двух разных профилей имя
    файла одно. Исчезни это свойство - защита стала бы лишней, и об этом надо
    узнать из падения проверки, а не из чтения кода.

    Args:
        tmp_path (Path): Временный каталог pytest.

    Returns:
        None
    """
    assert observe_mod._stem_for("/users/111/") == observe_mod._stem_for("/users/222/")
    assert observe_mod._stem_for("/users/111/") == "users_n"


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
        "path",
        "final_url",
        "captured_at",
        "locale",
        "http_status",
        "classification",
        "classification_reason",
        "classification_provisional",
        "format",
    ):
        assert key in data, f"в описании нет поля {key}"
    assert data["locale"] == "en"
    assert data["format"] == SKELETON_FORMAT


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
    secrets.mkdir(parents=True)
    key = secrets / "golden_key"
    key.write_text("value", encoding="utf-8")
    # На POSIX источник отвергает файл, доступный посторонним, и это правильно.
    # pytest создаёт временные файлы с правами 0644, поэтому права выставляются явно.
    if os.name == "posix":
        key.chmod(0o600)

    code = observe_mod.main(
        [
            "/orders/trade",
            "--out",
            str(tmp_path / "out"),
            "--secret-file",
            str(secrets),
            "--locale",
            "ru",
        ]
    )
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


def test_stem_keeps_no_identifiers() -> None:
    """Проверяет, что идентификаторы не попадают в имя файла.

    Имена файлов видны в списке репозитория, в истории и в результатах поиска.
    Идентификатору переписки там делать нечего, а на Windows путь с
    вопросительным знаком вообще не сохранился бы.
    """
    assert observe_mod._stem_for("/orders/trade") == "orders_trade"
    assert observe_mod._stem_for("/users/98765/") == "users_n"

    chat = observe_mod._stem_for("/chat/?node=123456789")
    assert "123456789" not in chat
    assert chat.startswith("chat-")


def test_stem_separates_different_queries() -> None:
    """Проверяет, что снимки разных переписок не затирают друг друга.

    Без этого оба легли бы в файл chat.ru.skeleton.txt, и второй захват молча
    уничтожил бы первый.
    """
    one = observe_mod._stem_for("/chat/?node=111111111")
    two = observe_mod._stem_for("/chat/?node=222222222")
    assert one != two
    assert observe_mod._stem_for("/chat/?node=111111111") == one


def test_provenance_masks_path_and_url() -> None:
    """Проверяет, что описание происхождения не хранит идентификаторов.

    Описание лежит рядом с фикстурой в открытом репозитории, поэтому строка
    запроса и числовые сегменты пути обязаны быть обезличены и здесь тоже.
    """
    data = observe_mod.build_provenance(
        path="/chat/?node=123456789",
        observation=Observation(
            status=200,
            final_url="https://funpay.com/users/98765/?ref=777",
            html="<html></html>",
            elapsed_ms=1,
            redirects=0,
            content_length=13,
        ),
        verdict_cls="ok",
        verdict_reason="identity_confirmed",
        provisional=False,
        locale="ru",
    )
    blob = json.dumps(data, ensure_ascii=False)
    for secret in ("123456789", "98765", "777"):
        assert secret not in blob, f"идентификатор {secret} попал в описание"
    assert data["path"] == "/chat/?node={q}"


def _secrets_dir(tmp_path: Path) -> Path:
    """Готовит каталог с секретом для запуска командной строки.

    Args:
        tmp_path (Path): Временный каталог pytest.

    Returns:
        Path: Каталог с файлом секрета.
    """
    secrets = tmp_path / "secrets"
    secrets.mkdir(parents=True)
    key = secrets / "golden_key"
    key.write_text("value", encoding="utf-8")
    if os.name == "posix":
        key.chmod(0o600)
    return secrets


def test_several_paths_are_captured_in_one_run(tmp_path: Path) -> None:
    """Проверяет, что несколько страниц снимаются одним запуском.

    Снимок делает человек руками, и три команды вместо одной - три повода
    ошибиться и три разных момента наблюдения там, где нужен один.

    Args:
        tmp_path (Path): Временный каталог pytest.

    Returns:
        None
    """
    code = observe_mod.main(
        [
            "/orders/trade",
            "/chat/",
            "--out",
            str(tmp_path / "out"),
            "--secret-file",
            str(_secrets_dir(tmp_path)),
            "--locale",
            "ru",
        ]
    )
    assert code == 0
    assert (tmp_path / "out" / "orders_trade.ru.skeleton.txt").exists()
    assert (tmp_path / "out" / "chat.ru.skeleton.txt").exists()


def test_analysis_modes_refuse_several_paths(tmp_path: Path) -> None:
    """Проверяет, что разбор во времени не принимает несколько страниц.

    Эти режимы сравнивают страницу саму с собой, и вторая в таком сравнении не
    участвует. Промолчать значило бы разобрать первую и молча забыть остальные.

    Args:
        tmp_path (Path): Временный каталог pytest.

    Returns:
        None
    """
    for mode in ("--relations", "--compare"):
        code = observe_mod.main(
            [
                "/orders/trade",
                "/chat/",
                mode,
                "--secret-file",
                str(_secrets_dir(tmp_path / mode.strip("-"))),
            ]
        )
        assert code == 2, f"{mode} принял две страницы"
