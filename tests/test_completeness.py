"""Матрица проверяет реальные вызовы обоих фасадов, а не только имена."""

import copy
import inspect
import os
import sys
from pathlib import Path

import pytest
from test_outbound_restore import AsyncOffline, Offline, complete

from funora import AsyncClient, Budget, Client, Router
from funora._engine import Engine
from funora.capabilities import Capability
from funora.errors import ConfigurationError, NetworkError
from funora.events import EventType
from funora.operations import OPERATIONS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import completeness

MATRIX = completeness.load_matrix()


def spec_dir():
    raw = os.environ.get("FUNORA_SPEC_DIR")
    if not raw:
        pytest.skip("FUNORA_SPEC_DIR не задана")
    return Path(raw)


def test_matrix_and_published_document_match_the_contract():
    items = completeness.validate(MATRIX, spec_dir())
    assert completeness.REPORT_PATH.read_text(encoding="utf-8") == completeness.render(
        MATRIX, items
    )


def test_cli_requires_the_specification(monkeypatch):
    monkeypatch.delenv("FUNORA_SPEC_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["completeness.py", "--check"])
    with pytest.raises(SystemExit) as caught:
        completeness.main()
    assert caught.value.code == 2


def test_cli_check_never_repairs_a_stale_document(tmp_path, monkeypatch):
    spec = spec_dir()
    report = tmp_path / "completeness.md"
    report.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(completeness, "REPORT_PATH", report)
    monkeypatch.setattr(sys, "argv", ["completeness.py", "--spec-dir", str(spec), "--check"])
    with pytest.raises(SystemExit) as caught:
        completeness.main()
    assert caught.value.code == 2
    assert report.read_text(encoding="utf-8") == "stale"
    monkeypatch.setattr(sys, "argv", ["completeness.py", "--spec-dir", str(spec)])
    completeness.main()
    assert report.read_text(encoding="utf-8") == completeness.render(
        MATRIX, completeness.validate(MATRIX, spec)
    )


@pytest.mark.parametrize(
    "section,key",
    [
        ("operations", "chats.send_image"),
        ("backlog", "single_flight_reauth"),
        ("missing_events", "lot.stock_changed"),
    ],
)
@pytest.mark.parametrize("add", [False, True])
def test_missing_or_invented_matrix_rows_are_rejected(section, key, add):
    matrix = copy.deepcopy(MATRIX)
    if add:
        matrix[section]["invented"] = matrix[section][key]
    else:
        del matrix[section][key]
    with pytest.raises(ValueError, match="состав"):
        completeness.validate(matrix, spec_dir())


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("tests", [], "не названа"),
        ("tests", ["tests/test_absent.py::test_absent"], "отсутствует набор"),
        ("tests", ["tests/test_chips.py::test_absent"], "отсутствует проверка"),
        ("engine", "absent", "отсутствует метод Engine"),
        ("method", "absent", "отсутствует метод"),
        ("limits", ["absent"], "отсутствующее ограничение"),
    ],
)
def test_stale_evidence_is_rejected(field, value, message):
    matrix = copy.deepcopy(MATRIX)
    matrix["operations"]["chats.send_text"][field] = value
    with pytest.raises(ValueError, match=message):
        completeness.validate(matrix, spec_dir())


def test_async_method_cannot_silently_become_synchronous(monkeypatch):
    from funora._aclient import AsyncChatsService
    from funora._client import ChatsService

    monkeypatch.setattr(AsyncChatsService, "send_text", ChatsService.send_text)
    with pytest.raises(ValueError, match="модель выполнения"):
        completeness.validate(MATRIX, spec_dir())


def test_new_async_only_method_cannot_escape_the_matrix(monkeypatch):
    from funora._aclient import AsyncChatsService

    monkeypatch.setattr(AsyncChatsService, "unregistered", lambda self: None, raising=False)
    with pytest.raises(ValueError, match="вне матрицы"):
        completeness.validate(MATRIX, spec_dir())


