"""CLI наблюдений читает выбранный путь и не сохраняет неудачные снимки."""

import runpy
import sys

import pytest
from test_observe import PAGE, _FakeFetcher, _provider

from funora import observe as observer
from funora._secret import CallableSecretProvider, SecretNotFoundError
from funora._skeleton import SkeletonError


@pytest.fixture
def offline(monkeypatch):
    class Fetcher(_FakeFetcher):
        html = PAGE
        status = 200
        calls = []

        def fetch(self, path):
            assert isinstance(path, str), "транспорт принимает путь, а не список аргументов CLI"
            self.calls.append(path)
            return super().fetch(path)

    monkeypatch.setattr(observer, "Fetcher", Fetcher)
    monkeypatch.setenv("FUNORA_GOLDEN_KEY", "synthetic-observe-secret")
    return Fetcher


@pytest.mark.parametrize("mode", ["--compare", "--relations"])
def test_cli_single_path_reaches_the_transport(offline, monkeypatch, tmp_path, mode):
    monkeypatch.chdir(tmp_path)
    # Сравнение без интерактивного терминала отменяется после первого чтения.
    expected = 1 if mode == "--compare" else 0
    assert observer.main(["/chat/", mode]) == expected
    assert offline.calls == ["/chat/"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["--compare", "--relations"])
def test_cli_refuses_multiple_paths_before_io(offline, mode):
    assert observer.main(["/chat/", "/orders/", mode]) == 2
    assert offline.calls == []


@pytest.mark.parametrize("mode", ["compare", "relations", "observe"])
def test_observation_transport_failures_do_not_expose_response_or_create_files(
    offline, monkeypatch, tmp_path, capsys, mode
):
    def fail(*args):
        raise RuntimeError("private-response-sentinel")

    monkeypatch.setattr(offline, "fetch", fail)
    function = getattr(observer, "observe" if mode == "observe" else "observe_" + mode)
    kwargs = {"out_dir": tmp_path} if mode == "observe" else {}
    assert function(path="/chat/", provider=_provider(), **kwargs) == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "private-response-sentinel" not in captured.out + captured.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["compare", "relations"])
def test_observation_modes_refuse_missing_credentials_without_io(offline, mode, capsys):
    def missing(name):
        raise SecretNotFoundError("missing synthetic key")

    assert (
        getattr(observer, "observe_" + mode)(
            path="/chat/", provider=CallableSecretProvider(missing)
        )
        == 1
    )
    assert not offline.calls
    assert "секрет недоступен" in capsys.readouterr().err


def test_relations_refuses_an_unusable_page(offline, capsys):
    offline.status = 403
    assert observer.observe_relations(path="/chat/", provider=_provider()) == 2
    assert "страница непригодна" in capsys.readouterr().err


def test_failed_skeleton_leaves_no_artifacts(offline, monkeypatch, tmp_path):
    def fail(html):
        raise SkeletonError("synthetic malformed document")

    monkeypatch.setattr(observer, "skeletonize", fail)
    assert observer.observe(path="/chat/", out_dir=tmp_path, provider=_provider()) == 1
    assert list(tmp_path.iterdir()) == []


def test_module_cli_reads_a_secret_file_and_writes_only_masked_artifacts(
    offline, monkeypatch, tmp_path
):
    secret = tmp_path / "key"
    secret.write_text("synthetic-observe-secret")
    secret.chmod(0o600)
    target = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        ["funora-observe", "/chat/", "--secret-file", str(secret), "--out", str(target)],
    )
    # run_module получает новый namespace: подменяем источник Fetcher перед импортом.
    monkeypatch.setattr("funora._transport.Fetcher", offline)
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as raised,
    ):
        runpy.run_module("funora.observe", run_name="__main__")
    assert raised.value.code == 0
    assert len(list(target.iterdir())) == 2
    assert all(
        "synthetic-observe-secret" not in path.read_text(encoding="utf-8")
        for path in target.iterdir()
    )


def test_compare_refuses_when_second_page_is_unusable(offline, monkeypatch, tmp_path):
    original = offline.fetch

    def fetch(self, path):
        if self.calls:
            self.status = 403
        return original(self, path)

    monkeypatch.setattr(offline, "fetch", fetch)
    monkeypatch.chdir(tmp_path)
    assert observer.observe_compare(path="/chat/", provider=_provider(), wait=lambda _: "") == 2
    assert offline.calls == ["/chat/", "/chat/"]
    assert list(tmp_path.iterdir()) == []
