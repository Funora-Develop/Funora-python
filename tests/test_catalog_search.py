"""Наблюдённый поиск: серверные совпадения, полнота, изоляция и отказы до сети."""

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from selectolax.parser import HTMLParser
from test_catalog import ROOT, WHEN, _page
from test_client import _FakeFetcher, _observation
from test_public_transport import _response
from test_reviews_pagination import Transport

from funora import AsyncClient, Client, Secret
from funora._budget import Budget
from funora._catalog import parse_catalog_search
from funora._engine import CATALOG_SEARCH_PATH, RUNNER_HEADERS, Submit
from funora._result import Completeness
from funora.capabilities import Capability
from funora.errors import (
    ConfigurationError,
    IncompleteResultError,
    ProtocolChangedError,
    ValidationError,
)

HTML = (Path(__file__).parent / "fixtures/pages/catalog-search.guest.ru.skeleton.txt").read_text()


def body(html=HTML):
    return json.dumps({"html": html})


def test_observed_search_preserves_every_group_and_server_match():
    page = parse_catalog_search(body(), " Minecraft ", WHEN)
    assert page.query == "minecraft"
    assert (page.cards_total, page.games_total, page.sections_total, page.letter_groups) == (
        4,
        4,
        31,
        2,
    )
    assert page.observed_at == WHEN
    assert page.completeness is Completeness.UNKNOWN
    assert page.reason == "search_total_unobserved"
    assert not page.defects
    with pytest.raises(IncompleteResultError):
        page.games()
    games = page.games(accept_incomplete=True)
    assert len(games) == 4
    assert all(game.sections[0].is_main for game in games)


def test_observed_empty_json_is_positive_evidence():
    page = parse_catalog_search('{"html":""}', "absent", WHEN)
    assert page.completeness is Completeness.COMPLETE
    assert page.reason == "search_no_matches"
    assert page.games() == ()
    assert (page.cards_total, page.games_total, page.sections_total, page.letter_groups) == (
        0,
        0,
        0,
        0,
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "<html>error</html>",
        "null",
        "[]",
        "{}",
        '{"html":null}',
        '{"html":123}',
        '{"html":[]}',
        '{"error":"denied","html":""}',
        '{"html":"x","html":""}',
        body(" "),
        body("<div>nothing</div>"),
        body('<div class="promo-games"></div>'),
        body("\ud800"),
        "[" * 1500 + "0" + "]" * 1500,
    ],
)
def test_unknown_envelope_or_markup_is_never_an_empty_success(raw):
    with pytest.raises(ProtocolChangedError):
        parse_catalog_search(raw, "game", WHEN)


def test_hidden_variant_uses_its_own_sections_regardless_of_order():
    tree = HTMLParser(HTML)
    card = tree.css_first(".promo-game-item")
    title = card.css_first(".game-title").html.replace('data-id="T2:d#1"', 'data-id="other"')
    title = title.replace('class="game-title"', 'class="game-title hidden"')
    sections = '<ul class="list-inline" data-id="other"><li><a href="https://funpay.com/chips/999/">coins</a></li></ul>'
    mutated = tree.html.replace(card.html, card.html[:-6] + title + sections + "</div>", 1)
    page = parse_catalog_search(body(mutated), "game", WHEN)
    games = page.games(accept_incomplete=True)
    variant = next(game for game in games if game.game_id.or_none() == "other")
    assert not variant.is_shown
    assert variant.sections[0].section_id.or_none() == "999"
    assert page.games_total == 5 and not page.defects


@pytest.mark.parametrize(
    "selector,attribute",
    [
        (".game-title", None),
        (".game-title", "data-id"),
        (".game-title a", "href"),
        (".game-title a", None),
        ("ul.list-inline", None),
        ("ul.list-inline li a", "href"),
    ],
)
def test_damaged_card_is_reported_without_losing_other_games(selector, attribute):
    tree = HTMLParser(HTML)
    node = tree.css_first(selector)
    if attribute:
        del node.attrs[attribute]
    else:
        node.decompose()
    page = parse_catalog_search(body(tree.html), "game", WHEN)
    assert page.completeness is Completeness.PARTIAL
    assert page.defects
    assert len(page.games(accept_incomplete=True)) >= 3
    with pytest.raises(IncompleteResultError):
        page.games()


def test_duplicate_game_is_partial_and_not_silently_deduplicated():
    tree = HTMLParser(HTML)
    card = tree.css_first(".promo-game-item")
    mutated = tree.html.replace(card.html, card.html * 2, 1)
    page = parse_catalog_search(body(mutated), "game", WHEN)
    assert page.games_total == 5
    assert page.completeness is Completeness.PARTIAL