@pytest.mark.parametrize("event", MATRIX["missing_events"])
def test_missing_event_is_refused_at_registration(event):
    with pytest.raises(ConfigurationError, match="не порождает"):
        Router().on(EventType(event))


def arguments(method, explicit):
    """Различимые значения ловят перестановку ID и потерю необязательного поля.

    Значения намеренно не являются данными площадки: проверяется граница
    фасад/ядро, валидацию и протокол проверяют опорные сценарии матрицы.
    """
    args, kwargs = [], {}
    signature = inspect.signature(method)
    for name, parameter in signature.parameters.items():
        if parameter.kind is parameter.VAR_POSITIONAL:
            args.extend((name + "-one", name + "-two"))
        elif parameter.default is parameter.empty or explicit:
            value = object()
            if parameter.kind is parameter.KEYWORD_ONLY:
                kwargs[name] = value
            else:
                args.append(value)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return args, kwargs, dict(bound.arguments)


async def check_route(monkeypatch, name, asynchronous, explicit, failure):
    row = MATRIX["operations"][name]
    public = AsyncOffline() if asynchronous else Offline()
    client = (AsyncClient if asynchronous else Client)(
        transport=AsyncOffline() if asynchronous else Offline(),
        public_transport=public,
        budget=Budget(names=()),
    )
    method = getattr(getattr(client, row["service"].removesuffix("Service").lower()), row["method"])
    args, kwargs, expected = arguments(method, explicit)
    if name in {"lots.activate", "lots.deactivate"}:
        expected["visible"] = name == "lots.activate"
    if name == "lots.calculate_prices":
        expected["game_id"] = None
    if name == "chips.calculate_prices":
        expected["node_id"] = None
    engine_signature = inspect.signature(getattr(Engine, row["engine"]))
    observed = []
    result = None if OPERATIONS[name].returns == "void" else object()
    error = NetworkError("synthetic boundary failure")

    def operation(engine, *args, **kwargs):
        bound = engine_signature.bind(engine, *args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        del values["self"]
        observed.append((engine, values))
        if failure:
            raise error
        yield from ()
        return result

    monkeypatch.setattr(Engine, row["engine"], operation)
    try:
        if failure:
            with pytest.raises(NetworkError) as caught:
                await complete(method(*args, **kwargs))
            assert caught.value is error
        else:
            assert await complete(method(*args, **kwargs)) is result
        engine = (
            client._public_engine
            if OPERATIONS[name].transport_lane == "public_read"
            else client.engine
        )
        assert observed == [(engine, expected)]
        assert client._public_fetcher is public
    finally:
        await complete(client.close())


@pytest.mark.parametrize("name", [name for name in MATRIX["operations"] if name != "capabilities"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("failure", [False, True])
async def test_facade_preserves_arguments_lane_result_and_error(
    monkeypatch, name, asynchronous, explicit, failure
):
    await check_route(monkeypatch, name, asynchronous, explicit, failure)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_capability_facade_combines_both_lanes_without_io(monkeypatch, asynchronous):
    original = Engine.capability_profile
    calls = []

    def record(engine):
        calls.append(engine)
        return original(engine)

    monkeypatch.setattr(Engine, "capability_profile", record)
    client = (AsyncClient if asynchronous else Client)(
        transport=AsyncOffline() if asynchronous else Offline(), budget=Budget(names=())
    )
    try:
        result = await complete(client.account.capabilities())
        assert calls == [client.engine, client._public_engine]
        assert set(result.evaluations()) == set(Capability)
        assert client._public_fetcher is None
    finally:
        await complete(client.close())


async def test_route_check_detects_an_ignored_optional_argument(monkeypatch):
    from funora._client import ChatsService

    original = ChatsService.send_text

    def broken(self, node_id, text, *, declared_cold=False):
        return original(self, node_id, text)  # потеря явно переданного declared_cold

    monkeypatch.setattr(ChatsService, "send_text", broken)
    with pytest.raises(AssertionError):
        await check_route(monkeypatch, "chats.send_text", False, True, False)
