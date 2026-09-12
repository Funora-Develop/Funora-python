"""Граничные значения бюджета, денег, файлов и сетевых метаданных."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from funora import _fileio, _transport
from funora._budget import Budget, TokenBucket
from funora._identity import IdentityRegistry
from funora._money import Money
from funora._proxies import Proxy, ProxyPool
from funora._secret import FileSecretProvider, Secret, SecretNotFoundError
from funora.bot import SendCommand, Spool
from funora.budget import BUCKETS, MAX_WAIT_MS
from funora.errors import ConfigurationError, ValidationError


@pytest.mark.parametrize("factor", [-1, 0, 1.01])
def test_invalid_capacity_factor_never_changes_available_budget(factor):
    bucket = TokenBucket(BUCKETS["account"])
    before = (bucket.tokens, bucket.factor, bucket.allowance)
    with pytest.raises(ValueError):
        bucket.scale(factor)
    assert (bucket.tokens, bucket.factor, bucket.allowance) == before
    budget = Budget()
    with pytest.raises(ValueError):
        budget.scale(factor)
    assert budget.reserve(0).granted


@pytest.mark.parametrize("account", ["", " ", None, 1])
def test_invalid_account_cannot_create_a_budget(account):
    with pytest.raises(ValueError):
        Budget().for_account(account)


def test_empty_non_refilling_bucket_never_grants_after_waiting():
    bucket = TokenBucket(replace(BUCKETS["account"], refill_per_second=0), tokens=0)
    assert bucket.wait_for(0) == MAX_WAIT_MS
    assert bucket.wait_for(1_000_000) == MAX_WAIT_MS
    assert bucket.tokens == 0


def test_money_comparisons_preserve_currency_and_precision():
    left, right = Money(-1, "RUB"), Money(2, "RUB")
    assert left < right and left <= right and left <= left
    assert not right < left and not right <= left
    for other in (Money(2, "USD"), Money(2, "RUB", 0)):
        with pytest.raises(ValidationError):
            assert left < other
        with pytest.raises(ValidationError):
            assert left <= other
    assert str(Money(-3, "JPY", 0)) == "-3 JPY"


def test_proxy_binding_and_success_do_not_change_an_healthy_identity():
    registry = IdentityRegistry()
    proxies = (Proxy("synthetic", "https://proxy.example:8080"),)
    pool = ProxyPool(proxies, host="funpay.com", registry=registry)
    assert pool.proxies == proxies
    assert pool.bound_to("a") is None
    name, url = pool.choose("a", now=0)
    assert url == proxies[0].url
    assert pool.bound_to("a") == name
    assert registry.names() == (name,)
    pool.note_success(name)
    assert registry.get(name).capacity_factor == 1
    assert pool.bound_to("a") == name


def test_missing_secret_stays_typed_when_directory_listing_is_denied(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError("synthetic denial")

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(SecretNotFoundError, match="файл секрета не найден"):
        FileSecretProvider(tmp_path).get("golden_key")
    secret = Secret("do-not-print", label="fixture-key")
    assert secret.label == "fixture-key"
    assert "do-not-print" not in repr(secret)


@pytest.mark.parametrize("raw,expected", [(" 3 ", 3000), ("bad", None), ("-1", None)])
def test_retry_after_seconds_are_converted_without_guessing(raw, expected):
    response = httpx.Response(429, headers={"retry-after": raw})
    assert _transport._retry_after_ms(response) == expected


def test_empty_locale_contract_cannot_build_a_transport(monkeypatch):
    monkeypatch.setattr(_transport, "SUPPORTED_LOCALES", ())
    with pytest.raises(ConfigurationError, match="локалей пуст"):
        _transport.Fetcher(None)


@pytest.mark.parametrize("blocking", [False, True])
def test_windows_lock_owns_one_byte_and_releases_it_on_failure(tmp_path, monkeypatch, blocking):
    actions = []

    def locking(descriptor, mode, size):
        actions.append((mode, size, os.lseek(descriptor, 0, os.SEEK_CUR)))

    fake = SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(_fileio, "sys", SimpleNamespace(platform="win32"))
    path = tmp_path / "state.lock"
    for _ in range(2):
        with pytest.raises(RuntimeError), _fileio.file_lock(path, blocking=blocking):
            assert path.read_bytes() == b"\0"
            raise RuntimeError("synthetic failure inside lock")
    assert actions == [(1 if blocking else 2, 1, 0), (3, 1, 0)] * 2


def test_failed_queue_claim_does_not_discard_other_commands(tmp_path, monkeypatch):
    spool = Spool(tmp_path)
    for key in ("a", "b"):
        spool.submit(SendCommand("123", "synthetic", key))
    original = os.replace
    failed_path = next(
        path
        for path in (tmp_path / "ready").iterdir()
        if json.loads(path.read_text())["idempotency_key"] == "a"
    )

    def fail_one(source, destination):
        if Path(source) == failed_path:
            raise PermissionError("synthetic sharing violation")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_one)
        entries = spool.take(2)
    assert [entry.command.idempotency_key for entry in entries] == ["b"]
    assert [entry.command.idempotency_key for entry in spool.take(2)] == ["a"]