@pytest.mark.parametrize(
    "query", [None, 123, "", "   ", "a" * 201, "bad\nquery", "x\x00", "\ud800", "x\x85"]
)
def test_invalid_query_never_spends_budget_or_calls_transport(query, monkeypatch):
    def forbidden_reserve(*args, **kwargs):
        pytest.fail("invalid query consumed request budget")

    monkeypatch.setattr(Budget, "reserve", forbidden_reserve)
    transport = Transport([])
    with (
        Client(public_only=True, public_transport=transport, budget=Budget(names=())) as client,
        pytest.raises(ValidationError),
    ):
        client.catalog.search(query)
    assert transport.calls == 0 and transport.forms == []


def test_search_does_not_replace_catalog_cache_or_verify_account():
    private = _FakeFetcher([_observation(_page(ROOT))])
    public = Transport([_observation(body())])
    with Client(transport=private, public_transport=public, budget=Budget(names=())) as client:
        cached = client.catalog.categories()
        page = client.catalog.search(" Minecraft ")
        assert page.games_total == 4 and page.query == "minecraft"
        assert client.catalog.categories() is cached and cached.query is None
        assert client._public_engine._state.catalog_cached is None
        assert not client._public_engine._state.session_ever_valid
        assert client.capability(Capability.CATALOG_SEARCH) == client._public_engine.capability(
            Capability.CATALOG_SEARCH
        )
    assert private.calls == public.calls == 1
    assert public.forms == [(CATALOG_SEARCH_PATH, {"query": "minecraft"}, RUNNER_HEADERS)]


def test_empty_result_requires_verified_transport_integrity():
    public = Transport([replace(_observation(body("")), declared_length=None)])
    with Client(public_only=True, public_transport=public, budget=Budget(names=())) as client:
        page = client.catalog.search("absent")
    assert page.completeness is not Completeness.COMPLETE
    with pytest.raises(IncompleteResultError):
        page.games()


@pytest.mark.parametrize(
    "io_request",
    [
        Submit("/lots/offerSave", {"query": "game"}, RUNNER_HEADERS),
        Submit(CATALOG_SEARCH_PATH + "?write=1", {"query": "game"}, RUNNER_HEADERS),
        Submit(CATALOG_SEARCH_PATH, {"query": "game", "csrf_token": "x"}, RUNNER_HEADERS),
        Submit(
            CATALOG_SEARCH_PATH, {"query": "game"}, {**RUNNER_HEADERS, "Cookie": "golden_key=x"}
        ),
        Submit(CATALOG_SEARCH_PATH, {"query": ""}, RUNNER_HEADERS),
        Submit(CATALOG_SEARCH_PATH, {"query": 123}, RUNNER_HEADERS),
    ],
)
def test_public_driver_still_rejects_other_forms(io_request):
    public = Transport([])

    def core():
        yield io_request

    with (
        Client(public_only=True, public_transport=public, budget=Budget(names=())) as client,
        pytest.raises(ConfigurationError, match="только чтение"),
    ):
        client.run(core(), engine=client._public_engine)
    assert public.calls == 0


async def test_both_clients_use_real_public_post_without_account_cookie(monkeypatch):
    original_sync, original_async = httpx.Client, httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == CATALOG_SEARCH_PATH
        assert parse_qs(request.content.decode()) == {"query": ["minecraft"]}
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"
        assert "golden_key" not in request.headers.get("cookie", "")
        return _response(body())

    def sync_factory(**kwargs):
        return original_sync(**kwargs, transport=httpx.MockTransport(handler))

    def async_factory(**kwargs):
        return original_async(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("funora._transport.httpx.Client", sync_factory)
    monkeypatch.setattr("funora._transport.httpx.AsyncClient", async_factory)
    with Client(Secret("test-key"), budget=Budget(names=())) as client:
        assert client.catalog.search(" Minecraft ").games_total == 4
    async with AsyncClient(Secret("test-key"), budget=Budget(names=())) as client:
        assert (await client.catalog.search(" Minecraft ")).games_total == 4
    assert len(requests) == 2


async def test_async_public_only_validation_and_guard():
    from test_aclient import _AsyncFakeFetcher

    class Public(_AsyncFakeFetcher):
        async def submit(self, path, fields, headers):
            return await self.fetch(path)

    transport = Public([_observation(body(""))])
    async with AsyncClient(
        public_only=True, public_transport=transport, budget=Budget(names=())
    ) as client:
        with pytest.raises(ValidationError):
            await client.catalog.search("")
        assert (await client.catalog.search("absent")).games() == ()

        def forbidden():
            yield Submit("/lots/offerSave", {}, {})

        with pytest.raises(ConfigurationError):
            await client.run(forbidden(), engine=client._public_engine)
    assert transport.calls == 1


def test_duplicate_section_list_is_ambiguous_instead_of_last_wins():
    tree = HTMLParser(HTML)
    sections = tree.css_first("ul.list-inline")
    mutated = tree.html.replace(sections.html, sections.html * 2, 1)
    page = parse_catalog_search(body(mutated), "game", WHEN)
    assert page.completeness is Completeness.PARTIAL
    assert page.games(accept_incomplete=True)[0].sections == ()
    assert "section_list_not_paired" in {defect.code for defect in page.defects}
